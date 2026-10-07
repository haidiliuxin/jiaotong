"""Extract a published job ZIP elsewhere and audit its preserved native results."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_experiment import verify


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check(archive: Path, output: Path) -> dict:
    archive, output = archive.resolve(), output.resolve()
    if output.exists():
        raise ValueError("复核记录已存在，不覆盖")
    package = json.loads((archive.parent / "package.json").read_text(encoding="utf-8"))
    if package["archive"] != archive.name or package["zip_sha256"] != sha(archive):
        raise ValueError("ZIP与发布打包记录不符")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or len(names) != package["files"]:
            raise ValueError("ZIP文件数或唯一性不符")
        job_name = package["job"]
        for name in names:
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != job_name or ".." in parts
                    or name.startswith("/") or "\\" in name):
                raise ValueError(f"ZIP成员路径异常: {name}")
        if bundle.testzip():
            raise ValueError("ZIP成员CRC校验失败")
        parent = archive.parent.resolve()
        temp = Path(tempfile.mkdtemp(prefix="job_extract_check_", dir=parent)).resolve()
        if not temp.is_relative_to(parent):
            raise ValueError("临时解压路径越界")
        try:
            bundle.extractall(temp)
            extracted = temp / job_name
            report = verify(extracted, archived=True, write=False)
            if (report["completed_scenarios"] != package["completed_scenarios"]
                    or report["module_executions"] != package["module_executions"]
                    or sha(extracted / "archive_manifest.json") != package["source_archive_sha256"]):
                raise ValueError("解压档案与原任务计数或封存摘要不符")
            result = dict(status="passed", archive=archive.name,
                          archive_sha256=package["zip_sha256"], extracted_job=job_name,
                          files=len(names), completed_scenarios=report["completed_scenarios"],
                          module_executions=report["module_executions"],
                          repeat_arrays_exact=report["repeat_arrays_exact"],
                          portable_archive_verified=report["portable_archive_verified"],
                          new_model_calls=0,
                          scope="同机独立目录解压；只读重算原始输入、输出、约束和搜索决策")
        finally:
            if temp.is_relative_to(parent) and temp.name.startswith("job_extract_check_"):
                shutil.rmtree(temp)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.archive, args.output), ensure_ascii=False))
