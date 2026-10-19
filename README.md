# Pebble Health

A self-hosted health dashboard for the **Pebble Time 2**, in the spirit of Garmin Connect's "My Day":
readiness, sleep score, overnight HRV status, resting pulse, breathing rate, training load and
experimental stress. It works as an installable phone app (PWA), and a small watch app shows the
daily summary on your wrist.

Everything runs on your own server. Measurements stay in a single SQLite file.

> **Experimental.** None of these numbers are medical measurements. Scores compare you with your
> own history and say so when there isn't enough data yet. They never fill gaps with guesses.

## What's in the box

| Part | Path | What it does |
|---|---|---|
| Server | `pebblefit/bridge/` | Flask app: watch uploads, analytics, dashboard API, optional Home Assistant bridge |
| Dashboard / PWA | `pebblefit/web/` | Dependency-free single-page app with offline support, light/dark themes, phone and desktop layouts |
| Deployment | `pebblefit/deploy/` | Dockerfile, compose file, deploy and watch-build scripts |
| HRV watch app | `pebblefit/hrv-watch/` | 3-minute pulse-interval recordings, manual or scheduled at 02:00/03:00/04:00 |
| History watch app | `pebblefit/history-watch/` | Daily export of minute heart rate/steps, workouts and sleep sessions |
| Today watch app | `pebblefit/today-watch/` | Read-only daily summary on the watch: readiness, sleep, HRV, resting pulse, load |
| Tests | `pebblefit/tests/` | Python analytics/API tests and JS transport tests for the watch apps |

## How HRV works on a Pebble Time 2

PebbleOS 4.41 delivers HRV through the Goodix sensor library. The library reports the **current
RR interval once per second**, whatever your heart rate, so the stream is an instantaneous-RR
curve sampled at 1 Hz (a *tachogram*), not one value per heartbeat. Signs of this:
- Values arrive every ~997 ms.
- The intervals add up to more than the elapsed time.
- Consecutive values are almost never repeated.

Treating those values as beats gives wrong RMSSD. Deduplicating them is just as wrong. Pebble Health
reconstructs beats instead:
1. Detects the 1 Hz stream automatically.
2. Places samples on a uniform clock.
3. Steps through a Catmull-Rom interpolated RR curve within each artifact-free run.
4. Computes RMSSD over the reconstructed beats.

The method is validated as follows:
- **Synthetic tests:** 0 to −10% error, largest with fast breathing (the cost of 1 Hz sampling).
- **Chest strap:** one resting comparison against a chest strap came within a few percent.

Expect the watch to read roughly 5–10% below a chest strap. Within-person trends are what the scores use.

The same curve also carries the breathing rhythm. Overnight, the dominant oscillation gives a
breathing rate. Awake recordings are dominated by a ~0.1 Hz blood-pressure rhythm, so no breathing
rate is reported from them.

## Metrics

- **HRV status:** a 7-night average of ln RMSSD compared with your usual range from earlier nights.
  The status is balanced, above usual, below usual or low. It needs 14 baseline nights.
  Optionally, `baseline_seed` can supply the night-to-night spread from another device, which gives
  a provisional status after 7 nights.
- **Sleep score:** duration against an 8-hour need (60%) and timing regularity over the week (40%).
  The watch's "restful" time is shown but not scored, because it isn't a validated sleep stage.
  Afternoon naps never count toward the previous night.
- **Readiness:** a weighted mean of overnight HRV (30%), resting pulse (20%), sleep (25%) and
  training-load balance (25%), each scored against your own normal. It lists any missing inputs
  and needs HRV or sleep plus at least one other input.
- **Training load:** HR-zone minutes, or session effort (RPE) × minutes. Short-term (7-day) and
  long-term (42-day) averages are computed only over confirmed, unbroken day series.

## Morning briefing (optional)

Every morning a language model writes a short overview at the top of each page: a headline, a two-sentence
summary, highlights tagged good / neutral / worth watching, one practical focus, and data-quality notes.
How it stays honest:
- **The model never calculates.** `bridge/briefing.py` computes compact facts first, already compared with
  your own history, and the model only phrases them.
- **Every number is checked.** Each number in the answer must match a fact (rounding allowed). A briefing
  with invented numbers is retried once, and anything still ungrounded is dropped.
- **Any OpenAI-compatible endpoint works**, for example a local llama.cpp server. Reasoning models are
  supported and given a generous token budget, since this runs once a night.

## Running it

Requirements: Docker, plus a reverse proxy with HTTPS for the PWA and the phone.

```bash
git clone https://github.com/icewall905/pebblehealth.git && cd pebblehealth/pebblefit
HOST=root@my-server DEST=/srv/pebble-health ./deploy/deploy.sh
```

`deploy.sh` copies the server code, dashboard and compose file to `DEST` over SSH, then builds and
starts the containers. It never touches the server's `data/`, `downloads/` or `.env`. Put your
settings in `DEST/.env` (below) and run the script again.

Two services share `./data/fitness.sqlite3`:
- **`web` (port 8790):** dashboard, API, pairing, watch endpoints.
- **`receiver` (port 9998):** the optional Home Assistant bridge and admin API.

Point your reverse proxy's hostname at port 8790.

### Settings (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hostnames the dashboard answers to (DNS-rebinding protection). Add your proxy hostname. |
| `PEBBLE_TZ` | `UTC` | Your time zone, e.g. `Europe/Berlin`. Defines day and night boundaries. |
| `WEARER_NAME` | empty | Name used in the dashboard greeting |
| `AUTH_TOKEN` | empty | Admin bearer token for the receiver's `/fitness` API |
| `HA_WS_URL`, `HA_TOKEN` | — | Optional: report watch status to Home Assistant |
| `MQTT_ENABLED`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` | off | Optional MQTT discovery |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | off | Optional morning briefing via an OpenAI-compatible endpoint |
| `BRIEFING_TIME` | `05:00,05:40` | Daily briefing times; later times only run if new watch data arrived since the last briefing |
| `LLM_MAX_TOKENS` | `32000` | Output budget, including the model's reasoning |
| `WEIGHT_PG_DSN`, `WEIGHT_PG_QUERY`, `PROFILE_PG_QUERY` | off | Optional weight history and body profile (sex, birth date, height) from PostgreSQL |

> **Security:** the dashboard has **no login**. Run it on a LAN/VPN-only hostname, or put
> authentication in front of it (e.g. forward-auth in your proxy) before exposing it.
>
> Watch apps authenticate with per-app pairing tokens, each limited to one scope:
> - HRV and History may only upload.
> - Today may only read the daily summary.
>
> Tokens expire after 30 days without use.

### Watch apps

You need the [Pebble SDK](https://developer.repebble.com). Build all three for your server:

```bash
PEBBLE_HEALTH_URL=https://pebble-health.my.lan ./deploy/build-watch-apps.sh
```

The `.pbw` files land in `downloads/`, and the dashboard offers them under **More → Watch apps**.
Install them through your Pebble companion app (e.g. Gravel), then open each app's settings to pair
it. Pairing must happen on a network that can reach your server.

## Tests

From the repository root:

```bash
python3 -m pytest -q pebblefit/tests
node pebblefit/tests/test_hrv_transport.js
node pebblefit/tests/test_history_transport.js
node pebblefit/tests/test_today_transport.js
```

## Limitations

- Single wearer per server.
- The Pebble keeps sleep sessions only for the latest night, so sleep regularity builds up from the
  daily exports.
- No SpO2: firmware 4.41 doesn't expose it to apps.
- Scores are personal and relative. They are not comparable with Garmin, Polar or other devices.

## License

MIT, see [LICENSE](LICENSE).
