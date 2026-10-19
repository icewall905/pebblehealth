import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "bridge"))
import physiology as p  # noqa: E402

TZ = ZoneInfo("UTC")
DAY = date(2026, 10, 19)
T0 = int(datetime(2026, 10, 19, 12, tzinfo=TZ).timestamp())
BODY = {"sex": "male", "birthdate": "1982-03-01", "height_cm": 199}


def test_jackson_equation_matches_published_coefficients():
    out = p.fitness(BODY, 112.4, 176, [], {}, TZ, DAY)
    bmi = 112.4 / 1.99 ** 2
    expected = 56.363 + 1.921 * 0 - 0.381 * 44 - 0.754 * bmi + 10.987
    assert out["vo2max"]["value"] == round(expected, 1)
    assert out["vo2max"]["inputs"]["age"] == 44


def test_fitness_age_interpolates_hunt_means():
    assert p.fitness_age(46.6, "male") == 45.0
    assert p.fitness_age(44.25, "male") == 50.0
    assert p.fitness_age(60, "male") == 20.0 and p.fitness_age(20, "male") == 80.0


def test_cadence_lock_minutes_never_count_as_intensity():
    locked = [(T0 + i * 60, 150, 100) for i in range(10)]     # 1.5x cadence
    real = [(T0 + 3600 + i * 60, 150, 0) for i in range(10)]   # high HR, no steps
    out = p.intensity(locked + real, 176, TZ, DAY)
    assert out["cadence_lock_rejected"] == 10
    assert out["last_7_days"] == 20                              # 10 vigorous minutes x2


def test_single_spike_minutes_are_ignored():
    out = p.intensity([(T0, 160, 0), (T0 + 600, 160, 0)], 176, TZ, DAY)
    assert out["last_7_days"] == 0


def test_missing_profile_is_explicit():
    out = p.fitness(None, 100, 176, [], {}, TZ, DAY)
    assert out["vo2max"] is None and out["energy"] is None
    assert {"sex", "birth date", "height"} <= set(out["missing"])


def test_bmr_mifflin():
    out = p.fitness(BODY, 100, 176, [], {DAY.isoformat(): 300}, TZ, DAY)
    assert out["energy"]["bmr_kcal"] == round(10 * 100 + 6.25 * 199 - 5 * 44 + 5)
    assert out["energy"]["active_kcal_today"] == 300
