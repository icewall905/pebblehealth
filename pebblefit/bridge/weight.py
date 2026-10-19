"""Optional body-weight history from an external PostgreSQL source.

Configure with two environment variables:
  WEIGHT_PG_DSN    libpq connection string, ideally a read-only role
  WEIGHT_PG_QUERY  SELECT returning rows of (date, weight_kg), one per weigh-in

Rows are copied into the local ``weights`` table at most every SYNC_SECONDS,
so the dashboard stays fast and keeps showing history if the source is down.
Without configuration nothing is synced and the dashboard hides weight.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

SYNC_SECONDS = 30 * 60
TREND_DAYS = 7
log = logging.getLogger("pebble-health.weight")


def configured() -> bool:
    return bool(os.environ.get("WEIGHT_PG_DSN") and os.environ.get("WEIGHT_PG_QUERY"))


def sync(db, now: float | None = None, force: bool = False) -> str:
    """Refresh local weights from the source; returns a short status."""
    if not configured():
        return "not_configured"
    now = time.time() if now is None else now
    row = db.execute("SELECT value FROM settings WHERE key='weight_synced'").fetchone()
    if row and not force and now - float(row[0]) < SYNC_SECONDS:
        return "fresh"
    try:
        import psycopg
        with psycopg.connect(os.environ["WEIGHT_PG_DSN"], connect_timeout=5) as pg:
            rows = pg.execute(os.environ["WEIGHT_PG_QUERY"]).fetchall()
    except Exception as error:  # the source being down must never break the dashboard
        log.warning("weight sync failed: %s", error)
        # Back off for a full interval so a dead source can't slow every page.
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('weight_synced',?)", (str(now),))
        return "error"
    clean = []
    for day, kg in rows:
        day = day.decode() if isinstance(day, bytes) else str(day)[:10]
        try:
            kg = float(kg)
            date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        if 20 <= kg <= 400:
            clean.append((day, kg))
    # Several weigh-ins on one day keep the last one returned by the query.
    db.executemany("INSERT OR REPLACE INTO weights(day,kg,source) VALUES(?,?,'external')", clean)
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('weight_synced',?)", (str(now),))
    return "synced"


def history(db, today: date) -> dict[str, Any] | None:
    """Entries with a trailing 7-day mean and the latest changes."""
    rows = [(date.fromisoformat(d), kg) for d, kg in db.execute("SELECT day,kg FROM weights ORDER BY day")]
    if not rows:
        return None
    entries = []
    for i, (d, kg) in enumerate(rows):
        window = [w for e, w in rows[:i + 1] if d - timedelta(days=TREND_DAYS - 1) <= e <= d]
        entries.append({"date": d.isoformat(), "kg": round(kg, 2), "trend_kg": round(sum(window) / len(window), 2)})

    def trend_on_or_before(day: date) -> float | None:
        older = [e for e in entries if date.fromisoformat(e["date"]) <= day]
        return older[-1]["trend_kg"] if older else None

    latest = entries[-1]
    last_day = date.fromisoformat(latest["date"])
    change = {}
    for label, days in (("change_7d_kg", 7), ("change_28d_kg", 28)):
        base = trend_on_or_before(last_day - timedelta(days=days))
        change[label] = round(latest["trend_kg"] - base, 2) if base is not None else None
    return {"entries": entries, "latest": latest, "days_since_latest": (today - last_day).days, **change}
