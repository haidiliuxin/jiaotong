"""Pack every sealed job byte once, with a path-to-SHA map for lossless restore."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.frozen_study_audit import verify_frozen_study
from verify_experiment import verify


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pack(job, output, *, study=False):
    job, output = job.resolve(), output.resolve()
    if output.exists() or output.with_suffix(output.suffix + '.tmp').exists():
        raise ValueError('目标去重包或临时文件已存在，不覆盖')
    audit = verify_frozen_study(job) if study else verify(job, archived=True, write=False)
    archived = json.loads((job / 'archive_manifest.json').read_text(encoding='utf-8'))
    if study:
        portable = job / 'portable_verification.json'
        if portable.exists():
            if json.loads(portable.read_text(encoding='utf-8')) != audit:
                raise ValueError('已有研究便携核验与封存代码重算不一致')
        else:
            portable.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    files = [p for p in sorted(job.rglob('*')) if p.is_file() and p.relative_to(job).parts[0] != 'runtime'
             and (not study or ('__pycache__' not in p.relative_to(job).parts and p.suffix != '.pyc'))]
    expected = set(archived['files']) | {'archive_manifest.json', 'portable_verification.json'}
    if expected != {p.relative_to(job).as_posix() for p in files}:
        raise ValueError('任务文件不等于原封存清单及其两份附加核验记录')
    mappings, objects = {}, {}
    for path in files:
        rel = path.relative_to(job).as_posix()
        hash_value = sha(path)
        if rel in archived['files'] and hash_value != archived['files'][rel]['sha256']:
            raise ValueError(f'封存后文件改变: {rel}')
        mappings[rel] = dict(sha256=hash_value, bytes=path.stat().st_size)
        objects.setdefault(hash_value, path)
    manifest = dict(format='sealed-job-content-addressed-v1', job=job.name,
                    source_archive_sha256=sha(job / 'archive_manifest.json'),
                    files=mappings)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as bundle:
        bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False,
                                                   sort_keys=True, separators=(',', ':')).encode('utf-8'))
        for hash_value, path in sorted(objects.items()):
            bundle.write(path, arcname='objects/' + hash_value)
    with zipfile.ZipFile(temporary) as bundle:
        bad = bundle.testzip()
        if bad:
            raise ValueError(f'去重包ZIP CRC错误: {bad}')
    temporary.replace(output)
    return dict(format=manifest['format'], audit_mode='frozen_region_study' if study else 'agent',
                job=job.name, archive=output.name,
                zip_bytes=output.stat().st_size, zip_sha256=sha(output),
                source_archive_sha256=manifest['source_archive_sha256'],
                files=len(files), unique_objects=len(objects), duplicates_elided=len(files) - len(objects),
                completed_scenarios=audit['physical_scenes' if study else 'completed_scenarios'],
                module_executions=audit['native_module_calls' if study else 'module_executions'],
                verification_passed=audit['passed'],
                restoration='按manifest.json的路径映射复原所有文件；每个object以SHA-256核对；再执行严格封存核验',
                excluded=('挑战任务没有运行时程序副本；仅排除封存后核验生成的Python字节码缓存'
                          if study else '智能体实验/jobs/<任务>/runtime/为运行时重建副本，未收入任务包'),
                meaning='无损内容寻址任务包；SHA用于传输一致性检查，不是数字签名')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--study', action='store_true', help='使用任务封存的三地区研究代码严格审计')
    args = parser.parse_args()
    result = pack(args.job, args.output, study=args.study)
    (args.output.parent / 'package.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
