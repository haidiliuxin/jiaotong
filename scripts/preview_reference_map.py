"""Read-only UI verification before integrating the startup map into the app."""
import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.runner import DEFAULT_SOURCE
from traffic_agent.web import make_handler


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8791)
    args=parser.parse_args()
    base=make_handler(DEFAULT_SOURCE,args.root)

    class PreviewHandler(base):
        def do_POST(self):
            if urlparse(self.path).path=='/api/run':
                return self.respond(403,dict(error='此端口仅验收界面，不接受模型运行'))
            return super().do_POST()

    print(f'GEOMETRY UI PREVIEW http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),PreviewHandler).serve_forever()
