"""Durable, single-wearer Pebble records and transparent training-load models."""
import json
import hashlib
import secrets
import math
import os
import sqlite3
import time
import training
import hrv as hrv_analytics
import recovery as recovery_model
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("PEBBLE_TZ", "UTC"))  # the wearer's local day boundaries
DB_PATH = os.environ.get("FITNESS_DB", "/data/fitness.sqlite3")

SLEEP_KINDS = ("sleep", "restful_sleep")


def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS reports(ts INTEGER, source TEXT, received INTEGER, payload TEXT,
      PRIMARY KEY(ts,source));
    CREATE TABLE IF NOT EXISTS minutes(ts INTEGER PRIMARY KEY, hr INTEGER, steps INTEGER,
      active INTEGER, payload TEXT);
    CREATE TABLE IF NOT EXISTS workouts(id TEXT PRIMARY KEY, start INTEGER, seconds INTEGER,
      kind TEXT, rpe REAL, source TEXT);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS pairing_tokens(hash TEXT PRIMARY KEY, expires INTEGER);
    CREATE TABLE IF NOT EXISTS day_confirmations(day TEXT PRIMARY KEY,status TEXT,confirmed_at INTEGER);
    CREATE TABLE IF NOT EXISTS collector_reports(ts INTEGER PRIMARY KEY,received INTEGER,payload TEXT);
    CREATE TABLE IF NOT EXISTS hrv_sessions(id TEXT PRIMARY KEY, ts INTEGER, payload TEXT);
    CREATE TABLE IF NOT EXISTS sleep_sessions(id TEXT PRIMARY KEY, start INTEGER, seconds INTEGER, kind TEXT);
    """)
    if 'ignored' not in {r[1] for r in db.execute('PRAGMA table_info(workouts)')}:
        db.execute('ALTER TABLE workouts ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0')
    if 'scope' not in {r[1] for r in db.execute('PRAGMA table_info(pairing_tokens)')}:
        db.execute("ALTER TABLE pairing_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'hrv'")
    return db


def number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def ingest(data):
    now = int(time.time())
    ts = int(number(data.get("ts", now), 946684800, now + 300, "timestamp"))
    source = str(data.get("source", "phone"))[:80]
    rows = data.get("records", [])
    sessions = data.get("sessions", [])
    if not isinstance(rows, list) or len(rows) > 3000 or not isinstance(sessions, list) or len(sessions) > 1000:
        raise ValueError("Invalid record batch")
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?)", (ts, source, now, json.dumps(data)))
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Minute records must be objects")
            t = int(number(row.get("ts"), 946684800, now + 300, "record timestamp"))
            hr = row.get("hr")
            hr = int(number(hr, 1, 300, "heart rate")) if hr else None
            steps = int(number(row.get("steps", 0), 0, 10000, "steps"))
            active = int(number(row.get("active", 0), 0, 1, "active"))
            db.execute("INSERT OR REPLACE INTO minutes VALUES(?,?,?,?,?)", (t, hr, steps, active, json.dumps(row)))
        for s in sessions:
            if not isinstance(s, dict):
                raise ValueError("Sessions must be objects")
            start = int(number(s.get("start"), 946684800, now + 300, "session start"))
            seconds = int(number(s.get("seconds"), 1, 172800, "session duration"))
            kind = str(s.get("type", "workout"))[:40]
            ident = f"pebble:{start}:{kind}"
            if kind in SLEEP_KINDS:
                db.execute("""INSERT INTO sleep_sessions(id,start,seconds,kind) VALUES(?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET seconds=excluded.seconds""", (ident, start, seconds, kind))
                continue
            db.execute("""INSERT INTO workouts(id,start,seconds,kind,rpe,source) VALUES(?,?,?,?,NULL,'pebble')
              ON CONFLICT(id) DO UPDATE SET seconds=excluded.seconds""", (ident, start, seconds, kind))
    return ts


def sleep_sessions(since=0):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,start,seconds,kind FROM sleep_sessions WHERE start>=? ORDER BY start", (int(since),))]


def profile(data=None):
    with connect() as db:
        values={r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
        if data is not None:
            if 'hr_max' in data:
                value=data['hr_max']
                values['hr_max']=int(number(value,100,240,'Maximum heart rate')) if value is not None else None
            if 'load_method' in data:
                if data['load_method'] not in ('hr','rpe'):raise ValueError('Choose HR or session effort')
                values['load_method']=data['load_method']
            for key,value in values.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value)))
    return {'hr_max':values.get('hr_max'), 'load_method':values.get('load_method','hr')}


def add_workout(data):
    if 'ignored' in data:
        if not isinstance(data['ignored'],bool):raise ValueError('ignored must be true or false')
        with connect() as db:
            result=db.execute('UPDATE workouts SET ignored=? WHERE id=?',(int(data['ignored']),str(data.get('id',''))))
            if not result.rowcount:raise ValueError('Workout not found')
        return data['id']
    now = int(time.time())
    rpe = float(number(data.get("rpe"), 1, 10, "Perceived effort"))
    if data.get("id"):
        # Rating an existing (possibly watch-detected) workout: its own start
        # and duration stand, so they are not re-validated here.
        with connect() as db:
            if not db.execute("UPDATE workouts SET rpe=? WHERE id=?", (rpe, str(data["id"])[:120])).rowcount:
                raise ValueError("Workout not found")
        return str(data["id"])[:120]
    start = int(number(data.get("start"), 946684800, now + 300, "Workout start"))
    seconds = int(number(data.get("seconds"), 60, 86400, "Duration"))
    ident = f"manual:{start}"
    with connect() as db:
        db.execute("INSERT INTO workouts(id,start,seconds,kind,rpe,source) VALUES(?,?,?,?,?,'manual') ON CONFLICT(id) DO UPDATE SET rpe=excluded.rpe,seconds=excluded.seconds",
                   (ident, start, seconds, str(data.get("type", "workout"))[:40], rpe))
    return ident


def collector_ingest(data):
    now=int(time.time())
    ts=int(number(data.get('ts'),946684800,now+300,'Collector timestamp'))
    payload={'ts':ts,'received':now,'collector_version':int(number(data.get('collector_version'),0,99999,'Collector version')),
      'schedule_ok':int(number(data.get('schedule_ok'),0,8,'Scheduled slots')),
      'schedule_error':int(number(data.get('schedule_error'),-10000,0,'Scheduler error')),
      'next_wakeup':int(number(data.get('next_wakeup'),0,now+8*86400,'Next wakeup')),
      'nights_enabled':data.get('nights_enabled') is True}
    payload['firmware']=str(data.get('firmware','unknown'))[:32]
    payload['phase']=int(number(data.get('phase',1),1,4,'Collector phase'))
    payload['launch']='scheduled' if data.get('launch')=='scheduled' else 'manual'
    for key,lo,hi in [('schedule_ids',-10000,2147483647),('schedule_times',0,now+8*86400)]:
        values=data.get(key,[])
        if not isinstance(values,list) or len(values)>6:raise ValueError('Invalid scheduled slots')
        payload[key]=[int(number(v,lo,hi,'Scheduled slot')) for v in values]
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO collector_reports VALUES(?,?,?)',(ts,now,json.dumps(payload)))
    return {'ok':True}


def hrv_ingest(data):
    now = int(time.time())
    ts = int(number(data.get("ts"), 946684800, now + 300, "HRV timestamp"))
    ppi = data.get("ppi", [])
    offsets = data.get("offsets_ms", [])
    if not isinstance(ppi, list) or not 1 <= len(ppi) <= 1000 or len(offsets) != len(ppi):
        raise ValueError("HRV requires paired interval values and event times")
    for p in ppi:
        number(p, 1, 65535, "PPI")
    previous = -1
    for offset in offsets:
        number(offset, 0, 600000, "Event offset")
        if offset < previous:
            raise ValueError("Event times must be ordered")
        previous = offset
    # Raw diagnostic only: event cadence does not establish consecutive beats.
    payload = {"ppi": ppi, "offsets_ms": offsets, "duration_ms": offsets[-1],
               "count": len(ppi), "context": "scheduled" if data.get("scheduled") is True else "manual", "motion_total": number(data.get("motion_total", 0), 0, 4294967295, "motion"),
               "motion_samples": number(data.get("motion_samples",0), 0, 100000, "motion sample count"),
               "validated": False, "rmssd": None}
    payload['firmware']=str(data.get('firmware','unknown'))[:32]
    payload['capacity_limited']=data.get('capacity_limited') is True
    for key in ('hrv_events','zero_ppi_events','hr_events','hr_sum','hr_valid'):
        payload[key]=int(number(data.get(key,0),0,1000000,'Sensor counter'))
    payload['mean_reported_hr']=round(payload['hr_sum']/payload['hr_valid'],2) if payload['hr_valid'] else None
    for key in ('collector_version','accel_callbacks','vibration_samples'):
        payload[key]=int(number(data.get(key,0),0,100000,'Collector '+key))
    payload['accel_rate_result']=int(number(data.get('accel_rate_result',0),-10000,10000,'Acceleration status'))
    payload['motion_source']='raw_accelerometer' if data.get('motion_source')=='raw_accelerometer' else 'processed_accelerometer'
    payload['vibration_status']='unknown' if payload['motion_source']=='raw_accelerometer' else 'flagged_samples_excluded'
    if payload['vibration_status']=='unknown':payload['vibration_samples']=None
    if data.get('stream_semantics')=='latest_ppi_updates':payload['stream_semantics']='latest_ppi_updates'
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO hrv_sessions VALUES(?,?,?)", (f"ppi:{ts}", ts, json.dumps(payload)))
        # A saved physiological upload is authoritative evidence of a completed
        # collection. Link only launch metadata from that same measurement window.
        preceding=db.execute('SELECT payload FROM collector_reports WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 1',(ts-300,ts+5)).fetchone()
        outcome=json.loads(preceding['payload']) if preceding else {}
        outcome.update({'ts':min(now,ts+int(offsets[-1]/1000)),'received':now,'phase':3,
            'measurement_ts':ts,'collector_version':payload['collector_version'],
            'launch':payload['context'],'evidence':'measurement_saved'})
        db.execute('INSERT OR REPLACE INTO collector_reports VALUES(?,?,?)',(outcome['ts'],now,json.dumps(outcome)))
    return payload


def summary(days=42):
    now = int(time.time())
    days = max(7, min(int(days), 365))
    start_day = datetime.fromtimestamp(now, TZ).date() - timedelta(days=days - 1)
    since = int(datetime.combine(start_day, datetime.min.time(), TZ).timestamp())
    with connect() as db:
        latest = db.execute("SELECT * FROM reports WHERE source NOT IN ('phone-history','watch-history') ORDER BY ts DESC LIMIT 1").fetchone()
        minutes = [dict(r) for r in db.execute("SELECT * FROM minutes WHERE ts>=? ORDER BY ts", (since,))]
        workouts = [dict(r) for r in db.execute("SELECT * FROM workouts ORDER BY start DESC")]
        training_minutes = {}
        for workout in workouts:
            for row in db.execute("SELECT * FROM minutes WHERE ts>=? AND ts<?", (workout["start"],workout["start"]+workout["seconds"])):
                training_minutes[row["ts"]] = dict(row)
        confirmations={r['day']:r['status'] for r in db.execute('SELECT * FROM day_confirmations')}
        hrv_rows=[{'ts':r['ts'],'payload':json.loads(r['payload'])} for r in db.execute('SELECT ts,payload FROM hrv_sessions ORDER BY ts')]
        observed=[{'ts':r['ts'],'hr':r['hr'],'steps':r['steps']} for r in minutes]
        # Snapshots remain sparse observations, never workout HR coverage.
        for report in db.execute("SELECT ts,payload FROM reports WHERE source != 'phone-history' AND source != 'watch-history' AND ts>=? ORDER BY ts",(since,)):
            raw=json.loads(report['payload']);status=raw.get('status',{})
            pulse=status.get('heart_rate_bpm') or (raw.get('heart_rate') or {}).get('bpm')
            if pulse:observed.append({'ts':report['ts'],'hr':pulse,'steps':None,'snapshot':True})
        collector_history=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM collector_reports ORDER BY ts DESC LIMIT 12')]
        # Optional provisional-baseline spread imported from another device
        # (see research/seed_baseline.py); never raw history.
        seed_row=db.execute("SELECT value FROM settings WHERE key='baseline_seed'").fetchone()
        seed=json.loads(seed_row['value']) if seed_row else None
        sleep_rows=[dict(r) for r in db.execute('SELECT start,seconds,kind FROM sleep_sessions WHERE start>=? ORDER BY start',(now-70*86400,))]
        # Hourly snapshots carry the watch's running sleep total for the current
        # local day; its daily maximum is the previous night's sleep.
        snapshot_sleep={}
        for r in db.execute("""SELECT ts,json_extract(payload,'$.status.sleep_seconds') s,json_extract(payload,'$.status.sleep_restful_seconds') rs
            FROM reports WHERE source='watchapp' AND ts>=?""",(now-70*86400,)):
            if r['s']:
                d=datetime.fromtimestamp(r['ts'],TZ).date().isoformat()
                if r['s']>snapshot_sleep.get(d,(0,0))[0]:snapshot_sleep[d]=(int(r['s']),int(r['rs'] or 0))
        collector=db.execute('SELECT payload FROM collector_reports ORDER BY ts DESC LIMIT 1').fetchone()
        hrv = db.execute("SELECT ts,payload FROM hrv_sessions ORDER BY ts DESC LIMIT 1").fetchone()
        count = db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        hrv_recent = []
        for row in db.execute("SELECT ts,payload FROM hrv_sessions ORDER BY ts DESC LIMIT 12"):
            payload=json.loads(row["payload"])
            hrv_recent.append({"ts":row["ts"], "count":payload["count"], "duration_ms":payload["duration_ms"],
                "context":payload.get("context","manual"), "validated":False})
    config = profile()
    day_list=[str(start_day+timedelta(days=i)) for i in range(days)]
    earliest=min([start_day]+[datetime.fromtimestamp(w['start'],TZ).date() for w in workouts]+[datetime.fromisoformat(d).date() for d in confirmations])
    earliest=max(earliest,datetime.fromtimestamp(now,TZ).date()-timedelta(days=729))
    full_days=[str(earliest+timedelta(days=i)) for i in range((datetime.fromtimestamp(now,TZ).date()-earliest).days+1)]
    load=training.analyze(workouts,list(training_minutes.values()),full_days,config['hr_max'],config['load_method'],confirmations)
    full_history=load['history']
    load['history']=[r for r in load['history'] if r['date']>=str(start_day)]
    load['workouts']=[w for w in load['workouts'] if w['start']>=since]
    workouts=load['workouts'];history=load['history']
    recovery=hrv_analytics.analyze_history(hrv_rows)
    nightly=recovery_model.build([(r['ts'],hrv_analytics.analyze_session(r['payload'])) for r in hrv_rows if r['ts']>=now-70*86400],
                                 sleep_rows,snapshot_sleep,full_history,now,seed=seed)
    quality=hrv_analytics.analyze_session(json.loads(hrv['payload'])) if hrv else None
    last = json.loads(latest["payload"]) if latest else None
    return {"wearer": os.environ.get("WEARER_NAME", ""), "now": now, "collector_history":collector_history, "collector":json.loads(collector["payload"]) if collector else None, "profile": config, "latest": last,
      "last_received": latest["received"] if latest else None,
      "sync_age_seconds": now - latest["received"] if latest else None,
      "reports": count, "minute_records": len(minutes), "workouts": workouts, "history": history, "training": load,
      "observations": sorted(observed,key=lambda r:r["ts"])[-15000:], "confirmations": confirmations,
      "hrv": {"status": "diagnostic_only" if hrv else "not_collected", "ts": hrv["ts"] if hrv else None,
              "diagnostic": json.loads(hrv["payload"]) if hrv else None, "sessions": hrv_recent, "quality": quality, "analysis":recovery},
      "stress": recovery["stress"], "recovery": nightly,
      "model": {"hr": "Edwards-style zone minutes v1; at least 80% HR coverage", "rpe": "Session minutes × perceived effort (1–10)",
                "note": "Separate load scales. Unrecorded days are unknown; fitness/fatigue estimates need confirmed rest days and recording coverage."}}


def today():
    """Compact latest-recovery summary for the watch (the 'today' read scope)."""
    s = summary(7)
    rc = s["recovery"]
    ready, sleep, hs = rc["readiness"], rc["sleep"], rc["hrv_status"]
    night = rc["nights"][-1] if rc["nights"] and rc["nights"][-1]["night"] == rc["today"] else {}
    t = (s.get("training") or {}).get("latest") or {}
    status = (s.get("latest") or {}).get("status") or {}
    # ts is when the newest watch data arrived, so the watch's "updated ..."
    # reflects the data's age rather than the time of this request.
    data_ts = max([x for x in (s.get("last_received"), (s["hrv"] or {}).get("ts")) if x] or [None])
    return {"ts": data_ts, "readiness": ready.get("score"), "readiness_status": ready.get("status"),
            "sleep_score": sleep.get("score"),
            "sleep_min": round(sleep["sleep_s"] / 60) if sleep.get("sleep_s") is not None else None,
            "restful_min": round(sleep["restful_s"] / 60) if sleep.get("restful_s") is not None else None,
            "hrv_status": hs.get("status"), "hrv_ms": night.get("rmssd_ms"), "weekly_hrv_ms": hs.get("weekly_rmssd_ms"),
            "baseline_nights": hs.get("baseline_nights", 0), "rhr": night.get("resting_hr"),
            "breathing": night.get("breaths_per_min"), "load_acute": t.get("acute"), "load_chronic": t.get("chronic"),
            "steps": status.get("steps")}


def issue_hrv_token(scope="hrv"):
    # 'today' is the only read scope: it can fetch the compact daily summary
    # for the watch, never history or raw data.
    if scope not in ("hrv","history","today"):raise ValueError("Unknown pairing scope")
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 30 * 86400
    with connect() as db:
        db.execute("DELETE FROM pairing_tokens WHERE expires < ?", (int(time.time()),))
        db.execute("INSERT INTO pairing_tokens(hash,expires,scope) VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), expires,scope))
    return {"token": token, "expires": expires}


def authorize_hrv(token, scope="hrv"):
    if not token or len(token) > 200:
        return False
    with connect() as db:
        return db.execute("SELECT 1 FROM pairing_tokens WHERE hash=? AND expires>? AND scope=?",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()),scope)).fetchone() is not None


def refresh_hrv_token(token):
    with connect() as db:
        db.execute("UPDATE pairing_tokens SET expires=? WHERE hash=? AND expires>?", (
            int(time.time()) + 30 * 86400, hashlib.sha256(token.encode()).hexdigest(), int(time.time())))


def confirm_day(data):
    from datetime import date
    day=date.fromisoformat(str(data.get('date','')))
    if day>datetime.now(TZ).date() or day<datetime.now(TZ).date()-timedelta(days=730):raise ValueError('Day is outside the supported history')
    status=data.get('status')
    if status not in ('complete','rest','unknown'):raise ValueError('Choose complete, rest, or unknown')
    with connect() as db:
        if status=='rest':
            for row in db.execute('SELECT start FROM workouts WHERE ignored=0'):
                if datetime.fromtimestamp(row['start'],TZ).date()==day:raise ValueError('This day has workouts. Confirm it complete or exclude duplicate sessions first.')
        if status=='unknown':db.execute('DELETE FROM day_confirmations WHERE day=?',(str(day),))
        else:db.execute('INSERT OR REPLACE INTO day_confirmations VALUES(?,?,?)',(str(day),status,int(time.time())))
    return {'date':str(day),'status':status}
