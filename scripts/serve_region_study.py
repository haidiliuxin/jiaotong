import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.study_dashboard import serve


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='打开前瞻研究的只读地图对照厅，不启动模型')
    parser.add_argument('job', type=Path)
    parser.add_argument('--port', type=int, default=8782)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('端口须在1至65535之间')
    serve(args.job, args.port)
