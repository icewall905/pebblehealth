"""Quality diagnostics for raw Pebble pulse-to-pulse intervals.

This module deliberately produces experimental, unvalidated diagnostics.  PPI
arrival timestamps are not treated as beat timestamps: duplicate offsets can
be a normal batched transport artifact, and rejected samples are never
interpolated across.

Pebble firmware 4.41 (Goodix HRV library) does not emit one value per beat.
It emits the current RR interval on a fixed ~1 Hz clock, whatever the heart
rate: an instantaneous-RR tachogram.  Such sessions are detected and analysed
as a uniformly sampled tachogram: beats are reconstructed by stepping through
the interpolated RR curve within each artifact-free run, and RMSSD is taken
over those reconstructed beats.  Values are never interpolated across
rejected samples.
"""
from __future__ import annotations

import os

from collections import defaultdict
from datetime import datetime
from statistics import median
import math
from typing import Any
from zoneinfo import ZoneInfo


MIN_PPI_MS = 300.0
MAX_PPI_MS = 2000.0
WARMUP_MS = 30_000.0
MIN_INTERVALS = 60
MIN_SPAN_MS = 60_000.0
MAX_ARRIVAL_GAP_MS = 5_000.0
# A tachogram stream arrives on a fixed clock; FIFO batching jitters single
# arrivals but not the long-run sample period.
TACHOGRAM_PERIOD_MS = (900.0, 1100.0)
TACHOGRAM_MAX_GAP_MS = 2_500.0
# More interval time than elapsed time cannot be a beat stream.
TACHOGRAM_MIN_SUM_RATIO = 1.05
METRIC_BEAT = "rmssd_beat_to_beat"
# Breathing modulates RR (respiratory sinus arrhythmia).  Awake and seated, a
# ~0.1 Hz blood-pressure rhythm often dominates instead, so a rate is only
# trusted overnight and when the peak clearly stands out.
RESP_BAND_HZ = (0.10, 0.50)
RESP_MIN_SAMPLES = 90
RESP_MIN_CONCENTRATION = 0.40
METRIC_TACHOGRAM = "rmssd_1hz_tachogram_reconstructed"
LOCAL_TZ = ZoneInfo(os.environ.get("PEBBLE_TZ", "UTC"))  # the wearer's local day boundaries


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def _context(payload: dict[str, Any]) -> str:
    return "scheduled" if payload.get("context") == "scheduled" or payload.get("scheduled") is True else "manual"


def _motion(payload: dict[str, Any]) -> dict[str, Any]:
    samples = payload.get("motion_samples", 0)
    total = payload.get("motion_total", 0)
    if not _finite_number(samples) or samples <= 0 or not _finite_number(total) or total < 0:
        return {"available": False, "samples": 0, "normalized_mean": None}
    return {"available": True, "samples": int(samples), "normalized_mean": round(float(total) / float(samples), 6), "source":payload.get("motion_source","processed_accelerometer"), "vibration_status":payload.get("vibration_status","unspecified")}


def _sample_period(offsets: list[Any]) -> float | None:
    """Least-squares arrival period per sample, robust to per-batch jitter."""
    if len(offsets) < 3 or not all(_finite_number(x) for x in offsets):
        return None
    n = len(offsets)
    mean_i = (n - 1) / 2.0
    mean_o = sum(float(x) for x in offsets) / n
    denom = sum((i - mean_i) ** 2 for i in range(n))
    return sum((i - mean_i) * (float(x) - mean_o) for i, x in enumerate(offsets)) / denom


def _interp(t: float, times: list[float], values: list[float]) -> float:
    """Catmull-Rom on the uniform clock.  Linear interpolation flattens
    respiratory (~0.25 Hz) oscillation sampled at 1 Hz and biases RMSSD low."""
    if t <= times[0]:
        return values[0]
    for k in range(1, len(times)):
        if t <= times[k]:
            w = (t - times[k - 1]) / (times[k] - times[k - 1])
            p0 = values[max(0, k - 2)]
            p1, p2 = values[k - 1], values[k]
            p3 = values[min(len(values) - 1, k + 1)]
            return 0.5 * (2 * p1 + (p2 - p0) * w + (2 * p0 - 5 * p1 + 4 * p2 - p3) * w * w
                          + (3 * p1 - p0 - 3 * p2 + p3) * w * w * w)
    return values[-1]


def _tachogram_pairs(run: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Adjacent reconstructed-beat RR pairs from one artifact-free run.

    ``run`` holds (sample_time_ms, rr_ms) on the uniform tachogram clock.  Each
    beat lasts the RR value interpolated at its start, so successive beats are
    spaced like the heart beat rather than like the 1 Hz output clock.
    """
    if len(run) < 2:
        return []
    times = [t for t, _ in run]
    values = [v for _, v in run]
    beats = []
    t = times[0]
    while t <= times[-1]:
        rr = min(MAX_PPI_MS, max(MIN_PPI_MS, _interp(t, times, values)))
        beats.append(rr)
        t += rr
    return list(zip(beats, beats[1:]))


def _respiration(values: list[float], period_ms: float, context: str) -> dict[str, Any] | None:
    """Breathing rate from the dominant RR oscillation of one uniform run."""
    n = len(values)
    if n < RESP_MIN_SAMPLES or not period_ms:
        return None
    mean_i = (n - 1) / 2.0
    mean_v = sum(values) / n
    slope = sum((i - mean_i) * (v - mean_v) for i, v in enumerate(values)) / sum((i - mean_i) ** 2 for i in range(n))
    x = [(v - mean_v - slope * (i - mean_i)) * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i, v in enumerate(values)]
    fs = 1000.0 / period_ms
    spectrum = []
    f = RESP_BAND_HZ[0]
    while f <= min(RESP_BAND_HZ[1], fs / 2) + 1e-9:
        w = 2 * math.pi * f / fs
        re = sum(v * math.cos(w * i) for i, v in enumerate(x))
        im = sum(v * math.sin(w * i) for i, v in enumerate(x))
        spectrum.append((f, re * re + im * im))
        f += 0.002
    total = sum(p for _, p in spectrum)
    if total <= 0:
        return None
    peak_f, _ = max(spectrum, key=lambda z: z[1])
    concentration = sum(p for g, p in spectrum if abs(g - peak_f) <= 0.03) / total
    reliable = context == "scheduled" and concentration >= RESP_MIN_CONCENTRATION
    return {"breaths_per_min": round(peak_f * 60, 1), "peak_concentration": round(concentration, 3),
            "reliable": reliable,
            "note": None if reliable else ("awake recordings are dominated by a ~0.1 Hz blood-pressure rhythm"
                                           if context != "scheduled" else "breathing peak not distinct enough")}


def analyze_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Return UI-ready quality diagnostics for one raw session.

    ``offsets_ms`` describe arrival times and may contain duplicates.  The
    cleaned series includes every original sample, while ``accepted`` marks
    samples usable for the experimental adjacent-difference calculation.
    """
    if not isinstance(payload, dict):
        raise ValueError("HRV payload must be an object")
    ppis = payload.get("ppi", [])
    offsets = payload.get("offsets_ms", [])
    if not isinstance(ppis, list) or not isinstance(offsets, list) or len(ppis) != len(offsets):
        raise ValueError("ppi and offsets_ms must be paired arrays")

    series = []
    reasons: list[str] = []
    valid = []
    for index, (ppi, offset) in enumerate(zip(ppis, offsets)):
        item = {"index": index, "offset_ms": offset, "ppi_ms": ppi, "accepted": False, "reason": None}
        if not _finite_number(ppi) or not MIN_PPI_MS <= float(ppi) <= MAX_PPI_MS:
            item["reason"] = "outside_plausible_range"
        elif not _finite_number(offset) or float(offset) < 0:
            item["reason"] = "invalid_arrival_offset"
        elif float(offset) < WARMUP_MS:
            item["reason"] = "warmup"
        else:
            item["accepted"] = True
            valid.append((index, float(ppi), float(offset)))
        series.append(item)

    # Reject a sudden change against a robust local baseline.  The baseline
    # uses nearby plausible values and does not repair or replace the sample.
    for pos, (index, ppi, offset) in enumerate(valid):
        neighbours = [x[1] for x in valid[max(0, pos - 2):pos] + valid[pos + 1:pos + 3]]
        baseline = median(neighbours) if neighbours else None
        if baseline and abs(ppi - baseline) / baseline > 0.20:
            series[index]["accepted"] = False
            series[index]["reason"] = "abrupt_relative_change"
    accepted = [(item["index"], float(item["ppi_ms"]), float(item["offset_ms"])) for item in series if item["accepted"]]
    for item in series:
        if item["reason"]:
            reasons.append(item["reason"])

    post_warmup_offsets = [x[2] for x in accepted]
    span_ms = max(post_warmup_offsets) - min(post_warmup_offsets) if len(post_warmup_offsets) > 1 else 0.0
    # This is only a transport/cadence consistency proxy, never proof of beat
    # coverage because offsets represent arrival times.
    sum_ppi = sum(x[1] for x in accepted)
    consistency_ratio = round(sum_ppi / span_ms, 4) if span_ms > 0 else None
    ordered = all(_finite_number(x) for x in offsets) and all(b >= a for a,b in zip(offsets, offsets[1:]))
    period = _sample_period(offsets) if ordered else None
    fixed_clock = period is not None and TACHOGRAM_PERIOD_MS[0] <= period <= TACHOGRAM_PERIOD_MS[1]
    latest_updates = payload.get("stream_semantics") == "latest_ppi_updates"
    tachogram = latest_updates or (fixed_clock and consistency_ratio is not None
                                   and consistency_ratio > TACHOGRAM_MIN_SUM_RATIO)

    pairs = []
    runs: list[list[tuple[float, float, int]]] = []
    if tachogram:
        # Uniform sample clock: index * period.  Arrival offsets only carry
        # FIFO batching jitter.  Runs split at every rejected sample.
        max_gap = max((float(b) - float(a) for a, b in zip(offsets, offsets[1:])), default=0.0) if ordered else float("inf")
        cadence_ok = fixed_clock and max_gap <= TACHOGRAM_MAX_GAP_MS
        if cadence_ok:
            run: list[tuple[float, float, int]] = []
            for item in accepted:
                if run and item[0] != run[-1][2] + 1:
                    runs.append(run)
                    run = []
                run.append((item[0] * period, item[1], item[0]))
            if run:
                runs.append(run)
            for run in runs:
                pairs.extend(_tachogram_pairs([(t, v) for t, v, _ in run]))
    else:
        for left, right in zip(accepted, accepted[1:]):
            # Duplicate offsets are valid transport batches.  A large positive
            # arrival gap indicates an unknown portion of the interval stream and
            # must split the RMSSD run.
            gap = right[2] - left[2]
            if right[0] == left[0] + 1 and gap <= MAX_ARRIVAL_GAP_MS:
                pairs.append((left[1], right[1]))
            elif right[0] == left[0] + 1 and gap > MAX_ARRIVAL_GAP_MS:
                reasons.append("oversized_arrival_gap")
        cadence_ok = consistency_ratio is not None and 0.80 <= consistency_ratio <= 1.20
    rmssd = None
    if len(pairs) >= MIN_INTERVALS - 1:
        rmssd = round((sum((b - a) ** 2 for a, b in pairs) / len(pairs)) ** 0.5, 3)

    post_warmup = [item for item in series if item["reason"] != "warmup"]
    artifact_rejected = sum(1 for item in post_warmup if not item["accepted"])
    artifact_fraction = artifact_rejected / max(1, len(post_warmup))
    retained_fraction = round(len(accepted) / len(series), 4) if series else 0.0
    duplicate_offsets = sum(1 for i in range(1, len(offsets)) if offsets[i] == offsets[i - 1])
    duplicate_groups = 0
    for i in range(1, len(offsets)):
        if offsets[i] == offsets[i - 1] and (i == 1 or offsets[i - 1] != offsets[i - 2]):
            duplicate_groups += 1
    motion = _motion(payload)
    unique_reasons = sorted(set(reasons))

    if not cadence_ok:
        unique_reasons.append("tachogram_clock_irregular" if tachogram else "arrival_cadence_inconsistent_with_interval_sum")
    if not ordered:
        unique_reasons.append("nonmonotonic_arrival_times")
    if tachogram:
        unique_reasons.append("1hz_tachogram_beats_reconstructed")
    if motion.get("vibration_status") == "unknown":
        # The collector never vibrates during capture; external notifications
        # remain possible, so this is recorded but does not block the session.
        unique_reasons.append("vibration_contamination_unknown")
    if len(pairs) < MIN_INTERVALS - 1 or span_ms < MIN_SPAN_MS or not cadence_ok or not ordered:
        status = "insufficient"
        if len(pairs) < MIN_INTERVALS - 1:
            unique_reasons.append("fewer_than_60_consecutive_surviving_intervals")
        if span_ms < MIN_SPAN_MS:
            unique_reasons.append("post_warmup_span_under_60_seconds")
    elif artifact_fraction > 0.35:
        status = "artifacts"
        unique_reasons.append("high_artifact_fraction")
    elif not motion["available"]:
        status = "motion_unknown"
        unique_reasons.append("motion_data_missing_or_empty")
    else:
        status = "experimental"
    # The spectral estimate is the costly part; only run it for usable sessions.
    respiration = None
    if status == "experimental" and runs:
        respiration = _respiration([v for _, v, _ in max(runs, key=len)], period, _context(payload))
    return {
        "status": status,
        "quality": status,
        "validated": False,
        "experimental_rmssd_ms": rmssd if status == "experimental" else None,
        "metric": METRIC_TACHOGRAM if tachogram else METRIC_BEAT,
        "stream": "tachogram_1hz" if tachogram else "beat_to_beat",
        "sample_period_ms": round(period, 3) if period is not None else None,
        "mean_rr_ms": round(sum_ppi / len(accepted), 3) if accepted else None,
        "respiration": respiration if status == "experimental" else None,
        "cleaned_intervals": series,
        "original_count": len(series),
        "retained_count": len(accepted),
        "retained_fraction": retained_fraction,
        "artifact_fraction": round(artifact_fraction, 4),
        "consecutive_pairs": len(pairs),
        "span_ms": round(span_ms, 3),
        "arrival_batches": {"duplicate_offset_count": duplicate_offsets, "duplicate_group_count": max(0, duplicate_groups)},
        "sum_ppi_ms": round(sum_ppi, 3),
        "sum_ppi_over_span": consistency_ratio,
        "cadence_consistent": cadence_ok,
        "motion": motion,
        "context": _context(payload),
        "reasons": sorted(set(unique_reasons)),
    }


def analyze_history(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze history with context-separated, prior-day-only baselines."""
    points = []
    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in sessions if isinstance(sessions, list) else []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload", row)
        if isinstance(payload, str):
            import json
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                continue
        if not isinstance(payload, dict):
            continue
        quality = analyze_session(payload)
        ts = row.get("ts", payload.get("ts"))
        context = quality["context"]
        point = {"ts": ts, "experimental_rmssd_ms": quality["experimental_rmssd_ms"], "quality": quality["status"], "context": context, "metric": quality["metric"], "source_validation": "unvalidated", "motion": quality["motion"]}
        points.append(point)
        if isinstance(ts, (int, float)):
            day = datetime.fromtimestamp(ts, LOCAL_TZ).date().isoformat()
            by_day[(context, day)].append(quality)
    day_summaries = []
    progress = {}
    for context in ("manual", "scheduled"):
        rows = []
        for (ctx, day), values in sorted(by_day.items()):
            if ctx != context:
                continue
            good = [v for v in values if v["status"] == "experimental" and v["motion"]["available"]]
            # Never pool beat-to-beat RMSSD with tachogram-reconstructed RMSSD.
            metrics = [v["metric"] for v in good]
            metric = max(sorted(set(metrics)), key=metrics.count) if metrics else None
            usable = [v["experimental_rmssd_ms"] for v in good if v["metric"] == metric]
            rows.append({"date": day, "context": context, "metric": metric, "experimental_rmssd_ms": round(median(usable), 3) if usable else None, "sessions": len(values), "adequate_motion": bool(usable), "quality": "experimental" if usable else "insufficient"})
        day_summaries.extend(rows)
        progress[context] = {"adequate_baseline_days": sum(1 for r in rows if r["adequate_motion"]), "required_days": 14}

    # Use only the latest usable point as the current observation. A baseline
    # is made from earlier local days of the same context, never future data
    # and never a mixture of manual and scheduled sessions.
    stress = {"status": "quality_insufficient", "experimental_score": None, "baseline_days": 0, "required_days": 14,
              "reason": "Current session lacks adequate quality or motion", "method": "experimental logRMSSD percentile; lower current value maps to higher arousal; no clinical interpretation"}
    numeric_points = [p for p in points if isinstance(p["ts"], (int, float))]
    if numeric_points:
        current = max(numeric_points, key=lambda p: p["ts"])
        current_day = datetime.fromtimestamp(current["ts"], LOCAL_TZ).date()
        prior = []
        for row in day_summaries:
            if row["context"] != current["context"] or row["metric"] != current["metric"] or not row["adequate_motion"] or row["experimental_rmssd_ms"] is None:
                continue
            day = datetime.fromisoformat(row["date"]).date()
            age = (current_day - day).days
            if 1 <= age <= 28:
                prior.append(float(row["experimental_rmssd_ms"]))
        stress["baseline_days"] = len(prior)
        stress["method"] = ("experimental recovery/arousal proxy from prior context-matched logRMSSD percentile; "
                             "lower current value maps to higher arousal; no clinical interpretation") if current["context"] == "scheduled" else stress["method"]
        if current['experimental_rmssd_ms'] is None or not current['motion']['available']:
            stress['status']='quality_insufficient'
            stress['reason']='Latest recording cannot establish consecutive beat intervals with adequate movement data; a longer baseline alone will not fix this'
        elif len(prior) < 14:
            stress['status']='baseline_building'
            stress['reason']='Requires 14 distinct prior context-matched days with adequate quality and motion'
        else:
            import math
            current_log = math.log(max(1e-9, float(current["experimental_rmssd_ms"])))
            baseline_logs = sorted(math.log(max(1e-9, x)) for x in prior)
            percentile = 100.0 * sum(current_log > x for x in baseline_logs) / len(baseline_logs)
            percentile += 50.0 * sum(current_log == x for x in baseline_logs) / len(baseline_logs)
            stress["status"] = "experimental"
            stress["experimental_score"] = round(max(0.0, min(100.0, 100.0 - percentile)), 3)
            stress["reason"] = "Experimental context-matched baseline available; source remains unvalidated"
    return {"points": points, "day_summaries": day_summaries, "baseline_progress": progress, "stress": stress, "source_validation": "unvalidated"}
