import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
from pebblefit.bridge.hrv import analyze_history, analyze_session


def payload(n=100, motion=True, context="manual", step=1000):
    return {"ppi": [800] * n, "offsets_ms": list(range(0, n * step, step)), "motion_samples": n if motion else 0, "motion_total": 0, "context": context}


def test_warmup_is_excluded_and_valid_session_is_experimental():
    out = analyze_session(payload())
    assert out["status"] == "experimental"
    assert out["experimental_rmssd_ms"] == 0
    assert all(x["reason"] == "warmup" for x in out["cleaned_intervals"][:30])


def test_sparse_disconnected_intervals_never_report_rmssd():
    p = payload()
    p["ppi"] = [800 if i % 2 == 0 else 2500 for i in range(100)]
    out = analyze_session(p)
    assert out["experimental_rmssd_ms"] is None
    assert out["status"] == "insufficient"


def test_missing_motion_is_explicit_unknown():
    out = analyze_session(payload(motion=False))
    assert out["status"] == "motion_unknown"
    assert out["motion"]["available"] is False
    assert out["experimental_rmssd_ms"] is None


def test_batch_duplicate_timestamps_are_reported_but_not_invalidated():
    p = payload()
    p["offsets_ms"][40] = p["offsets_ms"][39]
    out = analyze_session(p)
    assert out["status"] == "experimental"
    assert out["arrival_batches"]["duplicate_offset_count"] == 1


def test_cadence_and_large_gaps_are_gated():
    assert analyze_session(payload(step=10000))["status"] == "insufficient"
    p = payload()
    p["offsets_ms"][70:] = [x + 60000 for x in p["offsets_ms"][70:]]
    assert analyze_session(p)["experimental_rmssd_ms"] is None


def test_history_stress_uses_fourteen_prior_same_context_days_only():
    base = 1700000000
    rows = [{"ts": base + day * 86400, "payload": payload(context="manual")} for day in range(14)]
    rows.append({"ts": base + 14 * 86400, "payload": payload(context="manual")})
    rows += [{"ts": base + day * 86400, "payload": payload(context="scheduled")} for day in range(14)]
    out = analyze_history(rows)
    assert out["stress"]["status"] == "experimental"
    assert out["stress"]["baseline_days"] == 14
    assert out["stress"]["experimental_score"] == 50.0


def test_history_contexts_and_local_day_bins_are_separate():
    rows = [{"ts": 1700000000 + day * 86400, "payload": payload(context="manual")} for day in range(14)]
    rows += [{"ts": 1700000000 + day * 86400, "payload": payload(context="scheduled")} for day in range(14)]
    out = analyze_history(rows)
    assert {p["context"] for p in out["points"]} == {"manual", "scheduled"}
    assert out["baseline_progress"]["manual"]["adequate_baseline_days"] == 14
    assert out["baseline_progress"]["scheduled"]["adequate_baseline_days"] == 14


def test_nonmonotonic_arrivals_are_not_usable():
    p=payload();p['offsets_ms']=list(reversed(p['offsets_ms']))
    assert analyze_session(p)['status']=='insufficient'
    assert analyze_session(p)['experimental_rmssd_ms'] is None


def test_flat_prior_distribution_still_distinguishes_higher_current():
    base=1700000000
    rows=[{'ts':base+d*86400,'payload':payload()} for d in range(14)]
    current=payload();current['ppi']=[790 if i%2 else 810 for i in range(100)]
    rows.append({'ts':base+14*86400,'payload':current})
    result=analyze_history(rows)['stress']
    assert result['status']=='experimental' and result['experimental_score']==0

def tachogram(rr_fn, seconds=180, motion=True, context="manual", jitter=True):
    """Firmware 4.41 shape: current RR emitted once per second, FIFO-jittered."""
    offsets = [6000 + i * 997 + ((i * 37) % 80 - 40 if jitter else 0) for i in range(seconds)]
    ppi = [int(round(rr_fn(o / 1000.0))) for o in offsets]
    return {"ppi": ppi, "offsets_ms": offsets, "motion_samples": 4000 if motion else 0, "motion_total": 100,
            "context": context, "stream_semantics": "latest_ppi_updates"}

def test_latest_ppi_updates_are_analysed_as_tachogram_not_beats():
    import math
    out = analyze_session(tachogram(lambda t: 1350 + 40 * math.sin(2 * math.pi * 0.25 * t)))
    assert out["stream"] == "tachogram_1hz"
    assert out["metric"] == "rmssd_1hz_tachogram_reconstructed"
    assert out["status"] == "experimental"
    assert "1hz_tachogram_beats_reconstructed" in out["reasons"]
    assert 990 <= out["sample_period_ms"] <= 1000
    # Reconstructed beats are spaced like the heart (~1.35 s), so there are
    # fewer beat pairs than 1 Hz samples.
    assert out["consecutive_pairs"] < out["retained_count"] - 1
    assert out["experimental_rmssd_ms"] > 10

def test_tachogram_detected_without_semantics_flag_when_sum_exceeds_time():
    p = tachogram(lambda t: 1300)
    del p["stream_semantics"]
    out = analyze_session(p)
    assert out["stream"] == "tachogram_1hz"
    assert out["status"] == "experimental"
    assert out["experimental_rmssd_ms"] == 0

def test_tachogram_rmssd_tracks_reference_beat_rmssd():
    import math
    # Respiratory sinus arrhythmia at 0.25 Hz around 1.1 s RR.  Build the
    # true beat series, its RMSSD, and the 1 Hz instantaneous-RR samples.
    rr = lambda t: 1100 + 50 * math.sin(2 * math.pi * 0.25 * t)
    beats, t = [], 36.0
    while t < 186:
        beats.append(rr(t)); t += rr(t) / 1000.0
    ref = (sum((b - a) ** 2 for a, b in zip(beats, beats[1:])) / (len(beats) - 1)) ** 0.5
    out = analyze_session(tachogram(rr, jitter=False))
    assert abs(out["experimental_rmssd_ms"] - ref) / ref < 0.15

def test_tachogram_irregular_clock_is_insufficient():
    p = tachogram(lambda t: 1300)
    p["offsets_ms"][100:] = [x + 4000 for x in p["offsets_ms"][100:]]
    out = analyze_session(p)
    assert out["status"] == "insufficient"
    assert "tachogram_clock_irregular" in out["reasons"]

def test_tachogram_does_not_interpolate_across_rejected_samples():
    p = tachogram(lambda t: 1300)
    p["ppi"][90] = 1900
    out = analyze_session(p)
    assert out["cleaned_intervals"][90]["reason"] == "abrupt_relative_change"
    assert out["experimental_rmssd_ms"] == 0

def test_baseline_never_mixes_beat_and_tachogram_metrics():
    base = 1700000000
    rows = [{"ts": base + d * 86400, "payload": payload()} for d in range(14)]
    rows.append({"ts": base + 14 * 86400, "payload": tachogram(lambda t: 1300)})
    stress = analyze_history(rows)["stress"]
    assert stress["baseline_days"] == 0 and stress["status"] == "baseline_building"

def test_inadequate_current_quality_is_not_presented_as_baseline_wait():
    result=analyze_history([{'ts':1792281606,'payload':payload(motion=False)}])
    assert result['stress']['status']=='quality_insufficient'

def test_raw_motion_is_retained_without_claiming_vibration_is_known():
    p=payload()
    p.update(motion_source='raw_accelerometer',vibration_status='unknown')
    result=analyze_session(p)
    assert result['motion']['available'] is True
    assert result['motion']['source']=='raw_accelerometer'
    assert result['motion']['vibration_status']=='unknown'
    # Recorded, but the collector never vibrates during capture, so it no
    # longer blocks every session.
    assert 'vibration_contamination_unknown' in result['reasons']
    assert result['status']=='experimental'
