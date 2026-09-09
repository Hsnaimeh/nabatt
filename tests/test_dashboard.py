"""Tests for the arithmetic that turns CSV rows into a bill.

Standard library only, no hardware, no Windows: run with

    python -m unittest discover -s tests -v

The point of these is not coverage for its own sake. Nabatt's one real promise
is that it does not invent data -- unobserved time stays a gap, and power that
cannot be attributed to a process stays in the baseline. Most of what follows
tests exactly that, because those are the properties a well-meaning change is
most likely to quietly break.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard as D            # noqa: E402
import demo_data                 # noqa: E402

RATE = 0.2
SAMPLE_S = 10


def power_row(ts, wall, wh=None, gpu=0.0, cpu=0.0):
    """One already-parsed power sample, as _load_power would hand it over."""
    return {"ts": ts, "gpu": gpu, "gu": 0.0, "cu": 0.0, "cpu": cpu,
            "wall": wall, "wh": wall * SAMPLE_S / 3600.0 if wh is None else wh}


def steady(day, start_h, minutes, wall=200.0):
    """A run of contiguous samples, one every SAMPLE_S seconds."""
    t0 = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_h)
    n = int(minutes * 60 / SAMPLE_S)
    return [power_row(t0 + timedelta(seconds=i * SAMPLE_S), wall)
            for i in range(n)]


class ParseDt(unittest.TestCase):
    def test_both_accepted_formats(self):
        self.assertEqual(D.parse_dt("2026-09-08 14:22:03"),
                         datetime(2026, 9, 8, 14, 22, 3))
        self.assertEqual(D.parse_dt(" 2026-09-08 "), datetime(2026, 9, 8))

    def test_junk_raises_rather_than_guessing(self):
        for bad in ("", "not a date", "08/09/2026"):
            with self.assertRaises(ValueError):
                D.parse_dt(bad)


class LoadPower(unittest.TestCase):
    """A logger that dies mid-line must not take the dashboard with it."""

    def _write(self, text, name="power-2026-09.csv"):
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        return p

    def test_reads_a_normal_file(self):
        p = self._write(
            "ts,epoch,dt_s,gpu_w,gpu_util,cpu_util,cpu_w,rest_w,wall_w,wh,src\n"
            "2026-09-08 06:00:00,1,10.0,40.0,0,5,26.0,60.0,140.0,0.38889,live\n")
        rows = D._load_power(p)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["wall"], 140.0)

    def test_bad_rows_are_skipped_not_fatal(self):
        p = self._write(
            "ts,epoch,dt_s,gpu_w,gpu_util,cpu_util,cpu_w,rest_w,wall_w,wh,src\n"
            "2026-09-08 06:00:00,1,10.0,40.0,0,5,26.0,60.0,140.0,0.38889,live\n"
            "2026-09-08 06:00:10,1,10.0,,0,5,26.0,60.0,,,live\n"         # torn
            "garbage\n"
            "2026-09-08 06:00:20,1,10.0,41.0,0,5,26.0,60.0,141.0,0.39167,live\n")
        rows = D._load_power(p)
        self.assertEqual(len(rows), 2)

    def test_rows_come_back_in_time_order(self):
        p = self._write(
            "ts,epoch,dt_s,gpu_w,gpu_util,cpu_util,cpu_w,rest_w,wall_w,wh,src\n"
            "2026-09-08 08:00:00,1,10,40,0,5,26,60,300,0.8,live\n"
            "2026-09-08 07:00:00,1,10,40,0,5,26,60,100,0.3,live\n")
        rows = D._load_power(p)
        self.assertLess(rows[0]["ts"], rows[1]["ts"])


class DayStats(unittest.TestCase):
    """A finished day, so that 'now' never enters into it."""

    def setUp(self):
        self.day = (datetime.now() - timedelta(days=3)).date()

    def test_energy_and_cost(self):
        rows = steady(self.day, 9, 60, wall=200.0)      # 200 W for an hour
        s = D.day_stats(rows, self.day, SAMPLE_S, RATE)
        self.assertAlmostEqual(s["kwh"], 0.2, places=3)
        self.assertAlmostEqual(s["cost"], 0.2 * RATE, places=3)

    def test_average_is_over_observed_time_not_the_whole_day(self):
        # one hour at 200 W. The average draw was 200 W, not 200/24.
        s = D.day_stats(steady(self.day, 9, 60, 200.0), self.day, SAMPLE_S, RATE)
        self.assertAlmostEqual(s["avg_w"], 200.0, delta=1.0)
        self.assertAlmostEqual(s["on_hours"], 1.0, delta=0.02)

    def test_unobserved_time_is_a_gap_never_an_estimate(self):
        s = D.day_stats(steady(self.day, 9, 60, 200.0), self.day, SAMPLE_S, RATE)
        self.assertLess(s["coverage"], 0.05)            # 1 h of 24
        self.assertTrue(s["gaps"])
        self.assertGreater(s["off_hours"], 22.0)
        # and the hours nobody watched carry no energy at all
        for b in s["hourly"]:
            if b["h"] != 9:
                self.assertEqual(b["kwh"], 0.0)
                self.assertEqual(b["coverage"], 0.0)

    def test_energy_lands_in_the_hour_it_happened(self):
        rows = steady(self.day, 14, 30, 400.0)
        s = D.day_stats(rows, self.day, SAMPLE_S, RATE)
        self.assertAlmostEqual(s["hourly"][14]["kwh"], 0.2, places=3)
        self.assertAlmostEqual(s["hourly"][14]["coverage"], 0.5, places=2)

    def test_a_short_pause_is_not_reported_as_a_gap(self):
        # gap_limit is 3 x the sample interval; a single missed sample is noise
        rows = steady(self.day, 9, 10, 200.0)
        rows = rows[:30] + rows[31:]                    # drop one sample
        s = D.day_stats(rows, self.day, SAMPLE_S, RATE)
        # only the two real gaps survive: midnight to 09:00, and 09:10 to
        # midnight. Nothing inside the session.
        self.assertEqual(len(s["gaps"]), 2)
        self.assertTrue(all(g["hours"] > 0.5 for g in s["gaps"]),
                        "a single missed sample was reported as a gap")

    def test_a_real_pause_is_reported(self):
        rows = steady(self.day, 9, 20, 200.0) + steady(self.day, 11, 20, 200.0)
        s = D.day_stats(rows, self.day, SAMPLE_S, RATE)
        self.assertTrue(any(g["from"].startswith("09:") and
                            g["to"].startswith("11:") for g in s["gaps"]))

    def test_peak_is_the_highest_sample_and_knows_when(self):
        rows = steady(self.day, 9, 10, 150.0)
        spike = rows[20]["ts"]
        rows[20] = power_row(spike, 590.0)
        s = D.day_stats(rows, self.day, SAMPLE_S, RATE)
        self.assertAlmostEqual(s["peak_w"], 590.0, places=1)
        self.assertEqual(s["peak_at"], spike.strftime("%H:%M"))

    def test_an_empty_day_reports_nothing_rather_than_zero_watts(self):
        s = D.day_stats([], self.day, SAMPLE_S, RATE)
        self.assertEqual(s["kwh"], 0.0)
        self.assertEqual(s["samples"], 0)
        self.assertIsNone(s["first_seen"])
        self.assertEqual(s["avg_w"], 0.0)


class AppBreakdown(unittest.TestCase):
    """Attribution, driven through the real CSV path."""

    @classmethod
    def setUpClass(cls):
        cls.logs = tempfile.mkdtemp(prefix="nabatt-test-logs-")
        cls.day = date(2026, 9, 8)
        rows = [
            # 08:00 -- an hour of GPU work
            ("2026-09-08 08:00:00", "llama-server", 1, 300.0, 2.5, 280.0),
            ("2026-09-08 08:00:30", "llama-server", 1, 300.0, 2.5, 280.0),
            ("2026-09-08 08:00:00", "chrome", 4, 20.0, 0.16, 2.0),
            ("2026-09-08 08:00:30", "chrome", 4, 20.0, 0.16, 2.0),
            ("2026-09-08 08:00:00", "__baseline__", 0, 140.0, 1.17, 0.0),
            ("2026-09-08 08:00:30", "__baseline__", 0, 140.0, 1.17, 0.0),
            # 09:00 -- a process that existed for one sample only
            ("2026-09-08 09:00:00", "ffmpeg", 1, 60.0, 0.5, 55.0),
            ("2026-09-08 09:00:00", "__baseline__", 0, 140.0, 1.17, 0.0),
        ]
        with open(os.path.join(cls.logs, "apps-2026-09-08.csv"), "w",
                  encoding="utf-8", newline="") as f:
            f.write("ts,app,pids,gpu_pct,cpu_pct,gpu_w,cpu_w,watts,wh\n")
            for ts, app, pids, watts, wh, gw in rows:
                f.write("%s,%s,%d,0,0,%.2f,%.2f,%.2f,%.5f\n"
                        % (ts, app, pids, gw, watts - gw, watts, wh))
        cls._real_logs = D.LOGS
        D.LOGS = cls.logs

    @classmethod
    def tearDownClass(cls):
        D.LOGS = cls._real_logs

    def test_shares_sum_to_one(self):
        apps = D.app_breakdown([self.day], RATE)
        self.assertAlmostEqual(sum(a["share"] for a in apps), 1.0, places=2)

    def test_baseline_is_flagged_and_kept_separate(self):
        apps = D.app_breakdown([self.day], RATE)
        base = [a for a in apps if a["baseline"]]
        self.assertEqual(len(base), 1)
        self.assertEqual(base[0]["raw"], "__baseline__")
        # it is never folded into a named application
        self.assertNotIn("__baseline__", [a["app"] for a in apps if not a["baseline"]])

    def test_names_are_made_friendly(self):
        apps = {a["raw"]: a["app"] for a in D.app_breakdown([self.day], RATE)}
        self.assertEqual(apps["llama-server"], "llama.cpp server (local LLM)")
        self.assertEqual(apps["chrome"], "Google Chrome")

    def test_hour_filter_selects_only_that_hour(self):
        eight = {a["raw"] for a in D.app_breakdown([self.day], RATE, hour=8)}
        nine = {a["raw"] for a in D.app_breakdown([self.day], RATE, hour=9)}
        self.assertIn("llama-server", eight)
        self.assertNotIn("ffmpeg", eight)
        self.assertIn("ffmpeg", nine)
        self.assertNotIn("llama-server", nine)

    def test_short_lived_process_still_gets_an_average(self):
        """Regression: a `sec > 60` threshold used to report 0.0 W beside a
        real peak, for anything that ran for less than a minute."""
        ff = [a for a in D.app_breakdown([self.day], RATE, hour=9)
              if a["raw"] == "ffmpeg"][0]
        self.assertGreater(ff["avg_w"], 0.0)
        self.assertAlmostEqual(ff["avg_w"], 60.0, delta=1.0)

    def test_gpu_share_reflects_where_the_power_went(self):
        apps = {a["raw"]: a for a in D.app_breakdown([self.day], RATE, hour=8)}
        self.assertGreater(apps["llama-server"]["gpu_share"], 0.9)
        self.assertEqual(apps["__baseline__"]["gpu_share"], 0.0)

    def test_a_missing_day_is_empty_not_an_error(self):
        self.assertEqual(D.app_breakdown([date(1999, 1, 1)], RATE), [])


class DemoData(unittest.TestCase):
    """The demo fixture has to stay a faithful stand-in for the real loggers."""

    @classmethod
    def setUpClass(cls):
        cls.logs = tempfile.mkdtemp(prefix="nabatt-test-demo-")
        demo_data.generate(cls.logs, seed=1)
        cls._real_logs = D.LOGS
        D.LOGS = cls.logs

    @classmethod
    def tearDownClass(cls):
        D.LOGS = cls._real_logs

    def test_writes_files_the_dashboard_can_find(self):
        self.assertTrue(D.month_keys())
        self.assertTrue(D.app_day_keys())

    def test_power_rows_parse_through_the_real_loader(self):
        rows = D.all_power_rows()
        self.assertGreater(len(rows), 1000)
        self.assertTrue(all(r["wall"] > 0 for r in rows))

    def test_it_produces_gaps_rather_than_a_flat_line(self):
        by_day = D.group_by_day(D.all_power_rows())
        d, rows = sorted(by_day.items())[1]        # a whole day, not today
        s = D.day_stats(rows, d, SAMPLE_S, RATE)
        self.assertTrue(s["gaps"], "a demo day with no gaps is not realistic")
        self.assertLess(s["coverage"], 1.0)

    def test_baseline_is_present_in_every_app_day(self):
        for key in D.app_day_keys():
            names = {r["app"] for r in D.read_app_day(key)}
            self.assertIn("__baseline__", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
