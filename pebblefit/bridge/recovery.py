"""Nightly recovery: HRV status, resting HR, breathing, sleep score, readiness.

Experimental, transparent personal models.  Every output says which inputs it
used and which were missing; nothing is filled in.  Baselines only ever use
earlier nights, never the night being judged.
"""
from __future__ import annotations

import os

import math
from datetime import date, datetime, timedelta
from statistics import mean, median, pstdev
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("PEBBLE_TZ", "UTC"))  # the wearer's local day boundaries

# HRV status, modelled on the published 7-day-average-vs-personal-range idea.
BASELINE_NIGHTS = 28          # look-back for the personal range
MIN_BASELINE_NIGHTS = 14      # before this, status is "baseline_building"
WEEK_NIGHTS_REQUIRED = 3      # nights in the last 7 needed for a weekly average
BAND_SD = 0.5                 # balanced band half-width, in baseline SDs
LOW_SD = 1.5                  # "low" below this many SDs
MIN_BAND = 0.04               # ln-units floor so a very stable baseline isn't hair-trigger

# A night runs from 18:00 to 14:00 the next day and is named by its morning.
NIGHT_START_HOUR = 18
NIGHT_END_HOUR = 14
SLEEP_NEED_S = 8 * 3600

# Provisional baselines: until the Pebble has its own 14 nights, the *spread*
# (night-to-night SD) can come from an earlier device's history, while the
# *level* always comes from Pebble nights.  Levels are device-specific;
# spread is mostly physiology and transfers far better.
SEED_MIN_NIGHTS = 4           # earlier Pebble nights needed for a provisional level
PROVISIONAL_RECENT = 3        # recent nights compared against that level

READINESS_WEIGHTS = {"hrv": 0.30, "resting_hr": 0.20, "sleep": 0.25, "training_load": 0.25}


def _night_of(ts: float) -> date | None:
    """The morning date a timestamp's night belongs to; None for daytime
    (14:00-18:00), so an afternoon nap never joins the previous night."""
    local = datetime.fromtimestamp(ts, TZ)
    if local.hour >= NIGHT_START_HOUR:
        return local.date() + timedelta(days=1)
    return local.date() if local.hour < NIGHT_END_HOUR else None


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _round(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


# --------------------------------------------------------------------------
# Nights from overnight HRV sessions
# --------------------------------------------------------------------------

def nightly_hrv(analysed: list[tuple[float, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Combine usable scheduled sessions into one record per night.

    ``analysed`` holds (session ts, hrv.analyze_session output).  Night RMSSD is
    the geometric mean of its sessions (mean of ln), resting HR comes from the
    mean RR, breathing from the median of reliable session estimates.
    """
    by_night: dict[date, list[dict[str, Any]]] = {}
    for ts, q in analysed:
        if q.get("context") != "scheduled" or q.get("status") != "experimental" or not q.get("experimental_rmssd_ms"):
            continue
        night = _night_of(ts)
        if night is not None:
            by_night.setdefault(night, []).append(q)
    nights = []
    for night, qs in sorted(by_night.items()):
        rmssd = math.exp(mean(math.log(q["experimental_rmssd_ms"]) for q in qs))
        rrs = [q["mean_rr_ms"] for q in qs if q.get("mean_rr_ms")]
        if not rrs:
            continue
        rr = mean(rrs)
        resp = [q["respiration"]["breaths_per_min"] for q in qs if (q.get("respiration") or {}).get("reliable")]
        metrics = {q.get("metric") for q in qs}
        nights.append({"night": night.isoformat(), "sessions": len(qs), "rmssd_ms": _round(rmssd),
                       "ln_rmssd": round(math.log(rmssd), 4), "resting_hr": _round(60000.0 / rr),
                       "breaths_per_min": _round(median(resp)) if resp else None,
                       "metric": metrics.pop() if len(metrics) == 1 else "mixed"})
    return nights


def hrv_status(nights: list[dict[str, Any]], today: date, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    """7-night average of ln RMSSD against the prior personal range.

    Before 14 baseline nights exist, a seeded spread gives a provisional
    status: the last 3 nights against the earlier Pebble nights."""
    metric_nights = [n for n in nights if n["metric"] != "mixed"]
    if metric_nights:
        current_metric = metric_nights[-1]["metric"]
        metric_nights = [n for n in metric_nights if n["metric"] == current_metric]
    week_start = today - timedelta(days=6)
    week = [n for n in metric_nights if week_start <= date.fromisoformat(n["night"]) <= today]
    prior = [n for n in metric_nights
             if today - timedelta(days=6 + BASELINE_NIGHTS) <= date.fromisoformat(n["night"]) < week_start]
    out: dict[str, Any] = {"weekly_rmssd_ms": None, "baseline_low_ms": None, "baseline_high_ms": None,
                           "nights_this_week": len(week), "baseline_nights": len(prior),
                           "required_baseline_nights": MIN_BASELINE_NIGHTS,
                           "last_night_rmssd_ms": nights[-1]["rmssd_ms"] if nights and nights[-1]["night"] == today.isoformat() else None}
    if len(week) >= WEEK_NIGHTS_REQUIRED:
        out["weekly_rmssd_ms"] = _round(math.exp(mean(n["ln_rmssd"] for n in week)))
    out["provisional"] = False
    if len(prior) < MIN_BASELINE_NIGHTS:
        seed_sd = (seed or {}).get("hrv_ln_sd")
        window = [n for n in metric_nights if today - timedelta(days=BASELINE_NIGHTS) <= date.fromisoformat(n["night"]) <= today]
        recent = [n for n in window[-PROVISIONAL_RECENT:] if date.fromisoformat(n["night"]) >= week_start]
        earlier = window[:-PROVISIONAL_RECENT]
        if not seed_sd or len(recent) < PROVISIONAL_RECENT or len(earlier) < SEED_MIN_NIGHTS:
            have = len(window)
            if seed_sd:
                # Progress toward the provisional status, which comes first.
                out.update(baseline_nights=have, required_baseline_nights=SEED_MIN_NIGHTS + PROVISIONAL_RECENT,
                           full_baseline_nights=MIN_BASELINE_NIGHTS)
            out.update(status="baseline_building",
                       reason=(f"{have} Pebble nights; provisional status from {SEED_MIN_NIGHTS + PROVISIONAL_RECENT}, "
                               f"full status after {MIN_BASELINE_NIGHTS} baseline nights") if seed_sd else
                              f"{len(prior)} of {MIN_BASELINE_NIGHTS} earlier overnight measurements")
            return out
        mu, sd = mean(n["ln_rmssd"] for n in earlier), seed_sd
        half = max(BAND_SD * sd, MIN_BAND)
        out.update(provisional=True, baseline_nights=len(earlier), window_nights=len(recent),
                   weekly_rmssd_ms=_round(math.exp(mean(n["ln_rmssd"] for n in recent))),
                   baseline_low_ms=_round(math.exp(mu - half)), baseline_high_ms=_round(math.exp(mu + half)),
                   seed_source=(seed or {}).get("source"))
        status, reason = _classify(math.log(out["weekly_rmssd_ms"]), mu, sd, half, "Last 3 nights")
        out.update(status=status, reason=f"Provisional: {reason}; range spread from your {seed.get('source', 'earlier')} history")
        return out
    logs = [n["ln_rmssd"] for n in prior]
    mu, sd = mean(logs), pstdev(logs)
    half = max(BAND_SD * sd, MIN_BAND)
    out["baseline_low_ms"] = _round(math.exp(mu - half))
    out["baseline_high_ms"] = _round(math.exp(mu + half))
    if out["weekly_rmssd_ms"] is None:
        out.update(status="insufficient", reason=f"Only {len(week)} usable nights in the last 7; need {WEEK_NIGHTS_REQUIRED}")
        return out
    status, reason = _classify(math.log(out["weekly_rmssd_ms"]), mu, sd, half, "Weekly HRV")
    out.update(status=status, reason=reason)
    return out


def _classify(value: float, mu: float, sd: float, half: float, label: str) -> tuple[str, str]:
    if value < mu - max(LOW_SD * sd, 2 * MIN_BAND):
        return "low", f"{label} well below your usual range"
    if value < mu - half:
        return "unbalanced_low", f"{label} below your usual range"
    if value > mu + half:
        return "unbalanced_high", f"{label} above your usual range"
    return "balanced", f"{label} within your usual range"


# --------------------------------------------------------------------------
# Sleep
# --------------------------------------------------------------------------

def nightly_sleep(sessions: list[dict[str, Any]], snapshot_totals: dict[str, tuple[int, int]]) -> list[dict[str, Any]]:
    """One record per night from exported sleep sessions, else daily totals.

    ``sessions`` are sleep_sessions rows (start, seconds, kind).  The watch's
    restful sleep overlaps its sleep sessions, so restful is a subset.
    ``snapshot_totals`` maps local date -> (sleep_s, restful_s) from hourly
    watch snapshots, used when no session export covers a night (no timing).
    """
    intervals: dict[date, dict[str, list[tuple[float, float]]]] = {}
    for s in sessions:
        night = _night_of(s["start"])
        if night is None or s["kind"] not in ("sleep", "restful_sleep"):
            continue
        intervals.setdefault(night, {"sleep": [], "restful_sleep": []})[s["kind"]].append((s["start"], s["start"] + s["seconds"]))
    out = []
    for night in sorted(set(intervals) | {date.fromisoformat(d) for d in snapshot_totals}):
        sleep = _merge(intervals.get(night, {}).get("sleep", []))
        restful = _merge(intervals.get(night, {}).get("restful_sleep", []))
        sleep_s = int(sum(b - a for a, b in sleep))
        restful_s = int(sum(b - a for a, b in restful))
        snap_s, snap_restful = snapshot_totals.get(night.isoformat(), (0, 0))
        if sleep_s and sleep_s >= snap_s:
            out.append({"night": night.isoformat(), "source": "sessions", "sleep_s": sleep_s,
                        "restful_s": min(restful_s, sleep_s), "onset": sleep[0][0], "wake": sleep[-1][1]})
        elif snap_s > 0:
            # The 05:15 export often holds only the first part of a night; the
            # running daily total is then more complete, but has no timing.
            # Sessions covering most of the night still give usable timing.
            timed = bool(sleep) and sleep_s >= 0.85 * snap_s
            out.append({"night": night.isoformat(), "source": "daily_total", "sleep_s": snap_s,
                        "restful_s": min(snap_restful, snap_s),
                        "onset": sleep[0][0] if timed else None, "wake": sleep[-1][1] if timed else None})
    return out


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Union of intervals, so re-segmented or duplicate sessions never double count."""
    merged: list[list[float]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _midpoint_minutes(rec: dict[str, Any]) -> float | None:
    """Sleep midpoint as minutes after 18:00 local, so it never wraps."""
    if rec["onset"] is None or rec["wake"] is None:
        return None
    # Wall-clock minutes, so a daylight-saving night doesn't shift the midpoint.
    mid = datetime.fromtimestamp((rec["onset"] + rec["wake"]) / 2, TZ)
    return (mid.hour * 60 + mid.minute - NIGHT_START_HOUR * 60) % 1440


def sleep_score(nights: list[dict[str, Any]], today: date, need_s: int = SLEEP_NEED_S) -> dict[str, Any]:
    """Duration (60%) and timing regularity (40%).  Restful time is shown, not scored:
    the watch's 'restful' label is not a validated sleep stage."""
    last = next((n for n in nights if n["night"] == today.isoformat()), None)
    out: dict[str, Any] = {"night": today.isoformat(), "score": None, "sleep_s": None, "restful_s": None,
                           "need_s": need_s, "regularity_sd_min": None, "components": {}, "missing": []}
    if last is None:
        out.update(status="no_data", reason="No sleep recorded for last night yet")
        return out
    out["sleep_s"], out["restful_s"] = last["sleep_s"], last["restful_s"]
    # Full marks at need, nothing at half of it.
    duration = _clip(100.0 * (last["sleep_s"] - need_s / 2) / (need_s / 2))
    out["components"]["duration"] = round(duration)
    week = [n for n in nights if today - timedelta(days=6) <= date.fromisoformat(n["night"]) <= today]
    mids = [m for m in (_midpoint_minutes(n) for n in week) if m is not None]
    if len(mids) >= 3:
        sd = pstdev(mids)
        out["regularity_sd_min"] = round(sd)
        # Full marks within ±30 min, nothing at ±2 h.
        regularity = _clip(100.0 * (120.0 - sd) / 90.0)
        out["components"]["regularity"] = round(regularity)
        out["score"] = round(0.6 * duration + 0.4 * regularity)
        out.update(status="experimental", reason="Duration against your sleep need, and how regular your sleep timing was this week")
    else:
        out["missing"].append("regularity: needs sleep start/end times on 3 nights this week (History app 1.2 export)")
        out["score"] = round(duration)
        out.update(status="duration_only", reason="Duration only until sleep timing is available for 3 nights")
    return out


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

def _z(value: float, history: list[float], seed_sd: float | None = None) -> tuple[float | None, bool]:
    """(z-score, provisional).  Own spread from 14 nights; before that a
    seeded spread with at least 4 earlier Pebble nights for the level."""
    if len(history) >= MIN_BASELINE_NIGHTS:
        sd = pstdev(history)
        return ((value - mean(history)) / sd if sd > 0 else 0.0), False
    if seed_sd and len(history) >= SEED_MIN_NIGHTS:
        return (value - mean(history)) / seed_sd, True
    return None, False


def training_state(history: list[dict[str, Any]], chronic_days: int = 42) -> dict[str, Any] | None:
    """Load balance from the latest confirmed day, ready once a full chronic
    window of consecutive confirmed days precedes it.  Today is usually not
    confirmed in the morning, so it is never required."""
    complete = [h for h in history or [] if h.get("complete")]
    if not complete:
        return None
    last = complete[-1]
    run, day = 0, date.fromisoformat(last["date"])
    confirmed = {h["date"] for h in complete}
    while day.isoformat() in confirmed:
        run += 1
        day -= timedelta(days=1)
    return {"date": last["date"], "acute": last.get("acute"), "chronic": last.get("chronic"),
            "consecutive_days": run, "status": "ready" if run >= chronic_days else "warming"}


def readiness(hrv_nights: list[dict[str, Any]], sleep: dict[str, Any], training_latest: dict[str, Any] | None,
              today: date, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Weighted mean of available components; each is 0-100 with 50 = your normal."""
    components: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    last = hrv_nights[-1] if hrv_nights and hrv_nights[-1]["night"] == today.isoformat() else None
    prior = [n for n in hrv_nights if today - timedelta(days=BASELINE_NIGHTS) <= date.fromisoformat(n["night"]) < today]

    if last is None:
        missing.append("hrv: no usable overnight measurement last night")
        missing.append("resting_hr: no usable overnight measurement last night")
    else:
        same = [n["ln_rmssd"] for n in prior if n["metric"] == last["metric"]]
        seed = seed or {}
        need = SEED_MIN_NIGHTS if seed.get("hrv_ln_sd") else MIN_BASELINE_NIGHTS
        z, provisional = _z(last["ln_rmssd"], same, seed.get("hrv_ln_sd"))
        if z is None:
            missing.append(f"hrv: baseline building ({len(same)} of {need} nights)")
        else:
            components["hrv"] = {"score": round(_clip(50 + 20 * z)), "provisional": provisional,
                                 "detail": f"{last['rmssd_ms']} ms, {z:+.1f} SD vs usual" + (" (provisional)" if provisional else "")}
        rhrs = [n["resting_hr"] for n in prior]
        need = SEED_MIN_NIGHTS if seed.get("rhr_sd") else MIN_BASELINE_NIGHTS
        z, provisional = _z(last["resting_hr"], rhrs, seed.get("rhr_sd"))
        if z is None:
            missing.append(f"resting_hr: baseline building ({len(rhrs)} of {need} nights)")
        else:
            components["resting_hr"] = {"score": round(_clip(50 - 20 * z)), "provisional": provisional,
                                        "detail": f"{last['resting_hr']} bpm, {z:+.1f} SD vs usual" + (" (provisional)" if provisional else "")}

    if sleep.get("score") is None:
        missing.append("sleep: " + sleep.get("reason", "no data"))
    else:
        components["sleep"] = {"score": sleep["score"], "detail": f"{sleep['sleep_s'] / 3600:.1f} h"}

    t = training_latest or {}
    if t.get("status") == "ready" and t.get("chronic"):
        # Form relative to fitness: fresher than usual scores above 50.
        ratio = (t["chronic"] - t["acute"]) / t["chronic"]
        components["training_load"] = {"score": round(_clip(50 + 100 * ratio)),
                                       "detail": f"acute {t['acute']:.0f} vs chronic {t['chronic']:.0f}"}
    else:
        missing.append(f"training_load: needs 42 consecutive confirmed days ({t.get('consecutive_days', 0)} so far)")

    weight = sum(READINESS_WEIGHTS[k] for k in components)
    out: dict[str, Any] = {"components": components, "missing": missing, "weights": READINESS_WEIGHTS,
                           "method": "weighted mean of available components, 50 = your normal; experimental"}
    if len(components) < 2 or not ({"hrv", "sleep"} & set(components)):
        out.update(score=None, status="insufficient",
                   reason="Needs at least two inputs including overnight HRV or sleep")
        return out
    score = sum(READINESS_WEIGHTS[k] * c["score"] for k, c in components.items()) / weight
    out.update(score=round(score), status="experimental",
               reason="Partial: " + ", ".join(m.split(":")[0] for m in missing) + " missing" if missing else "All inputs available")
    return out


def build(analysed: list[tuple[float, dict[str, Any]]], sleep_sessions: list[dict[str, Any]],
          snapshot_totals: dict[str, tuple[int, int]], training_history: list[dict[str, Any]] | None,
          now: float, need_s: int = SLEEP_NEED_S, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    today = datetime.fromtimestamp(now, TZ).date()
    nights = nightly_hrv(analysed)
    sleep_nights = nightly_sleep(sleep_sessions, snapshot_totals)
    sleep = sleep_score(sleep_nights, today, need_s)
    return {"today": today.isoformat(), "nights": nights[-60:], "hrv_status": hrv_status(nights, today, seed), "seed": seed,
            "sleep": sleep, "sleep_nights": sleep_nights[-60:],
            "readiness": readiness(nights, sleep, training_state(training_history or []), today, seed)}
