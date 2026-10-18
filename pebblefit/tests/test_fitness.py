import sys
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).parents[1]/'bridge'))

spec = importlib.util.spec_from_file_location('fitness', Path(__file__).parents[1] / 'bridge/fitness.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)

class FitnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        f.DB_PATH = self.tmp.name + '/test.sqlite3'
        self.start = (int(time.time()) // 60) * 60 - 3600
    def tearDown(self):
        self.tmp.cleanup()
    def test_idempotent_import_and_separate_loads(self):
        f.profile({'hr_max': 200})
        data = {'ts': self.start, 'records': [{'ts':self.start+i*60, 'hr':160} for i in range(10)],
                'sessions':[{'start':self.start,'seconds':600,'type':'run'}]}
        f.ingest(data); f.ingest(data)
        s=f.summary(); self.assertEqual(s['minute_records'],10); self.assertEqual(s['reports'],1)
        w=s['workouts'][0]; self.assertEqual(w['hr_load'],40); self.assertIsNone(w['rpe_load'])
        f.add_workout({'id':w['id'],'start':self.start,'seconds':600,'rpe':6})
        f.ingest(data)
        self.assertEqual(f.summary()['workouts'][0]['rpe_load'],60)
    def test_model_is_independent_of_visible_range(self):
        from datetime import datetime, timedelta
        f.profile({'load_method':'rpe'})
        today=datetime.now(f.TZ).date()
        start=int(datetime.combine(today-timedelta(days=30),datetime.min.time(),f.TZ).timestamp())+43200
        f.add_workout({'start':start,'seconds':3600,'rpe':5})
        for i in range(31):
            f.confirm_day({'date':str(today-timedelta(days=30-i)),'status':'complete'})
        a,b=f.summary(7),f.summary(42)
        self.assertEqual(a['training']['latest'],b['training']['latest'])
        self.assertEqual(len(a['history']),7)
    def test_history_tokens_cannot_upload_hrv(self):
        token=f.issue_hrv_token('history')['token']
        self.assertTrue(f.authorize_hrv(token,'history'))
        self.assertFalse(f.authorize_hrv(token,'hrv'))
    def test_collector_status_is_separate_from_measurements(self):
        f.collector_ingest({'ts':self.start,'collector_version':120,'schedule_ok':6,'schedule_error':0,'next_wakeup':int(time.time())+3600,'nights_enabled':True})
        d=f.summary()
        self.assertEqual(d['collector']['schedule_ok'],6)
        self.assertEqual(d['hrv']['sessions'],[])
        self.assertEqual(d['reports'],0)
    def test_raw_motion_and_finish_evidence_survive_ingest(self):
        f.collector_ingest({'ts':self.start,'collector_version':130,'schedule_ok':6,'schedule_error':0,'next_wakeup':int(time.time())+3600,'nights_enabled':True,'schedule_ids':[12,13],'schedule_times':[int(time.time())+3600,int(time.time())+7200],'phase':2})
        f.hrv_ingest({'ts':self.start+4,'ppi':[800,810],'offsets_ms':[1000,2000],'collector_version':130,'motion_samples':50,'motion_total':125,'motion_source':'raw_accelerometer','stream_semantics':'latest_ppi_updates'})
        d=f.summary()
        self.assertEqual(d['hrv']['diagnostic']['motion_samples'],50)
        self.assertIsNone(d['hrv']['diagnostic']['vibration_samples'])
        self.assertEqual(d['collector']['phase'],3)
        self.assertEqual(d['collector']['schedule_ids'],[12,13])
        self.assertEqual(d['collector']['evidence'],'measurement_saved')
        self.assertIsNone(d['hrv']['quality']['experimental_rmssd_ms'])
    def test_extended_firmware_counters_and_capacity_are_retained(self):
        result=f.hrv_ingest({'ts':self.start,'ppi':[800]*512,'offsets_ms':[i*350 for i in range(512)],'firmware':'4.40.0','collector_version':140,'hrv_events':513,'zero_ppi_events':1,'hr_events':174,'hr_sum':10440,'hr_valid':174,'capacity_limited':True,'stream_semantics':'latest_ppi_updates'})
        self.assertEqual(result['count'],512)
        self.assertEqual(result['firmware'],'4.40.0')
        self.assertEqual(result['mean_reported_hr'],60)
        self.assertEqual(result['zero_ppi_events'],1)
        self.assertTrue(result['capacity_limited'])
    def test_sparse_hr_is_unknown(self):
        f.profile({'hr_max':200})
        f.ingest({'ts':self.start,'records':[{'ts':self.start,'hr':180}],
                  'sessions':[{'start':self.start,'seconds':600}]})
        self.assertIsNone(f.summary()['workouts'][0]['hr_load'])
        self.assertIsNone(f.summary()['history'][0]['hr_load'])
    def test_bad_batch_rolls_back(self):
        with self.assertRaises(ValueError):
            f.ingest({'ts':self.start,'records':[{'ts':self.start,'hr':float('nan')}]})
        self.assertEqual(f.summary()['reports'],0)
    def test_hrv_stays_diagnostic(self):
        result=f.hrv_ingest({'ts':self.start,'ppi':[800,810,790],'offsets_ms':[0,10000,20000]})
        self.assertIsNone(result['rmssd']); self.assertFalse(result['validated'])
        self.assertEqual(f.summary()['hrv']['status'],'diagnostic_only')
    def test_pairing_token_has_limited_lifetime(self):
        token=f.issue_hrv_token()['token']
        self.assertTrue(f.authorize_hrv(token))
        self.assertFalse(f.authorize_hrv('wrong'))
        with f.connect() as db: db.execute('UPDATE pairing_tokens SET expires=0')
        self.assertFalse(f.authorize_hrv(token))
    def test_successful_upload_renews_without_reviving_expired_token(self):
        token=f.issue_hrv_token()['token']
        with f.connect() as db: db.execute('UPDATE pairing_tokens SET expires=?',(int(time.time())+30,))
        f.refresh_hrv_token(token)
        with f.connect() as db: self.assertGreater(db.execute('SELECT expires FROM pairing_tokens').fetchone()[0],int(time.time())+86400)
        with f.connect() as db: db.execute('UPDATE pairing_tokens SET expires=0')
        f.refresh_hrv_token(token)
        self.assertFalse(f.authorize_hrv(token))
    def test_history_does_not_replace_watch_snapshot(self):
        f.ingest({'ts':self.start,'source':'watchapp','status':{'steps':31}})
        f.ingest({'ts':self.start+1,'source':'phone-history','records':[]})
        self.assertEqual(f.summary()['latest']['status']['steps'],31)
    def test_no_profile_does_not_guess_hr_load(self):
        self.assertIsNone(f.profile()['hr_max'])
        with self.assertRaises(ValueError): f.profile({'hr_max': True})

if __name__ == '__main__': unittest.main()
