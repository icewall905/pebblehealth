import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "bridge"))
import dashboard  # noqa: E402
import fitness  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(fitness, "DB_PATH", str(tmp_path / "t.sqlite3"))
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>Pebble Health</h1>")
    (web / "manifest.webmanifest").write_text("{}")
    monkeypatch.setattr(dashboard, "WEB_DIR", web)
    monkeypatch.setattr(dashboard, "DOWNLOAD_DIR", tmp_path / "dl")
    return dashboard.app.test_client()


def test_shell_manifest_and_summary(client):
    assert b"Pebble Health" in client.get("/").data
    m = client.get("/manifest.webmanifest")
    assert m.mimetype == "application/manifest+json"
    s = client.get("/api/summary?days=7")
    assert s.status_code == 200 and "recovery" in s.get_json()
    assert s.headers["Cache-Control"] == "no-store"
    assert client.get("/api/summary?days=x").status_code == 400


def test_today_token_reads_summary_but_cannot_upload(client):
    token = client.post("/api/pair?kind=today").get_json()["token"]
    assert fitness.authorize_hrv(token, "today")
    assert not fitness.authorize_hrv(token, "hrv") and not fitness.authorize_hrv(token, "history")
    today = fitness.today()
    assert {"readiness", "sleep_score", "hrv_status", "rhr", "steps"} <= set(today)


def test_unknown_pair_kind_and_missing_download(client):
    assert client.post("/api/pair?kind=admin").status_code == 400
    assert client.get("/pair?kind=admin").status_code == 400
    assert client.get("/download/today").status_code == 404
    assert client.get("/download/../etc").status_code == 404


def test_watch_endpoints_on_web_service_respect_scopes(client):
    hrv = client.post("/api/pair?kind=hrv").get_json()["token"]
    today = client.post("/api/pair?kind=today").get_json()["token"]
    auth = lambda t: {"Authorization": "Bearer " + t}
    assert client.get("/fitness/hrv", headers=auth(hrv)).status_code == 200
    assert client.get("/fitness/hrv", headers=auth(today)).status_code == 401
    assert client.get("/fitness/today", headers=auth(hrv)).status_code == 401
    r = client.get("/fitness/today", headers=auth(today))
    assert r.status_code == 200 and "readiness" in r.get_json()
    assert client.get("/fitness/today").status_code == 401
    assert client.post("/fitness/records", headers=auth(today), json={}).status_code == 401


def test_unknown_host_is_refused_when_allowlist_set(client, monkeypatch):
    monkeypatch.setattr(dashboard, "ALLOWED_HOSTS", {"pebble-health.example.lan"})
    assert client.get("/api/summary", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/summary", headers={"Host": "pebble-health.example.lan"}).status_code == 200


def test_rating_long_watch_workout_skips_duration_check(client):
    with fitness.connect() as db:
        db.execute("INSERT INTO workouts(id,start,seconds,kind,rpe,source) VALUES('pebble:1:run',1700000000,100000,'run',NULL,'pebble')")
    r = client.post("/api/workouts", json={"id": "pebble:1:run", "start": 1700000000, "seconds": 100000, "rpe": 6})
    assert r.status_code == 201
