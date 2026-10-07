import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from traffic_agent.archive import original_scenario, verify_archive, write_archive


class ArchiveTests(unittest.TestCase):
    def test_relocated_content_accepts_new_timestamps_and_rejects_same_size_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, copy = root/'original', root/'copy'
            source.mkdir()
            (source/'state.json').write_text('{"status":"completed"}')
            (source/'verification.json').write_text('{"passed":true}')
            (source/'result.dat').write_bytes(b'10\n20\n')
            archive = write_archive(source)
            shutil.copytree(source, copy, copy_function=shutil.copyfile)
            os.utime(copy/'result.dat', (100, 100))
            self.assertEqual(verify_archive(copy), archive)
            self.assertNotEqual((copy/'result.dat').stat().st_mtime_ns, archive['files']['result.dat']['mtime_ns'])
            self.assertTrue(original_scenario(archive, r'scenarios\baseline\基础路网').endswith(r'scenarios\baseline\基础路网'))
            (copy/'result.dat').write_bytes(b'11\n20\n')
            with self.assertRaisesRegex(ValueError, '内容缺失或改变'):
                verify_archive(copy)

    def test_archive_rejects_escape_before_reading_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = {'version': 1, 'original_job': r'F:\ITS比赛\智能体实验\jobs\original',
                       'files': {'../outside.dat': {'bytes': 0, 'sha256': '', 'mtime_ns': 0}}}
            (root/'archive_manifest.json').write_text(json.dumps(archive), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '越出'):
                verify_archive(root)


if __name__ == '__main__':
    unittest.main()
