"""Portable copies of a locally verified experiment; no claim of signed provenance."""
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath

from .data import save_json

MANIFEST = 'archive_manifest.json'
EXCLUDED = {MANIFEST, 'portable_verification.json'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_archive(job):
    """Called only after the strict verifier has successfully re-read the job."""
    files = {}
    for path in sorted(job.rglob('*')):
        relative = path.relative_to(job)
        if not path.is_file() or relative.parts[0] == 'runtime' or relative.as_posix() in EXCLUDED:
            continue
        stamp = path.stat()
        files[relative.as_posix()] = dict(bytes=stamp.st_size, mtime_ns=stamp.st_mtime_ns, sha256=digest(path))
    if 'verification.json' not in files or 'state.json' not in files:
        raise ValueError('缺少严格核验记录和任务状态')
    archive = dict(version=1, original_job=str(job.resolve()), files=files,
                   meaning='原运行目录严格核验后保存的字节及时间戳清单；可检查传输一致性，不是数字签名或独立执行证明')
    save_json(job/MANIFEST, archive)
    return archive


def verify_archive(job):
    import json
    archive = json.loads((job/MANIFEST).read_text(encoding='utf-8'))
    if archive.get('version') != 1 or not PureWindowsPath(archive['original_job']).is_absolute():
        raise ValueError('不支持的档案版本或原运行位置')
    for name, expected in archive['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('档案路径越出任务目录')
        path = job.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(job.resolve()):
            raise ValueError('档案路径越出任务目录')
        if not path.is_file() or path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
            raise ValueError(f'档案内容缺失或改变: {name}')
    if not {'verification.json', 'state.json'} <= archive['files'].keys():
        raise ValueError('档案缺少严格核验和任务状态')
    return archive


def original_scenario(archive, relative):
    return str(PureWindowsPath(archive['original_job']) / PureWindowsPath(relative))
