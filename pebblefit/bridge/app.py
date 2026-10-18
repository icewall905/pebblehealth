"""Pebble health receiver: HTTP POST from patched Pebble app -> MQTT discovery -> Home Assistant."""
import json
import hmac
import fitness
import watch_api
import logging
import os
import threading
import time

import paho.mqtt.client as mqtt
import websocket
from flask import Flask, request, abort, send_file

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("pebble-health")

AUTH_TOKEN = os.environ.get("AUTH_TOKEN") or None
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASS = os.environ.get("MQTT_PASS") or None
DISCOVERY_PREFIX = os.environ.get("DISCOVERY_PREFIX", "homeassistant")
# Feed the existing pebble_ha_integration sensors (sensor.pebble_watch_*) over HA's
# WebSocket API. MQTT stays available but is off by default to avoid duplicate entities.
HA_WS_URL = os.environ.get("HA_WS_URL", "ws://homeassistant.local:8123/api/websocket")
HA_TOKEN = os.environ.get("HA_TOKEN") or None
MQTT_ENABLED = os.environ.get("MQTT_ENABLED", "false").lower() == "true"

NODE = "pebble_health"
STATE_TOPIC = f"{NODE}/state"
AVAIL_TOPIC = f"{NODE}/availability"

DEVICE = {
    "identifiers": [NODE],
    "name": "Pebble Health",
    "manufacturer": "Core Devices",
    "model": "Pebble",
}

# key -> (friendly name, unit, device_class, state_class, icon, value_template)
SENSORS = {
    "steps_today": ("Steps Today", "steps", None, "total_increasing", "mdi:walk",
                    "{{ value_json.today.steps | default(0) }}"),
    "calories_today": ("Calories Today", "kcal", None, "total_increasing", "mdi:fire",
                       "{{ value_json.today.calories | default(0) }}"),
    "distance_today": ("Distance Today", "km", "distance", "total_increasing", "mdi:map-marker-distance",
                       "{{ (value_json.today.distance_m | default(0) / 1000) | round(2) }}"),
    "active_minutes_today": ("Active Minutes Today", "min", "duration", "total_increasing", "mdi:run-fast",
                             "{{ value_json.today.active_minutes | default(0) }}"),
    "heart_rate": ("Heart Rate", "bpm", None, "measurement", "mdi:heart-pulse",
                   "{{ value_json.heart_rate.bpm | default(none) }}"),
    "resting_heart_rate": ("Resting Heart Rate", "bpm", None, "measurement", "mdi:heart",
                           "{{ value_json.resting_heart_rate | default(none) }}"),
    "sleep_total": ("Sleep Total", "min", "duration", "measurement", "mdi:sleep",
                    "{{ value_json.sleep.total_minutes | default(none) }}"),
    "sleep_deep": ("Deep Sleep", "min", "duration", "measurement", "mdi:sleep",
                   "{{ value_json.sleep.deep_minutes | default(none) }}"),
    "last_sync": ("Last Sync", None, "timestamp", None, "mdi:cloud-sync",
                  "{{ value_json.last_sync_iso }}"),
}

# Activity sessions (Pebble Fit walks/runs/workouts). These have no counterpart in
# pebble_ha_integration, so MQTT is the only path for them.
ACTIVITY_SENSORS = {
    "workout_minutes_today": ("Workout Minutes Today", "min", "duration", "total_increasing",
                              "mdi:timer-play",
                              "{{ (value_json.activity.seconds_today | default(0) / 60) | round(0) }}"),
    "workout_count_today": ("Workout Count Today", None, None, "total_increasing",
                            "mdi:counter",
                            "{{ value_json.activity.count_today | default(0) }}"),
    "last_activity_type": ("Last Activity", None, None, None, "mdi:walk",
                           "{{ value_json.activity.last.type | default('none') }}"),
    "last_activity_start": ("Last Activity Start", None, "timestamp", None, "mdi:clock-start",
                            "{{ value_json.activity.last.start_iso | default(none) }}"),
    "last_activity_duration": ("Last Activity Duration", "min", "duration", "measurement",
                               "mdi:timelapse",
                               "{{ (value_json.activity.last.seconds | default(0) / 60) | round(0) }}"),
}

# The pebble_watch_* sensors already cover the daily totals, so the legacy MQTT
# copies stay off unless explicitly asked for.
LEGACY_MQTT_SENSORS = os.environ.get("LEGACY_MQTT_SENSORS", "false").lower() == "true"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pebble-health-bridge")
_last_payload = {}


def ha_report_status(status):
    """Push one status report into pebble_ha_integration via the HA WebSocket API."""
    if not HA_TOKEN:
        logger.error("HA_TOKEN not set; cannot report to pebble_ha_integration")
        return False
    ws = None
    try:
        ws = websocket.create_connection(HA_WS_URL, timeout=10)
        hello = json.loads(ws.recv())
        if hello.get("type") != "auth_required":
            logger.error("Unexpected HA greeting: %s", hello.get("type"))
            return False
        ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        auth = json.loads(ws.recv())
        if auth.get("type") != "auth_ok":
            logger.error("HA auth failed: %s", auth)
            return False
        ws.send(json.dumps({
            "id": 1,
            "type": "pebble_dashboard/report_status",
            "status": status,
        }))
        result = json.loads(ws.recv())
        if result.get("success"):
            logger.info("Reported to pebble_ha_integration: %s", status)
            return True
        logger.error("HA rejected report: %s", result.get("error"))
        return False
    except Exception as e:
        logger.error("HA websocket report failed: %s", e)
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def publish_discovery():
    sensors = dict(ACTIVITY_SENSORS)
    if LEGACY_MQTT_SENSORS:
        sensors.update(SENSORS)
    for key, (name, unit, dev_class, state_class, icon, tpl) in sensors.items():
        cfg = {
            "name": name,
            "unique_id": f"{NODE}_{key}",
            "state_topic": STATE_TOPIC,
            "value_template": tpl,
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE,
            "icon": icon,
        }
        if unit:
            cfg["unit_of_measurement"] = unit
        if dev_class:
            cfg["device_class"] = dev_class
        if state_class:
            cfg["state_class"] = state_class
        topic = f"{DISCOVERY_PREFIX}/sensor/{NODE}/{key}/config"
        client.publish(topic, json.dumps(cfg), qos=1, retain=True)
    logger.info("Published discovery for %d sensors", len(sensors))


def on_connect(c, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        logger.error("MQTT connect failed: %s", reason_code)
        return
    logger.info("Connected to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
    publish_discovery()
    c.publish(AVAIL_TOPIC, "online", qos=1, retain=True)
    # Re-send last known state so entities repopulate after an HA or broker restart.
    if _last_payload:
        c.publish(STATE_TOPIC, json.dumps(_last_payload), qos=1, retain=True)


def mqtt_start():
    if not MQTT_ENABLED:
        logger.info("MQTT disabled; reporting to pebble_ha_integration only")
        return
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(AVAIL_TOPIC, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            logger.error("MQTT connect error, retrying in 10s: %s", e)
            time.sleep(10)
    client.loop_start()


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/health", methods=["POST"])
def ingest():
    if AUTH_TOKEN:
        if request.headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
            logger.warning("Rejected unauthorized POST from %s", request.remote_addr)
            abort(401)

    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        logger.warning("Rejected non-object body from %s", request.remote_addr)
        abort(400)

    try:
        fitness.ingest(data)
    except (ValueError, TypeError, KeyError) as error:
        return {"error": str(error)}, 400

    ts = data.get("ts") or int(time.time())
    data["last_sync_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))

    activity = data.get("activity")
    if isinstance(activity, dict):
        last = activity.get("last")
        if isinstance(last, dict) and last.get("start"):
            last["start_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                              time.localtime(int(last["start"])))
        logger.info("Activity: count=%s seconds=%s last=%s",
                    activity.get("count_today"), activity.get("seconds_today"), last)

    # Preferred shape: the watchapp posts the integration's own field names.
    status = data.get("status")
    if isinstance(status, dict) and status:
        ok = ha_report_status(status)
        if MQTT_ENABLED:
            publish_mqtt_state(data)
        # The durable receiver has accepted this report even if HA is unavailable.
        return {"ok": True, "ha_delivered": ok}, 200

    # Legacy shape kept so an older installed watchapp keeps working.
    data.setdefault("today", {})
    data.pop("minutes", None)
    today = data["today"]
    legacy = {}
    if today.get("steps") is not None:
        legacy["steps"] = today["steps"]
    if today.get("active_minutes") is not None:
        legacy["active_seconds"] = today["active_minutes"] * 60
    if today.get("distance_m") is not None:
        legacy["distance_meters"] = today["distance_m"]
    if today.get("calories") is not None:
        legacy["active_kcal"] = today["calories"]
    sleep = data.get("sleep") or {}
    if sleep.get("total_minutes") is not None:
        legacy["sleep_seconds"] = sleep["total_minutes"] * 60
    if sleep.get("deep_minutes") is not None:
        legacy["sleep_restful_seconds"] = sleep["deep_minutes"] * 60
    hr = (data.get("heart_rate") or {}).get("bpm")
    if hr:
        legacy["heart_rate_bpm"] = hr

    ok = ha_report_status(legacy) if legacy else False
    if MQTT_ENABLED:
        publish_mqtt_state(data)
    return {"ok": True, "ha_delivered": ok}, 200


def publish_mqtt_state(data):
    global _last_payload
    _last_payload = data
    client.publish(STATE_TOPIC, json.dumps(data), qos=1, retain=True)


@app.route("/health/watchapp.pbw")
def watchapp():
    # The bundle embeds the bearer token in its JS, so gate the download on that
    # same token: /health/watchapp.pbw?t=<token>
    if AUTH_TOKEN and request.args.get("t") != AUTH_TOKEN:
        logger.warning("Rejected unauthorized pbw download from %s", request.remote_addr)
        abort(401)
    path = "/code/watchapp.pbw"
    if not os.path.exists(path):
        abort(404)
    logger.info("Serving watchapp.pbw to %s", request.remote_addr)
    return send_file(path, mimetype="application/octet-stream",
                     as_attachment=True, download_name="ha-health-sync.pbw")


def fitness_authorized():
    # Fitness reads expose health history: fail closed when no token is configured.
    return bool(AUTH_TOKEN) and hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {AUTH_TOKEN}")


@app.route("/fitness", methods=["GET"])
def fitness_summary():
    if not fitness_authorized():
        abort(401)
    try:
        return fitness.summary(request.args.get("days", 42))
    except (ValueError, TypeError):
        return {"error": "Invalid day range"}, 400


@app.route("/fitness/profile", methods=["PUT"])
def fitness_profile():
    if not fitness_authorized():
        abort(401)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    try:
        return fitness.profile(data)
    except (ValueError, TypeError) as error:
        return {"error": str(error)}, 400


@app.route("/fitness/workouts", methods=["POST"])
def fitness_workout():
    if not fitness_authorized():
        abort(401)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    try:
        return {"id": fitness.add_workout(data)}, 201
    except (ValueError, TypeError) as error:
        return {"error": str(error)}, 400


@app.route("/fitness/days", methods=["POST"])
def fitness_day():
    if not fitness_authorized():abort(401)
    data=request.get_json(silent=True)
    if not isinstance(data,dict):abort(400)
    try:return fitness.confirm_day(data)
    except (ValueError,TypeError) as error:return {"error":str(error)},400


@app.route("/fitness/pair", methods=["POST"])
def fitness_pair():
    if not fitness_authorized():
        abort(401)
    data=request.get_json(silent=True) or {}
    try:return fitness.issue_hrv_token(data.get("scope","hrv"))
    except ValueError as error:return {"error":str(error)},400


app.register_blueprint(watch_api.blueprint(fitness_authorized))


mqtt_start()
