"""AFT scoring tables and grading (PBI-07, L29).

The 25 cases below (5 per event) were read by hand off the official AFT Score Tables
(approved 15 May 2025, effective 1 June 2025), not generated from the data file, so a
loader or parsing mistake shows up as a mismatch. Format of each case:
(event, age group, column, raw result, points the printed table gives).
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import scoring  # noqa: E402

PACK = json.loads((HERE.parents[2] / "tests" / "synthetic_test_pack.json").read_text(encoding="utf-8"))

HAND_CASES = [
    # Deadlift (pounds)
    ("MDL", "17-21", "M|C", "340", 100),   # exactly the 100 point row
    ("MDL", "17-21", "M|C", "335", 98),    # 99 row is blank for this column, 330 earns 98
    ("MDL", "22-26", "F", "210", 97),
    ("MDL", "17-21", "F", "125", 60),      # 120 earns 60, next row up needs 130
    ("MDL", "17-21", "M|C", "79", 0),      # below the 0 point row (80)
    # Hand-release push-ups (repetitions)
    ("HRP", "17-21", "M|C", "58", 100),
    ("HRP", "17-21", "M|C", "56", 98),     # 99 needs 57, 98 needs 55
    ("HRP", "17-21", "F", "24", 81),       # the 82 row is blank for this column
    ("HRP", "22-26", "M|C", "14", 60),
    ("HRP", "17-21", "M|C", "3", 0),       # below the 0 point row (4)
    # Sprint-drag-carry (time, faster is better)
    ("SDC", "17-21", "M|C", "1:29", 100),
    ("SDC", "17-21", "M|C", "1:30", 99),   # 1:31 is the 99 point time, so 1:30 is faster
    ("SDC", "17-21", "F", "2:28", 80),
    ("SDC", "22-26", "F", "3:15", 60),
    ("SDC", "17-21", "M|C", "3:30", 0),    # slower than the 0 point time (3:28)
    # Plank (time, longer is better)
    ("PLK", "17-21", "M|C", "3:40", 100),
    ("PLK", "17-21", "M|C", "3:36", 98),   # 99 needs 3:37, 98 needs 3:34
    ("PLK", "22-26", "F", "3:35", 100),
    ("PLK", "17-21", "M|C", "1:27", 54),   # odd rows are blank here; 54 is printed as 1:27
    ("PLK", "17-21", "M|C", "0:45", 0),    # below the 0 point time (1:00)
    # Two-mile run (time, faster is better)
    ("2MR", "17-21", "M|C", "13:22", 100),
    ("2MR", "17-21", "M|C", "13:30", 99),
    ("2MR", "17-21", "F", "16:00", 100),
    ("2MR", "22-26", "M|C", "19:45", 60),
    ("2MR", "17-21", "M|C", "23:00", 0),   # slower than the 0 point time (22:45)
]


class HandCheckedTables(unittest.TestCase):
    def test_25_hand_checked_cases_match_the_printed_tables(self):
        self.assertEqual(len(HAND_CASES), 25)
        wrong = []
        for event, group, column, raw, expected in HAND_CASES:
            got, _ = scoring.points(event, raw, group, column)
            if got != expected:
                wrong.append((event, group, column, raw, expected, got))
        self.assertEqual(wrong, [], f"{len(wrong)} of 25 cases do not match the tables")


class TableData(unittest.TestCase):
    def setUp(self):
        self.t = scoring.load_tables()

    def test_five_events_ten_age_groups_two_columns(self):
        self.assertEqual(sorted(self.t["events"]), sorted(scoring.EVENT_CODES))
        for code, spec in self.t["events"].items():
            self.assertEqual(len(spec["tables"]), 10, code)
            for group, cols in spec["tables"].items():
                self.assertEqual(sorted(cols), ["F", "M|C"], (code, group))
                for col, rows in cols.items():
                    # every column has the 60, 50, 40, 30, 20, 10 and 0 rows
                    for p in ("60", "50", "40", "30", "20", "10", "0"):
                        self.assertIn(p, rows, (code, group, col, p))

    def test_more_points_never_needs_an_easier_result(self):
        for code, spec in self.t["events"].items():
            for group, cols in spec["tables"].items():
                for col, rows in cols.items():
                    pts = sorted(int(p) for p in rows)
                    vals = [scoring._seconds(rows[str(p)]) if spec["unit"] == "m:ss"
                            else rows[str(p)] for p in pts]
                    pairs = list(zip(vals, vals[1:]))
                    if spec["better"] == "higher":
                        ok = all(a <= b for a, b in pairs)
                    else:
                        ok = all(a >= b for a, b in pairs)
                    self.assertTrue(ok, (code, group, col))

    def test_shared_pack_results_all_score_between_0_and_100(self):
        keys = {"MDL": "mdl_lbs", "HRP": "hrp_reps", "SDC": "sdc_time",
                "PLK": "plank_time", "2MR": "twomr_time"}
        for cadet in PACK["cadets"]:
            for code, key in keys.items():
                pts, _ = scoring.points(code, str(cadet["aft_results"][key]), "22-26", "M|C")
                self.assertTrue(0 <= pts <= 100)

    def test_age_group_for(self):
        self.assertEqual(scoring.age_group_for(17), "17-21")
        self.assertEqual(scoring.age_group_for(21), "17-21")
        self.assertEqual(scoring.age_group_for(22), "22-26")
        self.assertEqual(scoring.age_group_for(61), "57-61")
        self.assertEqual(scoring.age_group_for(62), "62+")
        self.assertEqual(scoring.age_group_for(80), "62+")
        with self.assertRaises(scoring.ScoringError):
            scoring.age_group_for(16)

    def test_tables_are_labeled_confirmed(self):
        self.assertTrue(self.t["meta"]["dpe_confirmed"])
        self.assertEqual(scoring.summarize({})["standards_status"], "confirmed")


class BadInput(unittest.TestCase):
    def test_bad_values_are_rejected_with_the_field_named(self):
        bad = [
            ("MDL", "abc", "17-21", "M|C", "raw_value"),
            ("MDL", "-5", "17-21", "M|C", "raw_value"),
            ("MDL", "9999", "17-21", "M|C", "raw_value"),     # out of range
            ("HRP", "12.5", "17-21", "M|C", "raw_value"),
            ("SDC", "90", "17-21", "M|C", "raw_value"),       # a time needs m:ss
            ("SDC", "1:75", "17-21", "M|C", "raw_value"),     # seconds over 59
            ("PLK", "", "17-21", "M|C", "raw_value"),
            ("2MR", "1:00", "17-21", "M|C", "raw_value"),     # too fast to be real
            ("2MR", "13:22", "17-22", "M|C", "age_group"),
            ("2MR", "13:22", "17-21", "X", "scoring_column"),
            ("XXX", "10", "17-21", "M|C", "component"),
        ]
        for event, raw, group, col, field in bad:
            with self.assertRaises(scoring.ScoringError, msg=(event, raw, group, col)) as cm:
                scoring.points(event, raw, group, col)
            self.assertIn(f"'{field}'", str(cm.exception))

    def test_times_are_stored_in_one_format(self):
        self.assertEqual(scoring.points("2MR", "13:05", "17-21", "M|C")[1], "13:05")
        self.assertEqual(scoring.points("PLK", " 3:40 ", "17-21", "M|C")[1], "3:40")


class PassFail(unittest.TestCase):
    def test_total_and_pass(self):
        r = scoring.summarize({"MDL": 80, "HRP": 70, "SDC": 99, "PLK": 100, "2MR": 61})
        self.assertEqual((r["total_points"], r["result"], r["failed_events"]),
                         (410, "PASS", []))

    def test_one_event_under_the_minimum_fails(self):
        r = scoring.summarize({"MDL": 100, "HRP": 100, "SDC": 100, "PLK": 100, "2MR": 59})
        self.assertEqual((r["total_points"], r["result"], r["failed_events"]),
                         (459, "FAIL", ["2MR"]))

    def test_missing_events_are_incomplete_not_pass(self):
        r = scoring.summarize({"MDL": 100, "HRP": 100, "SDC": 100, "PLK": 100, "2MR": None})
        self.assertEqual((r["total_points"], r["result"]), (None, "INCOMPLETE"))


if __name__ == "__main__":
    unittest.main()
