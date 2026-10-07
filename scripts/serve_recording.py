"""Read-only loopback video player with HTTP byte ranges for chapter seeking."""
import argparse
import mimetypes
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def handler_for(root):
    root = Path(root).resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def send_head(self):
            path = Path(self.translate_path(self.path)).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                self.send_error(404)
                return None
            size = path.stat().st_size
            start, end = 0, size - 1
            requested = self.headers.get('Range')
            if requested:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                if not match or not any(match.groups()) or size == 0:
                    self.send_error(416)
                    return None
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                if start >= size or end < start:
                    self.send_error(416)
                    return None
            stream = path.open('rb')
            stream.seek(start)
            self._remaining = end - start + 1
            self.send_response(206 if requested else 200)
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(self._remaining))
            self.send_header('Accept-Ranges', 'bytes')
            if requested:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            return stream

        def copyfile(self, source, outputfile):
            while self._remaining:
                chunk = source.read(min(1024 * 1024, self._remaining))
                if not chunk:
                    raise OSError('录像文件读取提前结束')
                outputfile.write(chunk)
                self._remaining -= len(chunk)

        def log_message(self, *args):
            pass

    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--port', type=int, default=8794)
    args = parser.parse_args()
    if not args.directory.is_dir() or not 1 <= args.port <= 65535:
        parser.error('需要存在的录像目录与有效端口')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.directory))
    print(f'RECORDING http://127.0.0.1:{args.port}/播放说明.html', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
