"""Versioned recovery for exact M6 cache-open faults across shipped vehicle modes."""
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PureWindowsPath

from .data import input_fingerprints
from .runner import MODULES

CACHE_FILES = tuple(f'{prefix}_{mode}.dat' for prefix in ('Rwm', 'volume')
                    for mode in ('bus', 'car', 'motor', 'taxi', 'truck'))
MAX_RETRIES_PER_SCENE = 2
MAX_RETRIES_TOTAL = 20


def valid_scene_name(name):
    return isinstance(name, str) and name not in ('.', '..') and re.fullmatch(r'[A-Za-z0-9_.-]+', name) is not None


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def native_open_fault(job, name):
    """Classify only a failed M6 open of this scenario's own known cache file."""
    if not valid_scene_name(name):
        return None
    job = Path(job).resolve()
    scenario = job/'scenarios'/name/'基础路网'
    logs = job/'logs'/name
    manifest_path = logs/'run.json'
    if not manifest_path.is_file():
        return None
    manifest = read(manifest_path)
    modules = manifest.get('modules', [])
    if (manifest.get('status') != 'failed' or not modules
            or tuple(m.get('module') for m in modules) != MODULES[:4]
            or any(m.get('status') != 'completed' or m.get('returncode') != 0 for m in modules[:-1])
            or modules[-1].get('module') != 'M6AssignVehicle.exe'
            or modules[-1].get('status') != 'failed'
            or modules[-1].get('returncode') != 1):
        return None
    if manifest.get('scenario') != str(scenario):
        return None
    stream = logs/'M6AssignVehicle.exe.stdout.bin'
    if not stream.is_file():
        return None
    text = stream.read_bytes().decode('gb18030', errors='replace')
    if '[error]' not in text.lower() or '在打开以下数据文件或执行以下程序时出错' not in text:
        return None
    matches = [filename for filename in CACHE_FILES
               if f'{scenario.as_posix()}/cache/{filename}' in text]
    if len(matches) != 1:
        return None
    before = logs/'inputs_before.json'
    if not before.is_file() or input_fingerprints(scenario) != read(before):
        return None
    return dict(logical_name=name, cache_file=matches[0],
                stdout_sha256=sha(stream), input_before_sha256=sha(before),
                manifest_sha256=sha(manifest_path),
                failed_started=manifest['started'], failed_finished=manifest['finished'],
                failed_native_modules=len(modules),
                meaning='原生文件打开失败；原因未确定；尚无可供策略观察的完整结果')


def relocate_failure(job, name, attempt):
    """Move failed scenario and log directories within the same verified job."""
    job = Path(job).resolve()
    if not valid_scene_name(name):
        raise ValueError('失败证据场景名称越界')
    if type(attempt) is not int or not 1 <= attempt <= MAX_RETRIES_PER_SCENE:
        raise ValueError('单场景额外尝试次数超出预注册上限')
    base = job/'native_attempts'/name/f'attempt_{attempt}'
    pairs = ((job/'scenarios'/name, base/'scenario'),
             (job/'logs'/name, base/'logs'))
    for source, target in pairs:
        if (not source.resolve().is_relative_to(job)
                or not target.resolve().is_relative_to(job)
                or not source.is_dir() or target.exists()):
            raise ValueError('失败证据目录不完整、越界或目标已存在')
    base.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for source, target in pairs:
            source.rename(target)
            moved.append((source, target))
    except BaseException:
        for source, target in reversed(moved):
            target.rename(source)
        raise
    return {target.name: target.relative_to(job).as_posix() for _, target in moved}


def verify_retries(job, state, original_job=None):
    """Replay preserved native failures without treating them as policy results."""
    job = Path(job).resolve()
    original = PureWindowsPath(original_job or str(job))
    entries = state.get('native_retries', [])
    if not isinstance(entries, list) or len(entries) > MAX_RETRIES_TOTAL:
        raise ValueError('原生额外尝试总数超出预注册上限')
    by_name = {r['name']: r for r in state['runs']}
    numbers = defaultdict(list)
    expected_manifests = set()
    native_calls = 0
    for entry in entries:
        name, attempt = entry['logical_name'], entry['attempt']
        if (not valid_scene_name(name) or name not in by_name or type(attempt) is not int
                or not 1 <= attempt <= MAX_RETRIES_PER_SCENE):
            raise ValueError('失败尝试没有对应的完整物理场景或编号非法')
        numbers[name].append(attempt)
        base = job/'native_attempts'/name/f'attempt_{attempt}'
        if entry.get('locations') != {
                'scenario': (base/'scenario').relative_to(job).as_posix(),
                'logs': (base/'logs').relative_to(job).as_posix()}:
            raise ValueError('失败证据位置与登记不符')
        scenario = base/'scenario/基础路网'
        logs = base/'logs'
        for path in (scenario, logs):
            if not path.resolve().is_relative_to(job) or not path.is_dir():
                raise ValueError('失败场景或日志越出任务目录')
        manifest_path, input_path, stream = (logs/'run.json', logs/'inputs_before.json',
                                             logs/'M6AssignVehicle.exe.stdout.bin')
        expected_manifests.add(manifest_path.relative_to(job).as_posix())
        if (sha(manifest_path) != entry['manifest_sha256'] or
                sha(input_path) != entry['input_before_sha256'] or
                sha(stream) != entry['stdout_sha256']):
            raise ValueError('原生失败尝试字节摘要改变')
        manifest = read(manifest_path)
        expected_original = str(original/'scenarios'/name/'基础路网')
        if manifest['scenario'] != expected_original or manifest['status'] != 'failed':
            raise ValueError('失败日志原始场景路径或状态不符')
        modules = manifest['modules']
        if (tuple(m['module'] for m in modules) != MODULES[:4]
                or any(m['status'] != 'completed' or m['returncode'] != 0 for m in modules[:-1])
                or modules[-1]['status'] != 'failed' or modules[-1]['returncode'] != 1
                or len(modules) != entry['failed_native_modules']):
            raise ValueError('失败尝试不是预定M6缓存打开故障')
        stdout = stream.read_bytes().decode('gb18030', errors='replace')
        cache = entry['cache_file']
        if (cache not in CACHE_FILES or '[error]' not in stdout.lower()
                or '在打开以下数据文件或执行以下程序时出错' not in stdout
                or f'{PureWindowsPath(expected_original).as_posix()}/cache/{cache}' not in stdout):
            raise ValueError('原生错误消息与登记文件不一致')
        same_inputs = read(input_path)
        if (input_fingerprints(scenario) != same_inputs or
                same_inputs != read(job/'logs'/name/'inputs_before.json')):
            raise ValueError('重试未保持执行前输入一致')
        if (manifest['started'] != entry['failed_started'] or
                manifest['finished'] != entry['failed_finished']):
            raise ValueError('原生失败尝试时间记录改变')
        native_calls += len(modules)
    actual_manifests = {path.relative_to(job).as_posix()
                        for path in (job/'native_attempts').glob('*/attempt_*/logs/run.json')}
    if actual_manifests != expected_manifests:
        raise ValueError('失败尝试目录与登记不一致')
    for name, sequence in numbers.items():
        if sequence != list(range(1, len(sequence)+1)):
            raise ValueError('单场景失败尝试编号不连续')
        attempt_times = []
        for attempt in sequence:
            manifest = read(job/'native_attempts'/name/f'attempt_{attempt}'/'logs/run.json')
            attempt_times.append((datetime.fromisoformat(manifest['started']).timestamp(),
                                  datetime.fromisoformat(manifest['finished']).timestamp()))
        successful_start = datetime.fromisoformat(by_name[name]['execution']['started']).timestamp()
        prior = state['runs'][[r['name'] for r in state['runs']].index(name)-1] if name != 'baseline' else None
        prior_finish = datetime.fromisoformat(prior['execution']['finished']).timestamp() if prior else float('-inf')
        if (prior_finish > attempt_times[0][0] or any(start > finish for start, finish in attempt_times) or any(
                left[1] > right[0] for left, right in zip(attempt_times, attempt_times[1:]))
                or attempt_times[-1][1] > successful_start):
            raise ValueError('失败重试与最终成功场景时间顺序不符')
    return dict(failed_attempts=len(entries), failed_native_module_calls=native_calls,
                affected_scenes=len(numbers), all_inputs_exact=True)
