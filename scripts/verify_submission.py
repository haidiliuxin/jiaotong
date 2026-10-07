"""Check reading-package bytes without installing report-generation dependencies."""
import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.delivery import verify_package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='检查离线阅读目录或ZIP，不运行仿真模型')
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        print(json.dumps(verify_package(args.package), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(2, f'阅读包核验失败：{exc}\n')
