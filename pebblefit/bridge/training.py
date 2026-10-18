import os
"""Bounded, conservative training-load analytics (stdlib only).

This module intentionally keeps recorded time and confirmed calendar days
separate.  Missing recordings are unknown rather than inferred rest.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import math

TZ = ZoneInfo(os.environ.get("PEBBLE_TZ", "UTC"))  # the wearer's local day boundaries
ACUTE_DAYS, CHRONIC_DAYS = 7, 42


def _ts(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.timestamp()
    raise ValueError("timestamp must be epoch seconds or ISO-8601")


def _day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _round(value):
    return round(float(value), 3)


def analyze(workouts, minutes, days, hr_max, load_method="hr", confirmations=None,
            initial_fitness=None):
    """Analyze bounded session records and confirmed local calendar days.

    ``load_method`` chooses the series used for ``load`` and fitness estimates;
    both HR and RPE loads are always returned independently.
    """
    if load_method not in ("hr", "rpe"):
        raise ValueError("load_method must be 'hr' or 'rpe'")
    if not isinstance(workouts, list) or not isinstance(minutes, list) or not isinstance(days, list):
        raise ValueError("workouts, minutes and days must be lists")
    if len(workouts) > 2000 or len(minutes) > 200000 or len(days) > 730:
        raise ValueError("input exceeds bounded analytics limits")
    if initial_fitness is not None:
        raise ValueError("initial_fitness is not supported")
    if hr_max is not None and (not isinstance(hr_max, (int, float)) or isinstance(hr_max, bool) or not 100 <= hr_max <= 260):
        raise ValueError("hr_max must be between 100 and 260")
    confirmations = confirmations or {}
    day_list = [_day(x) for x in days]
    if len(set(day_list)) != len(day_list):
        raise ValueError("days must be unique")
    day_list.sort()
    mins = []
    for row in minutes:
        if not isinstance(row, dict) or "ts" not in row:
            raise ValueError("minute records require ts")
        hr = row.get("hr")
        if hr is not None and (isinstance(hr, bool) or not isinstance(hr, (int, float)) or not 1 <= hr <= 300):
            raise ValueError("heart rate must be between 1 and 300")
        mins.append((_ts(row["ts"]), float(hr) if hr is not None else None))
    mins.sort()
    out = []
    for source in workouts:
        if not isinstance(source, dict):
            raise ValueError("workouts must be objects")
        try:
            start, seconds = _ts(source["start"]), float(source["seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("workout requires start and seconds") from exc
        if not 1 <= seconds <= 172800:
            raise ValueError("workout seconds out of bounds")
        rpe = source.get("rpe")
        if rpe is not None and (isinstance(rpe, bool) or not isinstance(rpe, (int, float)) or not 0 <= rpe <= 10):
            raise ValueError("rpe must be between 0 and 10")
        item = dict(source)
        item.update({"hr_load": None, "rpe_load": None, "load": None,
                     "hr_coverage": 0.0, "overlap": False, "excluded": bool(source.get("ignored", False))})
        item["_start"], item["_end"] = start, start + seconds
        buckets = {}
        for ts, hr in mins:
            if ts < start:
                continue
            if ts >= start + seconds:
                break
            if hr is not None:
                # A wall-clock minute contributes at most once to one session.
                buckets.setdefault(int(ts // 60), hr)
        observed_seconds = sum(min(item["_end"], bucket * 60 + 60) - max(item["_start"], bucket * 60)
                               for bucket in buckets)
        # Keep the unrounded ratio for the threshold; display rounding is cosmetic.
        coverage = min(1.0, observed_seconds / seconds)
        item["hr_coverage"] = _round(coverage)
        if hr_max is not None and coverage >= .8:
            item["hr_load"] = _round(sum((min(item["_end"], bucket * 60 + 60) - max(item["_start"], bucket * 60)) / 60
                                           * sum(hr / hr_max >= edge for edge in (.5, .6, .7, .8, .9))
                                           for bucket, hr in buckets.items()))
        if rpe is not None:
            item["rpe_load"] = _round(seconds / 60 * rpe)
        out.append(item)
    # Mark meaningful overlaps before calculating either series.
    for i, left in enumerate(out):
        if left.get("ignored"):
            continue
        for right in out[i + 1:]:
            if right.get("ignored"):
                continue
            if min(left["_end"], right["_end"]) - max(left["_start"], right["_start"]) > 0:
                left["overlap"] = right["overlap"] = True
                left["excluded"] = right["excluded"] = True
    for item in out:
        if not item["excluded"]:
            item["load"] = item["hr_load"] if load_method == "hr" else item["rpe_load"]
        item.pop("_start"); item.pop("_end")

    by_day = {d: [] for d in day_list}
    for item in out:
        d = datetime.fromtimestamp(_ts(item["start"]), TZ).date()
        if d in by_day:
            by_day[d].append(item)
    history = []
    for d in day_list:
        sessions = [w for w in by_day[d] if not w.get("ignored", False)]
        confirm = confirmations.get(d.isoformat())
        if confirm not in (None, "complete", "rest"):
            raise ValueError("confirmation must be complete or rest")
        scoreable = bool(sessions) and all(w["load"] is not None and not w["excluded"] for w in sessions)
        if confirm == "complete" and scoreable:
            status, load = "complete", _round(sum(w["load"] for w in sessions))
        elif confirm == "rest" and not sessions:
            status, load = "rest", 0.0
        elif sessions:
            status, load = "partial", None
        else:
            status, load = "unknown", None
        hr_load = _round(sum(w["hr_load"] for w in sessions)) if sessions and all(w["hr_load"] is not None and not w["excluded"] for w in sessions) else None
        rpe_load = _round(sum(w["rpe_load"] for w in sessions)) if sessions and all(w["rpe_load"] is not None and not w["excluded"] for w in sessions) else None
        history.append({"date": d.isoformat(), "load": load, "hr_load": hr_load if status == "complete" else None,
                        "rpe_load": rpe_load if status == "complete" else None, "complete": status in ("complete", "rest"),
                        "status": status, "acute": None, "chronic": None, "form": None,
                        "rolling7": None, "rolling7_coverage": 0})

    # EWMA only traverses a contiguous confirmed series; unknown starts a new run.
    acute, chronic, run, previous_date = None, None, 0, None
    for i, h in enumerate(history):
        current_date = date.fromisoformat(h["date"])
        if not h["complete"] or (previous_date is not None and current_date != previous_date + timedelta(days=1)):
            acute = chronic = None; run = 0
            if not h["complete"]:
                previous_date = current_date
                continue
        value = h["load"]
        acute = value if acute is None else acute + (value - acute) / ACUTE_DAYS
        chronic = value if chronic is None else chronic + (value - chronic) / CHRONIC_DAYS
        run += 1
        previous_date = current_date
        h["acute"], h["chronic"], h["form"] = _round(acute), _round(chronic), _round(chronic - acute)
        window_start = current_date - timedelta(days=6)
        known = [x["load"] for x in history if x["complete"] and window_start <= date.fromisoformat(x["date"]) <= current_date]
        h["rolling7"] = _round(sum(known)) if known else None
        h["rolling7_coverage"] = len(known)
    zones = [] if hr_max is None else [{"label": f"{lo}-{lo + 10}%", "lower": lo, "upper": lo + 10, "minutes": 0.0} for lo in (50, 60, 70, 80)]
    if hr_max is not None:
        zones.append({"label": "90%+", "lower": 90, "upper": None, "minutes": 0.0})
    for w in out:
        if w["excluded"] or w["hr_load"] is None:
            continue
        # Recount the observed minute buckets to populate transparent zone totals.
        start, end = _ts(w["start"]), _ts(w["start"]) + float(w["seconds"])
        seen = set()
        for ts, hr in mins:
            b = int(ts // 60)
            if start <= ts < end and hr is not None and b not in seen:
                seen.add(b); ratio = hr / hr_max * 100
                idx = 0 if ratio < 60 else 1 if ratio < 70 else 2 if ratio < 80 else 3 if ratio < 90 else 4
                if ratio >= 50:
                    zones[idx]["minutes"] += (min(end, b * 60 + 60) - max(start, b * 60)) / 60
    for z in zones: z["minutes"] = _round(z["minutes"])
    latest = history[-1] if history else {"load": None, "acute": None, "chronic": None, "form": None}
    latest_date = date.fromisoformat(latest["date"]) if history else None
    known = sum(1 for h in history if latest_date and h["complete"] and latest_date - timedelta(days=6) <= date.fromisoformat(h["date"]) <= latest_date)
    weeks = []
    if day_list:
        first_day, last_day = min(day_list), max(day_list)
        week_start = first_day - timedelta(days=first_day.weekday())
        while week_start <= last_day:
            week_end = week_start + timedelta(days=6)
            expected_start, expected_end = max(week_start, first_day), min(week_end, last_day)
            expected = (expected_end - expected_start).days + 1
            chunk = [h for h in history if expected_start <= date.fromisoformat(h["date"]) <= expected_end]
            confirmed = [h for h in chunk if h["complete"]]
            recorded = [h for h in chunk if h["status"] == "partial"]
            weeks.append({"start": week_start.isoformat(), "end": week_end.isoformat(),
                          "load": _round(sum(h["load"] for h in confirmed)) if confirmed else None,
                          "confirmed_days": len(confirmed), "partial_days": len(recorded),
                          "unknown_days": expected - len(confirmed) - len(recorded),
                          "status": "complete" if len(confirmed) == expected else "partial"})
            week_start += timedelta(days=7)
    return {"workouts": out, "history": history, "zones": zones,
            "weeks": weeks,
            "notes": ["Sessions crossing midnight are attributed to their local start date."],
            "latest": {"load7": latest.get("rolling7"), "known_days7": known,
                       "acute": latest.get("acute"), "chronic": latest.get("chronic"),
                       "form": latest.get("form"), "baseline_days": run,
                       "status": "warming" if run < CHRONIC_DAYS else "ready"}}
