"""Nightly AI briefing: a short plain-language overview of every dashboard page.

The language model never calculates.  ``facts()`` turns the dashboard summary
into compact per-page facts, already compared with the wearer's own history,
and the model only phrases them.  Every number in its answer is checked
against those facts; a briefing with invented numbers is retried once with
the offending values listed, and anything still ungrounded is dropped.

Configuration (environment):
  LLM_BASE_URL     OpenAI-compatible endpoint, e.g. http://llm.lan:8080/v1
  LLM_MODEL        model name or alias (default "currentmodel")
  LLM_API_KEY      optional bearer token
  BRIEFING_TIME    local time(s) for the daily run, comma separated (default
                   "05:00,05:40"). Later times in a day only run when new
                   watch data has arrived since the last briefing, e.g. the
                   05:15 history export with last night's sleep sessions.

Run ``python3 briefing.py --daemon`` as its own service, or ``--once``.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any
from urllib.request import Request, urlopen

import fitness

log = logging.getLogger("pebble-health.briefing")
PAGES = ("today", "sleep", "recovery", "training", "more")
_running = threading.Lock()


def configured() -> bool:
    return bool(os.environ.get("LLM_BASE_URL"))


# --------------------------------------------------------------------------
# Facts: every number the model may use, computed here
# --------------------------------------------------------------------------

def _r(x, nd=1):
    return None if x is None else round(float(x), nd)


def _hm(seconds):
    if seconds is None:
        return None
    m = int(round(seconds / 60))
    return f"{m // 60}h {m % 60:02d}m"


def _version(code):
    return f"{code // 100}.{(code // 10) % 10}.{code % 10}" if code else None


def _clock(ts, tz):
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M") if ts else None


def _avg(values):
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def _spearman(pairs):
    """Rank correlation; None below 7 pairs."""
    if len(pairs) < 7:
        return None
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    xs, ys = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return round(num / den, 2) if den else None


def facts(s: dict[str, Any], tz) -> dict[str, Any]:
    now = datetime.fromtimestamp(s["now"], tz)
    today = now.date()
    rc = s.get("recovery") or {}
    nights = rc.get("nights") or []
    sleep_nights = rc.get("sleep_nights") or []
    fit = s.get("fitness") or {}
    weight = s.get("weight") or {}
    status = (s.get("latest") or {}).get("status") or {}

    # Daily steps from minute records (excluding the hourly snapshots).
    steps_by_day: dict[str, int] = {}
    for o in s.get("observations") or []:
        if not o.get("snapshot") and o.get("steps"):
            d = datetime.fromtimestamp(o["ts"], tz).date().isoformat()
            steps_by_day[d] = steps_by_day.get(d, 0) + o["steps"]
    last7 = [(today - timedelta(days=i)).isoformat() for i in range(1, 8)]

    sleep_recent = [n for n in sleep_nights if date.fromisoformat(n["night"]) > today - timedelta(days=7)]
    last_sleep = next((n for n in sleep_nights if n["night"] == today.isoformat()), None)
    hrv_recent = [n for n in nights if date.fromisoformat(n["night"]) > today - timedelta(days=7)]
    last_hrv = next((n for n in nights if n["night"] == today.isoformat()), None)
    hs, sl, ready = rc.get("hrv_status") or {}, rc.get("sleep") or {}, rc.get("readiness") or {}

    # Cross-page links, only reported when there are enough paired days.
    by_sleep = {n["night"]: n["sleep_s"] / 3600 for n in sleep_nights}
    sleep_vs_hrv = _spearman([(by_sleep[n["night"]], n["rmssd_ms"]) for n in nights if n["night"] in by_sleep])

    collector = s.get("collector") or {}
    night_sessions_today = (last_hrv or {}).get("sessions", 0)
    it = fit.get("intensity") or {}
    vo2 = fit.get("vo2max") or {}
    t = (s.get("training") or {}).get("latest") or {}
    unconfirmed = [h["date"] for h in (s.get("history") or [])[-7:] if not h.get("complete")]

    return {
        "date": today.isoformat(), "weekday": now.strftime("%A"), "time": now.strftime("%H:%M"),
        "name": s.get("wearer") or None,
        "today": {
            "readiness_score": ready.get("score"), "readiness_status": ready.get("status"),
            "readiness_inputs": {k: v.get("score") for k, v in (ready.get("components") or {}).items()},
            "readiness_missing": ready.get("missing"),
            "steps_so_far_today": status.get("steps"), "steps_daily_avg_previous_7_days": _r(_avg(steps_by_day.get(d) for d in last7), 0),
            "watch_battery_percent": status.get("battery_percent"),
            "weight_latest_weigh_in_kg": (weight.get("latest") or {}).get("kg"),
            "weight_7_day_average_kg": (weight.get("latest") or {}).get("trend_kg"),
            "weight_7_day_average_change_over_last_week_kg": weight.get("change_7d_kg"),
            "weight_7_day_average_change_over_last_4_weeks_kg": weight.get("change_28d_kg"),
            "intensity_minutes_this_week": it.get("this_week"), "intensity_goal": it.get("goal"),
            "days_left_this_week": 6 - today.weekday(),
        },
        "sleep": {
            "last_night_duration": _hm(last_sleep["sleep_s"]) if last_sleep else None,
            "last_night_restful_duration": _hm(last_sleep["restful_s"]) if last_sleep else None,
            "last_night_bedtime": _clock((last_sleep or {}).get("onset"), tz),
            "last_night_wake": _clock((last_sleep or {}).get("wake"), tz),
            "sleep_score": sl.get("score"), "score_status": sl.get("status"), "need_hours": _r((sl.get("need_s") or 0) / 3600),
            "regularity_sd_minutes": sl.get("regularity_sd_min"),
            "average_duration_previous_nights": _hm(_avg(n["sleep_s"] for n in sleep_recent if n["night"] != today.isoformat())),
            "previous_nights_in_average": sum(1 for n in sleep_recent if n["night"] != today.isoformat()),
            "nights_recorded_last_7": len(sleep_recent),
            "sleep_debt_last_7_nights": _hm(sum(max(0, (sl.get("need_s") or 28800) - n["sleep_s"]) for n in sleep_recent)),
            "nights": [{"night": n["night"], "duration": _hm(n["sleep_s"]), "bedtime": _clock(n.get("onset"), tz),
                        "wake": _clock(n.get("wake"), tz)} for n in sleep_recent],
        },
        "recovery": {
            "hrv_last_night_ms": (last_hrv or {}).get("rmssd_ms"),
            "hrv_avg_previous_nights_ms": _r(_avg(n["rmssd_ms"] for n in hrv_recent if n["night"] != today.isoformat())),
            "hrv_previous_nights_in_avg": sum(1 for n in hrv_recent if n["night"] != today.isoformat()),
            "hrv_status": hs.get("status"), "hrv_status_provisional": hs.get("provisional"),
            "hrv_usual_range_ms": [hs.get("baseline_low_ms"), hs.get("baseline_high_ms")] if hs.get("baseline_low_ms") else None,
            "hrv_nights_recorded": hs.get("baseline_nights"), "hrv_nights_needed": hs.get("required_baseline_nights"),
            "pulse_during_night_recording_bpm": (last_hrv or {}).get("resting_hr"),
            "resting_pulse_night_minimum_bpm": fit.get("resting_hr"),
            "breathing_per_min": (last_hrv or {}).get("breaths_per_min"),
            "night_recordings_last_night": night_sessions_today, "night_recordings_planned": 3,
            "stress_status": (s.get("stress") or {}).get("status"),
            "nights": [{"night": n["night"], "hrv_ms": n["rmssd_ms"], "pulse_bpm": n["resting_hr"], "breathing": n["breaths_per_min"]} for n in hrv_recent],
            "link_sleep_hours_vs_hrv_rank_correlation": sleep_vs_hrv,
        },
        "training": {
            "vo2max_estimate": vo2.get("value"), "vo2max_method": vo2.get("method"), "bmi": (vo2.get("inputs") or {}).get("bmi"),
            "activity_rating_0_7": (vo2.get("inputs") or {}).get("pa_rating"),
            "intensity_minutes_last_7_days": it.get("last_7_days"), "intensity_minutes_this_week": it.get("this_week"),
            "intensity_goal_per_week": it.get("goal"),
            "load_acute": t.get("acute"), "load_chronic": t.get("chronic"), "load_status": t.get("status"),
            "workouts_last_7_days": sum(1 for w in s.get("workouts") or [] if w.get("start", 0) >= s["now"] - 7 * 86400),
            "unconfirmed_days_last_7": len(unconfirmed),
            "max_hr_setting": fit.get("hr_max"),
        },
        "more": {
            "hours_since_last_upload": _r((s.get("sync_age_seconds") or 0) / 3600, 1),
            "watch_battery_percent": status.get("battery_percent"),
            "hrv_app_version": _version(collector.get("collector_version")),
            "night_slots_armed": collector.get("schedule_ok"),
            "next_night_recording": datetime.fromtimestamp(collector["next_wakeup"], tz).strftime("%a %H:%M") if collector.get("next_wakeup") else None,
            "night_recordings_last_night": night_sessions_today, "night_recordings_planned": 3,
            "minute_records_total": s.get("minute_records"),
        },
    }


# --------------------------------------------------------------------------
# Prompt and grounding check
# --------------------------------------------------------------------------

SYSTEM = """You write the morning briefing for a personal health dashboard fed by a Pebble smartwatch.
Write for one person, in plain, warm, direct English, like a good coach: no hype, no filler.
Rules:
- Use ONLY the facts given. Every number you write must appear in the facts (you may round it). Never calculate new numbers.
- Missing (null) values mean "not measured yet": say so briefly when it matters, never guess.
- Scores here are experimental personal estimates, not medical measurements. Never diagnose or give medical advice.
- Prefer comparisons with the person's own averages over absolute judgements.
- The "focus" is one practical, specific suggestion for today, grounded in the facts.
- Lead with what changed or matters most and why; do not list every number. Mention a missing value at most once per page.
- Say each point once: the headline, summary, highlights and focus must not repeat each other.
- An average built from fewer than 3 earlier values is not a baseline: call the value "the first reading" or similar instead of comparing.
- Interpret, do not just restate: e.g. a short night after a long one, a debt building up, a recording that failed and needs attention.
- Use tones honestly and decisively: "good" for better than usual or on track; "watch" for clearly worse than usual, a goal at risk or something to fix (e.g. 2+ hours of sleep debt, a missed night recording, a stale upload); "neutral" only for plain context.
- Write durations as given (e.g. "5h 41m"), never as decimal hours.
- Keep it short: headline <= 12 words, summary <= 2 sentences, 2-4 highlights of <= 20 words each.
Return JSON exactly matching the schema."""

PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "tone": {"type": "string", "enum": ["good", "neutral", "watch"]}},
            "required": ["text", "tone"]}},
        "focus": {"type": "string"},
        "data_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "highlights", "focus", "data_notes"],
}
SCHEMA = {"type": "object", "properties": {p: PAGE_SCHEMA for p in PAGES}, "required": list(PAGES)}

PAGE_GUIDE = {
    "today": "The big picture for the day: readiness, last night, activity so far, weight trend, intensity goal.",
    "sleep": "Last night against the person's own recent nights, timing and regularity, sleep debt.",
    "recovery": "Overnight HRV, pulse and breathing against recent nights; baseline progress; any sleep/HRV link.",
    "training": "Intensity minutes against the weekly goal, training load status, VO2max estimate and what moves it.",
    "more": "Is the setup healthy: uploads, battery, night recordings arriving, anything the person should fix.",
}

_NUM = re.compile(r"(?<![\w.])[-−+]?\d+(?:[.,]\d+)?")


def _numbers(obj) -> list[float]:
    out = []
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        return [float(m.replace("−", "-").replace(",", ".")) for m in _NUM.findall(obj)]
    if isinstance(obj, dict):
        for v in obj.values():
            out += _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _numbers(v)
    return out


def ungrounded(text: str, allowed: set[float]) -> list[str]:
    """Numbers in ``text`` that don't match a fact (allowing rounding)."""
    bad = []
    for m in _NUM.findall(text):
        v = abs(float(m.replace("−", "-").replace(",", ".")))
        if v <= 10 and float(v).is_integer():
            continue  # small counts and ordinals ("2 nights", "1 of 3") are fine
        tol = max(0.51, abs(v) * 0.02)
        if not any(abs(v - abs(a)) <= tol for a in allowed):
            bad.append(m)
    return bad


def _allowed(f: dict[str, Any]) -> set[float]:
    nums = set(_numbers(f))
    nums |= {round(n * 60) for n in list(nums) if 0 < n < 24}  # hours shown as minutes
    for t in re.findall(r"\b(\d{1,2}):(\d{2})\b", json.dumps(f)):
        nums |= {float(t[0]), float(t[1])}  # clock times
    return nums


def _chat(messages: list[dict[str, str]]) -> tuple[dict[str, Any], str]:
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = {"model": os.environ.get("LLM_MODEL", "currentmodel"), "messages": messages, "temperature": 0.4,
            # Reasoning counts against the output budget, and the model is much
            # better with it on; this runs once a night, so be generous.
            "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "32000")), "response_format": {"type": "json_object", "schema": SCHEMA}}
    headers = {"Content-Type": "application/json"}
    if os.environ.get("LLM_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["LLM_API_KEY"]
    with urlopen(Request(base + "/chat/completions", json.dumps(body).encode(), headers), timeout=1800) as r:
        reply = json.load(r)
    content = reply["choices"][0]["message"]["content"] or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    content = re.sub(r"^```(?:json)?|```$", "", content).strip()
    return json.loads(content), _resolve(base, headers, reply.get("model") or body["model"])


def _resolve(base: str, headers: dict[str, str], name: str) -> str:
    """Report the real model behind an alias such as "currentmodel"."""
    try:
        with urlopen(Request(base + "/models", headers=headers), timeout=10) as r:
            for m in json.load(r).get("data", []):
                if m.get("id") == name and m.get("alias_of"):
                    return m["alias_of"]
    except Exception:
        pass
    return name


class EmptyBriefing(ValueError):
    pass


def _normalise(result: dict[str, Any]) -> dict[str, Any]:
    """Servers don't always enforce the schema: unwrap one nesting level and
    accept plain-string highlights (taking a page-level tone if given)."""
    if isinstance(result, dict) and not any(p in result for p in PAGES):
        inner = [v for v in result.values() if isinstance(v, dict) and any(p in v for p in PAGES)]
        if inner:
            result = inner[0]
    if not isinstance(result, dict):
        result = {}
    for page in PAGES:
        p = result.get(page) if isinstance(result.get(page), dict) else {}
        page_tone = p.get("tone") if p.get("tone") in ("good", "neutral", "watch") else "neutral"
        p["highlights"] = [h if isinstance(h, dict) else {"text": str(h), "tone": page_tone} for h in p.get("highlights") or []]
        for h in p["highlights"]:
            if h.get("tone") not in ("good", "neutral", "watch"):
                h["tone"] = "neutral"
        p["data_notes"] = [str(n) for n in p.get("data_notes") or []]
        for key in ("headline", "summary", "focus"):
            p[key] = str(p.get(key) or "")
        result[page] = p
    if not any(result[p]["headline"] or result[p]["summary"] for p in PAGES):
        raise EmptyBriefing("model returned no usable page content")
    return result


def _check(result: dict[str, Any], allowed: set[float]) -> dict[str, list[str]]:
    problems = {}
    for page in PAGES:
        p = result.get(page) or {}
        texts = [p.get("headline", ""), p.get("summary", ""), p.get("focus", "")] + \
                [h.get("text", "") for h in p.get("highlights", [])] + list(p.get("data_notes", []))
        bad = sorted({b for t in texts for b in ungrounded(t, allowed)})
        if bad:
            problems[page] = bad
    return problems


def _scrub(result: dict[str, Any], allowed: set[float]) -> dict[str, Any]:
    """Drop any remaining sentence-level item with an ungrounded number."""
    for page in PAGES:
        p = result.get(page) or {}
        p["highlights"] = [h for h in p.get("highlights", []) if not ungrounded(h.get("text", ""), allowed)][:4]
        p["data_notes"] = [n for n in p.get("data_notes", []) if not ungrounded(n, allowed)][:3]
        for key in ("headline", "summary", "focus"):
            if ungrounded(p.get(key, ""), allowed):
                p[key] = ""
        result[page] = p
    return result


def generate(summary: dict[str, Any], tz) -> dict[str, Any]:
    f = facts(summary, tz)
    allowed = _allowed(f)
    user = ("Page guide:\n" + json.dumps(PAGE_GUIDE, indent=1) +
            "\n\nFacts (null = not measured yet):\n" + json.dumps(f, indent=1) +
            "\n\nWrite the briefing for each page. Answer with exactly this JSON shape:\n" +
            json.dumps({p: {"headline": "...", "summary": "...", "highlights": [{"text": "...", "tone": "good|neutral|watch"}],
                            "focus": "...", "data_notes": ["..."]} for p in PAGES}))
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    try:
        result, model = _chat(messages)
        result = _normalise(result)
    except (EmptyBriefing, json.JSONDecodeError):
        result, model = _chat(messages)
        result = _normalise(result)
    problems = _check(result, allowed)
    retried = False
    if problems:
        retried = True
        messages += [{"role": "assistant", "content": json.dumps(result)},
                     {"role": "user", "content": "These numbers do not appear in the facts: " + json.dumps(problems) +
                      ". Rewrite the whole briefing using only numbers from the facts."}]
        result, model = _chat(messages)
        result = _normalise(result)
    remaining = _check(result, allowed)
    result = _scrub(result, allowed)
    return {"pages": {p: result.get(p) for p in PAGES}, "model": model, "facts": f,
            "grounding": {"retried": retried, "dropped_numbers": remaining}}


# --------------------------------------------------------------------------
# Storage, run and schedule
# --------------------------------------------------------------------------

def latest(db) -> dict[str, Any] | None:
    row = db.execute("SELECT created,day,model,payload FROM briefings WHERE status='ok' ORDER BY created DESC LIMIT 1").fetchone()
    if not row:
        return None
    payload = json.loads(row["payload"])
    return {"created": row["created"], "day": row["day"], "model": row["model"], "pages": payload["pages"],
            "grounding": payload.get("grounding")}


def run() -> str:
    """Generate and store one briefing; returns 'ok', 'busy', 'disabled' or 'error'."""
    if not configured():
        return "disabled"
    if not _running.acquire(blocking=False):
        return "busy"
    try:
        summary = fitness.summary(42)
        started = time.time()
        try:
            out = generate(summary, fitness.TZ)
            status, error = "ok", None
        except Exception as exc:
            log.exception("briefing failed")
            out, status, error = {"pages": None, "model": os.environ.get("LLM_MODEL"), "facts": None}, "error", str(exc)[:500]
        with fitness.connect() as db:
            db.execute("INSERT INTO briefings(created,day,model,status,payload,error,seconds) VALUES(?,?,?,?,?,?,?)",
                       (int(time.time()), datetime.now(fitness.TZ).date().isoformat(), out.get("model"), status,
                        json.dumps(out), error, round(time.time() - started, 1)))
        return status
    finally:
        _running.release()


def run_in_background() -> str:
    if not configured():
        return "disabled"
    if _running.locked():
        return "busy"
    threading.Thread(target=run, daemon=True).start()
    return "started"


def _times() -> list[tuple[int, int]]:
    out = []
    for part in os.environ.get("BRIEFING_TIME", "05:00,05:40").split(","):
        hh, mm = part.strip().split(":")
        out.append((int(hh), int(mm)))
    return sorted(out)


def _next_run(now: datetime) -> tuple[datetime, bool]:
    """Next scheduled time, and whether it is the first of its day."""
    times = _times()
    for i, (hh, mm) in enumerate(times):
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target > now:
            return target, i == 0
    hh, mm = times[0]
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=1), True


def new_data_since_last_briefing() -> bool:
    with fitness.connect() as db:
        last = db.execute("SELECT max(created) FROM briefings WHERE status='ok'").fetchone()[0] or 0
        newest = max(db.execute("SELECT coalesce(max(received),0) FROM reports").fetchone()[0],
                     db.execute("SELECT coalesce(max(received),0) FROM collector_reports").fetchone()[0])
    return newest > last


def daemon() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fitness.connect().close()
    while True:
        nxt, first = _next_run(datetime.now(fitness.TZ))
        log.info("next briefing at %s%s", nxt.isoformat(), "" if first else " (only if new data)")
        time.sleep(max(1, (nxt - datetime.now(fitness.TZ)).total_seconds()))
        if not first and not new_data_since_last_briefing():
            log.info("no new data since the last briefing; skipping")
            continue
        for attempt in range(3):
            status = run()
            log.info("briefing run: %s", status)
            if status != "error":
                break
            time.sleep(900)  # model server busy or down: retry in 15 minutes


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        daemon()
    else:
        print(run())
