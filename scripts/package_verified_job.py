"""Pack a sealed TranStar job, excluding regenerated runtime executables."""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_experiment import verify


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package(job, output):
    job, output = job.resolve(), output.resolve()
    if output.exists() or output.with_suffix(output.suffix+'.tmp').exists():
        raise ValueError('目标档案或临时文件已存在，不覆盖')
    audit = verify(job, archived=True, write=False)
    archive = json.loads((job/'archive_manifest.json').read_text(encoding='utf-8'))
    files = [p for p in sorted(job.rglob('*')) if p.is_file() and p.relative_to(job).parts[0] != 'runtime']
    required = set(archive['files']) | {'archive_manifest.json', 'portable_verification.json'}
    if not required <= {p.relative_to(job).as_posix() for p in files}:
        raise ValueError('封存清单或核验摘要缺失')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix+'.tmp')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,
                         allowZip64=True) as bundle:
        for path in files:
            bundle.write(path,arcname=(Path(job.name)/path.relative_to(job)).as_posix())
    with zipfile.ZipFile(temporary) as bundle:
        bad = bundle.testzip()
        if bad:
            raise ValueError(f'ZIP内文件CRC检查失败: {bad}')
    temporary.replace(output)
    return dict(job=job.name,archive=output.name,zip_bytes=output.stat().st_size,
                zip_sha256=sha(output),source_archive_sha256=sha(job/'archive_manifest.json'),
                files=len(files),completed_scenarios=audit['completed_scenarios'],
                module_executions=audit['module_executions'],verification_passed=audit['passed'],
                excluded='智能体实验/jobs/<任务>/runtime/为运行时重建副本，未收入ZIP；主程序运行包另行提供',
                meaning='可移植的真实任务输入、输出、日志与校验清单；ZIP和SHA不是数字签名')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=package(args.job,args.output)
    (args.output.parent/'package.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',
                                                   encoding='utf-8',newline='\n')
    print(json.dumps(result,ensure_ascii=False))
