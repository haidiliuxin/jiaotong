"""Bind review documents to verified evidence and check portable reading packages.

These checks do not run TranStar and do not replace the raw experiment auditor.
"""
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED = {'研究报告.pdf', '操作手册.pdf', '事实摘要.json', '证据索引.json', 'README.md'}
MANIFEST = 'package_manifest.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_bound_study(job):
    job = Path(job)
    raw = {name: (job/f'{name}.json').read_bytes() for name in ('state', 'protocol', 'selection')}
    verification = json.loads((job/'verification.json').read_text(encoding='utf-8'))
    if verification.get('passed') is not True:
        raise ValueError('研究未通过独立核验')
    for name, data in raw.items():
        if verification.get(f'{name}_sha256') != digest(data):
            raise ValueError(f'核验已过期：{name}.json 与已核验内容不同')
    state, protocol, selection = (json.loads(raw[n]) for n in ('state', 'protocol', 'selection'))
    if state.get('status') != 'completed' or state.get('kind') != 'prospective_region_study':
        raise ValueError('只允许导出已完成的前瞻研究')
    if state.get('repeatability', {}).get('exact_match') is not True:
        raise ValueError('基准复跑尚未通过')
    return state, protocol, selection, verification


def test_count(log):
    text = Path(log).read_text(encoding='utf-8-sig')
    matches = re.findall(r'^Ran (\d+) tests? in .+$', text, re.M)
    if len(matches) != 1 or text.strip().splitlines()[-1] != 'OK':
        raise ValueError('测试记录不是单次完整通过的 unittest 输出')
    if re.search(r'^(FAILED|ERROR:|FAIL:)', text, re.M):
        raise ValueError('测试记录含失败')
    return int(matches[0])


def safe_name(name):
    p = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or p.is_absolute() or
            any(x in ('', '.', '..') for x in name.split('/'))):
        raise ValueError(f'非法包内路径：{name}')
    return p.as_posix()


def _validate(manifest, names, read):
    if manifest.get('kind') != 'offline_review_package' or manifest.get('includes_full_model_database') is not False:
        raise ValueError('阅读包类型或数据范围声明不符')
    files = manifest['files']
    for name in files:
        safe_name(name)
    if not REQUIRED.issubset(files):
        raise ValueError('缺少必需阅读材料')
    if set(names) != set(files) | {MANIFEST}:
        raise ValueError('文件清单存在遗漏或额外文件')
    for name, expected in files.items():
        data = read(name)
        if len(data) != expected['bytes'] or digest(data) != expected['sha256']:
            raise ValueError(f'内容摘要不符：{name}')
        if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise ValueError(f'仍是 Git LFS 指针：{name}')
    return dict(passed=True, files=len(files), kind=manifest['kind'],
                new_model_calls=0, includes_full_model_database=False,
                meaning='阅读材料传输一致性检查；不是数字签名、原始实验独立核验或重新求解')


def verify_package(path):
    path = Path(path)
    if path.is_dir():
        root = path.resolve()
        names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        for name in names:
            safe_name(name)
            if not (root/name).resolve().is_relative_to(root):
                raise ValueError('阅读包文件链接越界')
        read = lambda name: (root/name).read_bytes()
        return _validate(json.loads(read(MANIFEST)), names, read)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('压缩包存在重复文件名')
        for name in names:
            safe_name(name)
        return _validate(json.loads(z.read(MANIFEST)), names, z.read)


def seal_package(output, metadata):
    output = Path(output)
    if (output/MANIFEST).exists():
        raise ValueError('已封存的阅读包不可覆盖；请使用新输出目录')
    files = {}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            name = safe_name(path.relative_to(output).as_posix())
            data = path.read_bytes()
            files[name] = dict(bytes=len(data), sha256=digest(data))
    manifest = dict(metadata, kind='offline_review_package', includes_full_model_database=False, files=files)
    (output/MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return verify_package(output)
