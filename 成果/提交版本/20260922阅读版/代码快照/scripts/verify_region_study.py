import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.archive import write_archive
from traffic_agent.study_audit import verify


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='核验前瞻三地区真实模型证据与决策链')
    parser.add_argument('job', type=Path)
    parser.add_argument('--archive', action='store_true', help='使用已封存原始时间戳核对传输后的档案')
    parser.add_argument('--seal', action='store_true', help='严格核验后写入字节档案清单')
    parser.add_argument('--readonly', action='store_true', help='不改写任何证据文件')
    args = parser.parse_args()
    if args.seal and (args.archive or args.readonly):
        parser.error('--seal不能与--archive或--readonly混用')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    result = verify(args.job, archived=args.archive, write=not args.readonly)
    if args.seal:
        archive = write_archive(args.job)
        result['sealed_files'] = len(archive['files'])
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'}, ensure_ascii=False, indent=2))
