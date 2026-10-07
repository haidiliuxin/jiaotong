"""Copy small, immutable evidence for the aborted native run and exact-input probe."""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path


FIXED_FILES = (
    ('failed/state.json', 'state.json'),
    ('failed/protocol.json', 'protocol.json'),
    ('failed/execution_context.json', 'execution_context.json'),
    ('failed/source_inputs.json', 'source_inputs.json'),
    ('probe/plan.json', 'plan.json'),
    ('probe/input_check.json', 'input_check.json'),
    ('probe/result.json', 'result.json'),
    ('probe/logs/replay/run.json', 'logs/replay/run.json'),
    ('probe/logs/replay/M6AssignVehicle.exe.stdout.bin',
     'logs/replay/M6AssignVehicle.exe.stdout.bin'),
)


def files_for(physical):
    if re.fullmatch(r'case_\d{3}', physical) is None:
        raise ValueError('物理场景名称非法')
    failed_logs = tuple((f'failed/logs/{physical}/{name}', f'logs/{physical}/{name}') for name in (
        'run.json', 'inputs_before.json',
        'M6AssignVehicle.exe.stdout.bin', 'M6AssignVehicle.exe.stdout.txt'))
    return FIXED_FILES + failed_logs


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export(failed, probe, output, physical='case_002'):
    failed, probe, output = (p.resolve() for p in (failed, probe, output))
    selected_files = files_for(physical)
    if output.exists():
        raise FileExistsError('故障证据目录已存在，不覆盖')
    state = json.loads((failed/'state.json').read_text(encoding='utf-8'))
    diagnostic = json.loads((probe/'result.json').read_text(encoding='utf-8'))
    check = json.loads((probe/'input_check.json').read_text(encoding='utf-8'))
    if state['status'] != 'failed' or diagnostic['status'] != 'completed' or check['exact_match'] is not True:
        raise ValueError('失败任务或同输入重跑尚未达到可导出状态')
    native = json.loads((failed/'logs'/physical/'run.json').read_text(encoding='utf-8'))
    if (native['status'] != 'failed' or native['modules'][-1]['module'] != 'M6AssignVehicle.exe'
            or physical not in state['error'] or diagnostic['native_modules'] != 7):
        raise ValueError('指定物理场景不是当前原生分配故障')
    output.mkdir(parents=True, exist_ok=False)
    copied = {}
    for target_name, source_name in selected_files:
        root = failed if target_name.startswith('failed/') else probe
        source = root/source_name
        destination = output/'evidence'/target_name
        if not source.is_file():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied[target_name] = dict(source_sha256=digest(source),
                                   copied_sha256=digest(destination),
                                   bytes=destination.stat().st_size)
        if copied[target_name]['source_sha256'] != copied[target_name]['copied_sha256']:
            raise ValueError(f'证据复制不一致：{target_name}')
    manifest = dict(kind='aborted_native_run_and_exact_input_probe_v1',
                    failed_job=failed.name, physical=physical, probe=probe.name,
                    source_paths=dict(failed=str(failed), probe=str(probe)),
                    input_exact=True, probe_native_modules=diagnostic['native_modules'],
                    probe_rows=diagnostic['row_count'],
                    meaning='故障诊断副本及字节摘要；原失败任务保持原状；不等于正式挑战通过',
                    files=copied)
    (output/'manifest.json').write_bytes(json.dumps(manifest, ensure_ascii=False,
                                                  indent=2).encode('utf-8'))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('failed', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--physical', default='case_002')
    args = parser.parse_args()
    result = export(args.failed, args.probe, args.output, args.physical)
    print(json.dumps(dict(failed_job=result['failed_job'], probe=result['probe'],
                          evidence_files=len(result['files'])), ensure_ascii=False))
