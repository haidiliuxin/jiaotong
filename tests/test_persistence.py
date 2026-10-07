"""Atomic state publication with real Windows sharing conflicts, no API/native calls."""
import errno
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from traffic_agent.data import JSON_REPLACE_DELAYS,save_json


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'state.json'
        self.before=dict(step='before',note='原记录仍可读取')
        self.after=dict(step='after',rows=list(range(500)))
        save_json(self.path,self.before);self.original=self.path.read_bytes()

    @unittest.skipUnless(os.name=='nt','Real Windows reader-sharing semantics')
    def test_reader_lock_release_allows_the_same_snapshot_to_be_published(self):
        blocked=threading.Event();errors=[];original=Path.replace
        def replace(source,target):
            try:return original(source,target)
            except OSError as exc:
                errors.append(exc.winerror);blocked.set();raise
        with self.path.open('rb') as reader,ThreadPoolExecutor(1) as workers:
            with patch.object(Path,'replace',replace):
                future=workers.submit(save_json,self.path,self.after)
                try:
                    self.assertTrue(blocked.wait(2),'Did not encounter a real sharing conflict')
                    self.assertFalse(future.done())
                    self.assertEqual(json.loads(reader.read()),self.before)
                finally:reader.close()
                future.result(timeout=3)
        self.assertTrue(errors);self.assertTrue(set(errors)<={5,32,33})
        self.assertEqual(json.loads(self.path.read_text('utf-8')),self.after)
        self.assertEqual(list(self.root.iterdir()),[self.path])

    @unittest.skipUnless(os.name=='nt','Real Windows reader-sharing semantics')
    def test_permanent_reader_lock_has_a_finite_limit_and_keeps_old_record(self):
        original=Path.replace
        with self.path.open('rb'),patch('traffic_agent.data.time.sleep') as sleep, \
                patch.object(Path,'replace',autospec=True,side_effect=original) as replace:
            with self.assertRaises(PermissionError):save_json(self.path,self.after)
        self.assertEqual(replace.call_count,len(JSON_REPLACE_DELAYS)+1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],list(JSON_REPLACE_DELAYS))
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(list(self.root.iterdir()),[self.path])

    def test_unrelated_io_error_is_not_retried_or_hidden(self):
        with patch.object(Path,'replace',side_effect=OSError(errno.ENOSPC,'storage full')) as replace, \
                patch('traffic_agent.data.time.sleep') as sleep:
            with self.assertRaises(OSError) as caught:save_json(self.path,self.after)
        self.assertEqual(caught.exception.errno,errno.ENOSPC)
        replace.assert_called_once();sleep.assert_not_called()
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(list(self.root.iterdir()),[self.path])

    def test_invalid_json_does_not_open_a_temporary_or_replace_existing_data(self):
        with patch('traffic_agent.data.tempfile.mkstemp') as create:
            with self.assertRaises(ValueError):save_json(self.path,dict(value=float('nan')))
        create.assert_not_called();self.assertEqual(self.path.read_bytes(),self.original)

    def test_concurrent_writers_have_independent_complete_snapshots(self):
        stale=self.path.with_suffix('.json.tmp');stale.write_bytes(b'old diagnostic evidence')
        barrier=threading.Barrier(2);seen=set();guard=threading.Lock();original=Path.replace
        first=dict(writer=1,rows=['one']*1000);second=dict(writer=2,rows=['two']*1000)
        def replace(source,target):
            with guard:
                new=source not in seen;seen.add(source)
            if new:barrier.wait(timeout=2)
            return original(source,target)
        with patch.object(Path,'replace',replace),ThreadPoolExecutor(2) as workers:
            futures=[workers.submit(save_json,self.path,x) for x in (first,second)]
            for f in futures:f.result(timeout=4)
        self.assertEqual(len(seen),2)
        self.assertIn(json.loads(self.path.read_text('utf-8')),[first,second])
        self.assertEqual(stale.read_bytes(),b'old diagnostic evidence')
        self.assertEqual(set(self.root.iterdir()),{self.path,stale})


if __name__=='__main__':unittest.main()
