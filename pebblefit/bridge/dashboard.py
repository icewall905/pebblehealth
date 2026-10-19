"""Pebble Health web app: dashboard, its JSON API, watch pairing and downloads.

Runs as its own process (the ``web`` service) next to the upload receiver
(``app.py``), sharing the same SQLite database.  It has no login: it is meant
to sit on a LAN/VPN-only hostname behind a reverse proxy.  Never expose it to
the public internet without adding authentication.
"""
import os
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import briefing
import fitness
import watch_api

WEB_DIR = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parent / "web"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/downloads"))
DOWNLOADS = {"hrv": "pebblefit-hrv.pbw", "history": "pebblefit-history.pbw", "today": "pebblefit-today.pbw"}
PAIR_PURPOSE = {
    "hrv": ("HRV measurements", "This watch app will be allowed to upload overnight and manual pulse-interval measurements. It cannot read your history."),
    "history": ("health history", "This watch app will be allowed to upload minute-level heart rate, movement, workouts and sleep sessions. It cannot read your history."),
    "today": ("daily summary", "This watch app will be allowed to read today's summary only: readiness, sleep, HRV, resting pulse and training load. It cannot upload, and cannot read your history."),
}

app = Flask(__name__, static_folder=None)
# Watch uploads and the Today read endpoint, so one hostname serves everything.
app.register_blueprint(watch_api.blueprint())


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


# Port 8790 has no login.  Answering only known hostnames blocks DNS-rebinding
# pages from reading the data through a victim's browser.
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}


@app.before_request
def _check_host():
    if ALLOWED_HOSTS and request.host.split(":")[0].lower() not in ALLOWED_HOSTS:
        abort(403)


@app.after_request
def _headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/summary")
def api_summary():
    try:
        return fitness.summary(int(request.args.get("days", 42)))
    except (ValueError, TypeError):
        return {"error": "Invalid day range"}, 400


@app.put("/api/profile")
def api_profile():
    try:
        return fitness.profile(_json_body())
    except (ValueError, TypeError) as error:
        return {"error": str(error)}, 400


@app.post("/api/workouts")
def api_workouts():
    try:
        return {"id": fitness.add_workout(_json_body())}, 201
    except (ValueError, TypeError) as error:
        return {"error": str(error)}, 400


@app.post("/api/days")
def api_days():
    try:
        return fitness.confirm_day(_json_body())
    except (ValueError, TypeError) as error:
        return {"error": str(error)}, 400


@app.post("/api/briefing/run")
def api_briefing_run():
    status = briefing.run_in_background()
    return {"status": status}, 202 if status == "started" else 200


@app.post("/api/pair")
def api_pair():
    kind = request.args.get("kind", "hrv")
    if kind not in PAIR_PURPOSE:
        return {"error": "Unknown pairing type"}, 400
    response = jsonify(fitness.issue_hrv_token(kind))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/pair")
def pair_page():
    kind = request.args.get("kind", "hrv")
    if kind not in PAIR_PURPOSE:
        abort(400)
    title, purpose = PAIR_PURPOSE[kind]
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair Pebble Health</title><style>body{{font:18px/1.5 system-ui;max-width:32em;margin:40px auto;padding:20px}}button{{font:inherit;padding:12px 18px}}</style>
<h1>Connect {title}</h1><p>{purpose} Access expires after 30 days without use.</p>
<button id="pair">Allow</button><p id="status" role="status"></p>
<script>document.getElementById('pair').onclick=async function(){{this.disabled=true;try{{const r=await fetch('api/pair?kind={kind}',{{method:'POST'}});if(!r.ok)throw Error('Pairing failed');const data=await r.json();location.href='pebblejs://close#'+encodeURIComponent(JSON.stringify(data));}}catch(e){{document.getElementById('status').textContent=e.message;this.disabled=false;}}}};</script></html>"""
    return html, 200, {"Cache-Control": "no-store", "Content-Type": "text/html; charset=utf-8"}


@app.get("/download/<kind>")
def download(kind):
    if kind not in DOWNLOADS:
        abort(404)
    path = DOWNLOAD_DIR / DOWNLOADS[kind]
    if not path.is_file():
        return {"error": "This build is not available yet"}, 404
    return send_file(path, mimetype="application/octet-stream", as_attachment=True,
                     download_name=DOWNLOADS[kind], max_age=0)


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html", max_age=0)


@app.get("/<path:name>")
def static_file(name):
    if name.startswith("api/"):
        abort(404)
    # Code, styles and the service worker always revalidate (a cheap 304 on
    # a LAN), so a browser without the worker never runs stale app code after
    # an update.  Only icons are cached for long.
    response = send_from_directory(WEB_DIR, name, max_age=86400 if name.startswith("icons/") else 0)
    if name == "manifest.webmanifest":
        response.mimetype = "application/manifest+json"
    return response
