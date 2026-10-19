import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "bridge"))
import recovery as r

TZ = r.TZ
TODAY = date(2026, 11, 30)


def at(day, hour):
    return datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour).timestamp()


def q(rmssd, rr=1300, context="scheduled", resp=None):
    return {"context": context, "status": "experimental", "experimental_rmssd_ms": rmssd, "mean_rr_ms": rr,
            "metric": "rmssd_1hz_tachogram_reconstructed",
            "respiration": {"breaths_per_min": resp, "reliable": True} if resp else None}


def nights(values, end=TODAY):
    start = end - timedelta(days=len(values) - 1)
    out = []
    for i, v in enumerate(values):
        d = start + timedelta(days=i)
        out += [(at(d, 2), q(v)), (at(d, 3), q(v))]
    return out


def test_night_grouping_uses_morning_date_and_geometric_mean():
    out = r.nightly_hrv([(at(TODAY, 2), q(40, resp=14)), (at(TODAY, 3), q(90, resp=16)),
                         (at(TODAY, 3), q(70, context="manual"))])
    assert len(out) == 1 and out[0]["night"] == TODAY.isoformat()
    assert out[0]["rmssd_ms"] == 60.0 and out[0]["sessions"] == 2
    assert out[0]["breaths_per_min"] == 15.0
    assert r._night_of(at(TODAY - timedelta(days=1), 23)) == TODAY


def test_status_builds_then_balanced_then_low():
    assert r.hrv_status(r.nightly_hrv(nights([60] * 10)), TODAY)["status"] == "baseline_building"
    wobble = [55, 65] * 14
    assert r.hrv_status(r.nightly_hrv(nights(wobble + [60] * 7)), TODAY)["status"] == "balanced"
    assert r.hrv_status(r.nightly_hrv(nights(wobble + [40] * 7)), TODAY)["status"] == "low"
    assert r.hrv_status(r.nightly_hrv(nights(wobble + [80] * 7)), TODAY)["status"] == "unbalanced_high"


def test_status_needs_three_nights_this_week():
    data = nights([55, 65] * 14 + [60, 60], end=TODAY - timedelta(days=5))
    assert r.hrv_status(r.nightly_hrv(data), TODAY)["status"] == "insufficient"


def test_sleep_sessions_preferred_over_daily_total_and_regularity_scored():
    sessions, totals = [], {}
    for i in range(7):
        d = TODAY - timedelta(days=i)
        onset = at(d - timedelta(days=1), 23)
        sessions += [{"start": onset, "seconds": 8 * 3600, "kind": "sleep"},
                     {"start": onset + 3600, "seconds": 2 * 3600, "kind": "restful_sleep"}]
        totals[d.isoformat()] = (1, 1)
    sn = r.nightly_sleep(sessions, totals)
    assert all(n["source"] == "sessions" for n in sn)
    score = r.sleep_score(sn, TODAY)
    assert score["status"] == "experimental" and score["score"] == 100 and score["regularity_sd_min"] == 0


def test_sleep_falls_back_to_daily_total_without_timing():
    sn = r.nightly_sleep([], {TODAY.isoformat(): (6 * 3600, 5 * 3600)})
    score = r.sleep_score(sn, TODAY)
    assert score["status"] == "duration_only" and score["score"] == 50
    assert sn[0]["restful_s"] == 5 * 3600


def test_readiness_lists_missing_inputs_and_needs_hrv_or_sleep():
    sleep = r.sleep_score(r.nightly_sleep([], {TODAY.isoformat(): (8 * 3600, 0)}), TODAY)
    out = r.readiness([], sleep, None, TODAY)
    assert out["score"] is None and out["status"] == "insufficient"
    load = {"status": "ready", "acute": 50.0, "chronic": 50.0}
    out = r.readiness([], sleep, load, TODAY)
    assert out["status"] == "experimental" and out["score"] == round((0.25 * 100 + 0.25 * 50) / 0.5)
    assert any(m.startswith("hrv") for m in out["missing"])


def test_readiness_hrv_component_vs_baseline():
    n = r.nightly_hrv(nights([55, 65] * 14 + [60]))
    sleep = r.sleep_score(r.nightly_sleep([], {TODAY.isoformat(): (8 * 3600, 0)}), TODAY)
    out = r.readiness(n, sleep, None, TODAY)
    assert 45 <= out["components"]["hrv"]["score"] <= 55
    assert "resting_hr" in out["components"]


def test_afternoon_nap_never_joins_previous_night():
    y = TODAY - timedelta(days=1)
    sn = r.nightly_sleep([{"start": at(y, 23), "seconds": 3 * 3600, "kind": "sleep"},
                          {"start": at(TODAY, 15), "seconds": 3600, "kind": "sleep"}], {})
    assert len(sn) == 1 and sn[0]["sleep_s"] == 3 * 3600 and sn[0]["wake"] == at(TODAY, 2)


def test_partial_export_loses_to_larger_daily_total():
    y = TODAY - timedelta(days=1)
    sn = r.nightly_sleep([{"start": at(y, 23), "seconds": 3 * 3600, "kind": "sleep"}],
                         {TODAY.isoformat(): (8 * 3600, 4 * 3600)})
    assert sn[0]["source"] == "daily_total" and sn[0]["sleep_s"] == 8 * 3600 and sn[0]["onset"] is None


def test_overlapping_sessions_are_merged():
    y = TODAY - timedelta(days=1)
    sn = r.nightly_sleep([{"start": at(y, 23), "seconds": 3 * 3600, "kind": "sleep"},
                          {"start": at(y, 23) + 3600, "seconds": 3 * 3600, "kind": "sleep"}], {})
    assert sn[0]["sleep_s"] == 4 * 3600


def test_training_state_uses_latest_confirmed_day_not_today():
    days = [{"date": (TODAY - timedelta(days=i)).isoformat(), "complete": i > 0, "acute": 40.0, "chronic": 50.0}
            for i in range(44, -1, -1)]
    t = r.training_state(days)
    assert t["date"] == (TODAY - timedelta(days=1)).isoformat() and t["status"] == "ready"
    days[10]["complete"] = False
    assert r.training_state(days)["status"] == "warming"


SEED = {"source": "Garmin", "hrv_ln_sd": 0.2, "rhr_sd": 3.0}


def test_provisional_status_needs_seven_nights_and_a_seed():
    six = r.nightly_hrv(nights([60] * 6))
    assert r.hrv_status(six, TODAY, SEED)["status"] == "baseline_building"
    seven = r.nightly_hrv(nights([60] * 4 + [40] * 3))
    assert r.hrv_status(seven, TODAY)["status"] == "baseline_building"
    out = r.hrv_status(seven, TODAY, SEED)
    assert out["provisional"] is True and out["status"] in ("unbalanced_low", "low")
    assert out["reason"].startswith("Provisional") and out["weekly_rmssd_ms"] == 40.0
    assert r.hrv_status(r.nightly_hrv(nights([60] * 7)), TODAY, SEED)["status"] == "balanced"


def test_full_baseline_replaces_provisional():
    out = r.hrv_status(r.nightly_hrv(nights([55, 65] * 14 + [60] * 7)), TODAY, SEED)
    assert out["provisional"] is False and out["status"] == "balanced"


def test_readiness_uses_seed_from_fifth_night():
    n = r.nightly_hrv(nights([60] * 4 + [50]))
    sleep = r.sleep_score(r.nightly_sleep([], {TODAY.isoformat(): (8 * 3600, 0)}), TODAY)
    assert "hrv" not in r.readiness(n, sleep, None, TODAY)["components"]
    out = r.readiness(n, sleep, None, TODAY, SEED)
    assert out["components"]["hrv"]["provisional"] is True
    assert out["components"]["hrv"]["score"] < 50 and out["status"] == "experimental"


def test_near_complete_sessions_keep_timing_with_daily_total():
    y = TODAY - timedelta(days=1)
    sn = r.nightly_sleep([{"start": at(y, 23), "seconds": 7 * 3600, "kind": "sleep"}],
                         {TODAY.isoformat(): (int(7.5 * 3600), 0)})
    assert sn[0]["source"] == "daily_total" and sn[0]["sleep_s"] == int(7.5 * 3600)
    assert sn[0]["onset"] == at(y, 23) and sn[0]["wake"] == at(TODAY, 6)
