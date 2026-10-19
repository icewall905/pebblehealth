import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "bridge"))
import fitness  # noqa: E402
import weight  # noqa: E402


def test_unconfigured_source_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(fitness, "DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("WEIGHT_PG_DSN", raising=False)
    with fitness.connect() as db:
        assert weight.sync(db) == "not_configured"
        assert weight.history(db, date(2026, 10, 19)) is None
    assert fitness.summary(7)["weight"] is None


def test_trend_and_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(fitness, "DB_PATH", str(tmp_path / "t.sqlite3"))
    with fitness.connect() as db:
        db.executemany("INSERT INTO weights VALUES(?,?,'external')",
                       [(f"2026-10-{d:02d}", 120 - 0.25 * d) for d in range(1, 20)])
        h = weight.history(db, date(2026, 10, 19))
    assert h["latest"]["kg"] == 115.25
    assert h["latest"]["trend_kg"] == round(sum(120 - 0.25 * d for d in range(13, 20)) / 7, 2)
    assert h["change_7d_kg"] == -1.75 and h["change_28d_kg"] is None
    assert h["days_since_latest"] == 0


def test_failing_source_never_breaks_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(fitness, "DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("WEIGHT_PG_DSN", "host=127.0.0.1 port=1 dbname=x connect_timeout=1")
    monkeypatch.setenv("WEIGHT_PG_QUERY", "select 1")
    with fitness.connect() as db:
        assert weight.sync(db) == "error"
