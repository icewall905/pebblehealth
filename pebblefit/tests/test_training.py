import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location("training", Path(__file__).parents[1] / "bridge/training.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 1, 10, tzinfo=t.TZ).timestamp()

    def workout(self, **extra):
        row = {"id": "w", "start": self.start, "seconds": 600, "kind": "run", "rpe": 5, "source": "test"}
        row.update(extra)
        return row

    def minutes(self, hr=160, count=10, start=None):
        start = self.start if start is None else start
        return [{"ts": start + i * 60, "hr": hr} for i in range(count)]

    def test_missing_day_is_unknown_but_confirmed_rest_is_zero(self):
        r = t.analyze([], [], ["2026-10-01", "2026-10-02"], 200,
                      confirmations={"2026-10-02": "rest"})
        self.assertEqual(r["history"][0]["status"], "unknown")
        self.assertEqual(r["history"][1]["load"], 0)

    def test_incomplete_session_does_not_make_complete_day(self):
        r = t.analyze([self.workout()], self.minutes(count=3), ["2026-10-01"], 200,
                      confirmations={"2026-10-01": "complete"})
        self.assertEqual(r["history"][0]["status"], "partial")
        self.assertIsNone(r["history"][0]["load"])

    def test_hr_requires_eighty_percent_distinct_minutes(self):
        r = t.analyze([self.workout()], self.minutes(count=7), ["2026-10-01"], 200)
        self.assertIsNone(r["workouts"][0]["hr_load"])
        self.assertEqual(r["workouts"][0]["hr_coverage"], .7)

    def test_hr_and_rpe_scales_remain_separate(self):
        r = t.analyze([self.workout()], self.minutes(), ["2026-10-01"], 200,
                      confirmations={"2026-10-01": "complete"})
        self.assertEqual(r["workouts"][0]["hr_load"], 40)
        self.assertEqual(r["workouts"][0]["rpe_load"], 50)
        rp = t.analyze([self.workout()], [], ["2026-10-01"], 200, load_method="rpe",
                       confirmations={"2026-10-01": "complete"})
        self.assertEqual(rp["history"][0]["load"], 50)

    def test_overlap_is_excluded_until_one_is_ignored(self):
        second = self.workout(id="w2", start=self.start + 300)
        r = t.analyze([self.workout(), second], self.minutes(), ["2026-10-01"], 200,
                      confirmations={"2026-10-01": "complete"})
        self.assertTrue(all(w["overlap"] and w["excluded"] for w in r["workouts"]))
        self.assertIsNone(r["history"][0]["load"])
        second["ignored"] = True
        r = t.analyze([self.workout(), second], self.minutes(), ["2026-10-01"], 200,
                      confirmations={"2026-10-01": "complete"})
        self.assertEqual(r["history"][0]["load"], 40)

    def test_ewma_restarts_after_unknown_day(self):
        days = [f"2026-10-{i:02d}" for i in range(1, 4)]
        ws = [self.workout(start=self.start + i * 86400, id=f"w{i}") for i in range(3)]
        mins = sum((self.minutes(start=self.start + i * 86400) for i in range(3)), [])
        r = t.analyze(ws, mins, days, 200, confirmations={days[0]: "complete", days[2]: "complete"})
        self.assertIsNotNone(r["history"][0]["acute"])
        self.assertIsNone(r["history"][1]["acute"])
        self.assertEqual(r["history"][2]["acute"], 40)

    def test_ewma_resets_on_calendar_gap_and_rolling_window_is_calendar_based(self):
        w = self.workout(seconds=120, rpe=5)
        r = t.analyze([w], self.minutes(count=2), ["2026-10-01", "2026-10-10"], 200,
                      confirmations={"2026-10-01": "complete", "2026-10-10": "rest"})
        self.assertEqual(r["history"][1]["acute"], 0)
        self.assertEqual(r["history"][1]["rolling7"], 0)
        self.assertEqual(r["history"][1]["rolling7_coverage"], 1)

    def test_rpe_mode_does_not_require_hr_max(self):
        r = t.analyze([self.workout()], [], ["2026-10-01"], None, load_method="rpe",
                      confirmations={"2026-10-01": "complete"})
        self.assertEqual(r["history"][0]["load"], 50)
        self.assertIsNone(r["workouts"][0]["hr_load"])
        self.assertEqual(r["zones"], [])

    def test_initial_fitness_is_explicitly_unsupported(self):
        with self.assertRaises(ValueError):
            t.analyze([], [], [], 200, initial_fitness=10)

    def test_fractional_edge_minutes_are_not_full_minutes(self):
        start = self.start + 30
        r = t.analyze([self.workout(start=start, seconds=90)],
                      [{"ts": start, "hr": 160}, {"ts": start + 60, "hr": 160}],
                      ["2026-10-01"], 200)
        self.assertEqual(r["workouts"][0]["hr_coverage"], 1)
        self.assertEqual(r["workouts"][0]["hr_load"], 6)

    def test_calendar_windows_do_not_treat_sparse_rows_as_complete(self):
        r = t.analyze([], [], ["2026-10-01", "2026-10-10"], 200,
                      confirmations={"2026-10-01": "rest", "2026-10-10": "rest"})
        self.assertEqual(r["latest"]["known_days7"], 1)
        self.assertEqual(len(r["weeks"]), 2)
        self.assertTrue(all(w["status"] == "partial" for w in r["weeks"]))
        self.assertGreater(r["weeks"][0]["unknown_days"], 0)


if __name__ == "__main__":
    unittest.main()
