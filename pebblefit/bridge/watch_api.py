"""Watch-app endpoints, shared by the receiver (app.py) and the web service.

Each watch app authenticates with its own paired token, limited to one scope:
'hrv' and 'history' may only upload, 'today' may only read the daily summary.
"""
from flask import Blueprint, abort, request

import fitness


def blueprint(admin_authorized=lambda: False):
    bp = Blueprint("watch_api", __name__)

    def token():
        return request.headers.get("Authorization", "").removeprefix("Bearer ")

    @bp.route("/fitness/records", methods=["GET", "POST"])
    def fitness_records():
        if not admin_authorized() and not fitness.authorize_hrv(token(), "history"):
            abort(401)
        if request.method == "GET":
            return {"ok": True, "purpose": "history-upload"}
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        try:
            ts = fitness.ingest(data)
            fitness.refresh_hrv_token(token())
            return {"ok": True, "ts": ts}, 201
        except (ValueError, TypeError, KeyError) as error:
            return {"error": str(error)}, 400

    @bp.route("/fitness/hrv", methods=["GET", "POST"])
    def fitness_hrv():
        if not admin_authorized() and not fitness.authorize_hrv(token()):
            abort(401)
        if request.method == "GET":
            return {"ok": True, "purpose": "hrv-diagnostic-upload"}
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        try:
            result = fitness.collector_ingest(data) if data.get("collector") is True else fitness.hrv_ingest(data)
            fitness.refresh_hrv_token(token())
            return result, 201
        except (ValueError, TypeError) as error:
            return {"error": str(error)}, 400

    @bp.route("/fitness/today", methods=["GET"])
    def fitness_today():
        # Read-only compact summary for the PebbleFit Today watch app.
        if not admin_authorized() and not fitness.authorize_hrv(token(), "today"):
            abort(401)
        result = fitness.today()
        fitness.refresh_hrv_token(token())
        return result, 200, {"Cache-Control": "no-store"}

    return bp
