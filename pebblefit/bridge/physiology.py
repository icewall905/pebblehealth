"""Fitness estimates: VO2max, fitness age, intensity minutes and energy.

All estimates are labelled as such.  Sources:
  - VO2max: Jackson et al. 1990 non-exercise model (BMI version):
      56.363 + 1.921*PA-R - 0.381*age - 0.754*BMI + 10.987*(male)
  - Fitness age: HUNT 3 Fitness Study reference means (Loe et al. 2013,
    PLoS ONE, Table 4), interpolated between age-group midpoints.
  - BMR: Mifflin-St Jeor.
  - Intensity: moderate >= 64 % and vigorous >= 77 % of max HR (ACSM),
    weekly goal 150 moderate-equivalent minutes (vigorous counts double).

Wrist heart rate on the Pebble can lock onto arm swing while walking, reading
the step cadence (or 1.5x/2x of it) instead of the pulse.  Such minutes are
excluded wherever heart rate is used.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

HUNT_MEANS = {  # age-group midpoint -> mean VO2peak, mL/kg/min
    "male": [(25, 54.0), (35, 48.6), (45, 46.6), (55, 41.9), (65, 38.4), (75, 34.2)],
    "female": [(25, 42.8), (35, 39.6), (45, 37.8), (55, 33.7), (65, 30.5), (75, 26.8)],
}
MODERATE, VIGOROUS = 0.64, 0.77
WEEKLY_GOAL = 150
LOCK_HARMONICS = (1.0, 1.5, 2.0)
LOCK_TOLERANCE = 8
MIN_WALK_STEPS = 60


def age_on(birthdate: date, day: date) -> int:
    return day.year - birthdate.year - ((day.month, day.day) < (birthdate.month, birthdate.day))


def cadence_locked(hr: float | None, steps: float | None) -> bool:
    """True when a walking minute's HR matches cadence or a harmonic of it."""
    if not hr or not steps or steps < MIN_WALK_STEPS:
        return False
    return any(abs(hr - steps * k) <= LOCK_TOLERANCE for k in LOCK_HARMONICS)


def intensity(minutes: list[tuple[int, int | None, int | None]], hr_max: float, tz, today: date) -> dict[str, Any]:
    """Moderate/vigorous minutes per day; a minute counts only inside a run of
    at least two qualifying minutes, so single spikes are ignored."""
    days: dict[str, dict[str, int]] = {}
    rejected = 0
    run: list[tuple[int, str]] = []

    def flush():
        if len(run) >= 2:
            for ts, kind in run:
                d = datetime.fromtimestamp(ts, tz).date().isoformat()
                days.setdefault(d, {"moderate": 0, "vigorous": 0})[kind] += 1
        run.clear()

    previous = None
    for ts, hr, steps in minutes:
        if cadence_locked(hr, steps):
            rejected += 1
            kind = None
        elif hr and hr >= VIGOROUS * hr_max:
            kind = "vigorous"
        elif hr and hr >= MODERATE * hr_max:
            kind = "moderate"
        else:
            kind = None
        if kind is None or (previous is not None and ts - previous != 60):
            flush()
        if kind:
            run.append((ts, kind))
        previous = ts
    flush()
    week_start = today - timedelta(days=today.weekday())
    def total(since: date) -> int:
        return sum(v["moderate"] + 2 * v["vigorous"] for d, v in days.items() if since <= date.fromisoformat(d) <= today)
    return {"days": [{"date": d, **v} for d, v in sorted(days.items())],
            "this_week": total(week_start), "last_7_days": total(today - timedelta(days=6)),
            "goal": WEEKLY_GOAL, "cadence_lock_rejected": rejected,
            "method": "moderate >=64% / vigorous >=77% of max HR, vigorous counts double, runs of 2+ minutes"}


def pa_rating(weekly_moderate: float, weekly_vigorous: float, daily_steps: float | None) -> int:
    """Jackson PA-R (0-7) from the last 4 weeks' average weekly minutes."""
    if weekly_vigorous >= 180:
        return 7
    if weekly_vigorous >= 60:
        return 6
    if weekly_vigorous >= 30:
        return 5
    if weekly_vigorous > 0:
        return 4
    if weekly_moderate > 60:
        return 3
    if weekly_moderate >= 10:
        return 2
    return 1 if (daily_steps or 0) >= 5000 else 0


def resting_hr(minutes: list[tuple[int, int | None, int | None]], tz) -> float | None:
    """5th percentile of night-time (00-06) minute HR: close to a true resting
    pulse, unlike the average of a short night recording."""
    night = sorted(hr for ts, hr, _ in minutes if hr and datetime.fromtimestamp(ts, tz).hour < 6)
    return float(night[int(len(night) * 0.05)]) if len(night) >= 60 else None


def fitness_age(vo2: float, sex: str) -> float | None:
    curve = HUNT_MEANS.get(sex)
    if not curve:
        return None
    if vo2 >= curve[0][1]:
        return 20.0
    if vo2 <= curve[-1][1]:
        return 80.0
    for (a0, v0), (a1, v1) in zip(curve, curve[1:]):
        if v1 <= vo2 <= v0:
            return round(a0 + (v0 - vo2) / (v0 - v1) * (a1 - a0), 1)
    return None


def fitness(body: dict[str, Any] | None, weight_kg: float | None, hr_max: float | None,
            minutes: list[tuple[int, int | None, int | None]], snapshots: dict[str, int], tz, today: date) -> dict[str, Any]:
    """Assemble the fitness section; every field states what was missing."""
    missing = []
    body = body or {}
    sex, height = body.get("sex"), body.get("height_cm")
    birth = date.fromisoformat(body["birthdate"]) if body.get("birthdate") else None
    age = age_on(birth, today) if birth else None
    if sex not in ("male", "female"):
        missing.append("sex")
    if not age:
        missing.append("birth date")
    if not height:
        missing.append("height")
    if not weight_kg:
        missing.append("weight")
    out: dict[str, Any] = {"age": age, "sex": sex, "height_cm": height, "weight_kg": weight_kg,
                           "hr_max": hr_max}
    out["intensity"] = intensity(minutes, hr_max, tz, today) if hr_max else None
    out["resting_hr"] = resting_hr(minutes, tz)
    # Energy: Mifflin-St Jeor resting burn; active burn as the watch reports it.
    if not missing:
        bmr = 10 * weight_kg + 6.25 * height - 5 * age + (5 if sex == "male" else -161)
        out["energy"] = {"bmr_kcal": round(bmr), "active_kcal_today": snapshots.get(today.isoformat()),
                         "method": "resting: Mifflin-St Jeor; active: as reported by the watch"}
    else:
        out["energy"] = None
    # VO2max from the profile model; PA-R from the last 4 weeks of activity.
    if missing or not out["intensity"]:
        out["vo2max"] = None
        out["missing"] = missing + ([] if out["intensity"] else ["max heart rate"])
        return out
    since = today - timedelta(days=27)
    recent = [d for d in out["intensity"]["days"] if date.fromisoformat(d["date"]) >= since]
    weeks = 4.0
    par = pa_rating(sum(d["moderate"] for d in recent) / weeks, sum(d["vigorous"] for d in recent) / weeks, None)
    bmi = weight_kg / (height / 100) ** 2
    vo2 = 56.363 + 1.921 * par - 0.381 * age - 0.754 * bmi + 10.987 * (1 if sex == "male" else 0)
    out["vo2max"] = {"value": round(vo2, 1), "method": "profile estimate (Jackson et al. 1990)",
                     "inputs": {"age": age, "bmi": round(bmi, 1), "pa_rating": par},
                     # Hidden until a measured VO2max exists: a profile estimate is too
                     # uncertain to place against population norms.  fitness_age()
                     # stays available for that.
                     "fitness_age": None,
                     "walking_estimate": None,
                     "walking_note": "Not shown: wrist heart rate during walking often locks onto arm swing on the Pebble."}
    out["missing"] = missing
    return out
