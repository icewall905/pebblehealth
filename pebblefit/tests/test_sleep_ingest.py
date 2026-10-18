import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'bridge'))
spec = importlib.util.spec_from_file_location('fitness', Path(__file__).parents[1] / 'bridge/fitness.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class SleepIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        f.DB_PATH = self.tmp.name + '/test.sqlite3'
        self.start = (int(time.time()) // 60) * 60 - 36000

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, table):
        with f.connect() as db:
            return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_sleep_routed_separately_and_idempotent(self):
        s = self.start
        data = {'ts': s, 'records': [], 'sessions': [
            {'start': s, 'seconds': 25000, 'type': 'sleep'},
            {'start': s + 600, 'seconds': 3000, 'type': 'restful_sleep'},
            {'start': s + 30000, 'seconds': 900, 'type': 'run'}]}
        f.ingest(data); f.ingest(data)
        self.assertEqual(self.count('workouts'), 1)
        with f.connect() as db:
            self.assertEqual(db.execute('SELECT kind FROM workouts').fetchone()[0], 'run')
        rows = f.sleep_sessions(0)
        self.assertEqual([r['kind'] for r in rows], ['sleep', 'restful_sleep'])
        self.assertEqual(rows[0]['id'], f'pebble:{s}:sleep')
        self.assertEqual(f.sleep_sessions(s + 1)[0]['kind'], 'restful_sleep')

    def test_duration_update(self):
        s = self.start
        f.ingest({'ts': s, 'sessions': [{'start': s, 'seconds': 1000, 'type': 'sleep'}]})
        f.ingest({'ts': s, 'sessions': [{'start': s, 'seconds': 2000, 'type': 'sleep'}]})
        self.assertEqual(self.count('sleep_sessions'), 1)
        self.assertEqual(f.sleep_sessions(0)[0]['seconds'], 2000)
        self.assertEqual(self.count('workouts'), 0)


if __name__ == '__main__':
    unittest.main()
