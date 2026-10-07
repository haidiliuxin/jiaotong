import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from traffic_agent.llm_settings import Settings, DEFAULTS
from traffic_agent.web import make_handler


class SettingsRecoveryTests(unittest.TestCase):
    def test_public_corruption_is_safe_and_reset_preserves_original_bytes(self):
        for raw in (b'{PRIVATE_BROKEN_CONFIG', b'null', b'[]', b'{}',
                    json.dumps(dict(config=DEFAULTS, secret=True)).encode()):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as folder:
                settings = Settings(folder); settings.path.write_bytes(raw)
                public = settings.public()
                self.assertTrue(public['settings_corrupt'])
                self.assertFalse(public['key_configured'])
                self.assertNotIn('PRIVATE', json.dumps(public))
                self.assertEqual(settings.path.read_bytes(), raw)
                with self.assertRaises(ValueError):settings.credentials()
                with self.assertRaises(ValueError):settings.update(dict(key_action='clear'))
                result = settings.reset_corrupt()
                self.assertFalse(result['settings_corrupt'])
                self.assertFalse(result['key_configured'])
                self.assertEqual((settings.directory/result['backup_name']).read_bytes(), raw)
                self.assertEqual(settings.public()['model'], DEFAULTS['model'])

    def test_reset_refuses_healthy_settings_and_invalid_backup_does_not_replace_original(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(folder)
            settings.update(dict(key_action='clear'))
            original = settings.path.read_bytes()
            with self.assertRaises(ValueError):settings.reset_corrupt()
            self.assertEqual(settings.path.read_bytes(), original)
            settings.path.write_bytes(b'{')
            with patch('traffic_agent.llm_settings.save_json', side_effect=OSError('offline write failure')):
                with self.assertRaises(OSError):settings.reset_corrupt()
            self.assertEqual(settings.path.read_bytes(), b'{')
            self.assertEqual(len(list(settings.directory.glob('llm.corrupt-*.json'))), 1)

    def test_reset_http_is_explicit_same_origin_and_never_calls_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); settings = Settings(root/'settings')
            settings.directory.mkdir();settings.path.write_bytes(b'{')
            with patch('traffic_agent.dispatch_web.Settings', return_value=settings):
                server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(root/'source', root))
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            opener = build_opener(ProxyHandler({}));url = f'http://127.0.0.1:{server.server_address[1]}/api/settings/llm/reset'
            try:
                with patch('traffic_agent.dispatch_web.client_for') as model:
                    # Origin is rejected before reading a body. Sending an unread
                    # body can reset the socket on Windows before HTTP 403 arrives.
                    for headers,body in (({'Content-Type':'application/json','Origin':'https://other.invalid'},b''),
                                         ({'Content-Type':'application/json'},b'{"unexpected":true}')):
                        with self.assertRaises(HTTPError) as denied:
                            opener.open(Request(url, body, headers), timeout=3)
                        self.assertEqual(denied.exception.code, 403 if 'Origin' in headers else 400)
                        denied.exception.close()
                    self.assertEqual(settings.path.read_bytes(), b'{')
                    with opener.open(Request(url, b'{}', {'Content-Type':'application/json'}), timeout=3) as response:
                        self.assertFalse(json.load(response)['settings_corrupt'])
                    model.assert_not_called()
            finally:server.shutdown();server.server_close();thread.join()
