"""AFT scoring for the Gatekeeper (PBI-07, contract v1).

The points for one event come from the official Army Fitness Test score tables, loaded from
data/aft_scoring_2025.json. Nobody types a score: the grader enters the raw result (pounds,
repetitions, or a time), the cadet's age group and the table column, and the Gatekeeper looks
up the points. The Vault stores the raw value and the points it was given.

How a raw result becomes points (the same rule the printed tables use):
  higher is better (deadlift lbs, push-up reps, plank time):
      the points are the largest row whose number the result meets or beats
  lower is better (sprint-drag-carry time, 2-mile run time):
      the points are the largest row whose time the result meets or beats (equal or faster)
  a row printed as "---" has no number and is skipped
  a result that does not reach even the 0 point row scores 0

STATUS: the tables are the official published ones, but the DPE has not yet confirmed which
standards apply to cadets and where each cadet's age group and column come from (open RFI),
and the pass rule below is a placeholder. Every graded result says so in `standards_status`.
"""

import json
import re
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "aft_scoring_2025.json"
IOCT_FILE = Path(__file__).parent / "data" / "ioct_standard.json"

COLUMNS = ("M|C", "F")
EVENT_CODES = ("MDL", "HRP", "SDC", "PLK", "2MR")
STANDARDS_STATUS = "pending_dpe"

# Sanity limits on what a grader may type. A value outside is a typo, not a score.
LIMITS = {
    "MDL": (0, 600),          # pounds
    "HRP": (0, 150),          # repetitions
    "SDC": (30, 600),         # seconds (0:30 to 10:00)
    "PLK": (0, 600),          # seconds (0:00 to 10:00)
    "2MR": (300, 3600),       # seconds (5:00 to 60:00)
}

_TIME = re.compile(r"^(\d{1,2}):([0-5]\d)$")


class ScoringError(ValueError):
    """A value the scoring cannot use. Messages name the field, like the Gatekeeper's other
    validation errors, and never echo more than the bad field."""


_cache = {}


def load_tables(path=DATA_FILE):
    path = str(path)
    if path not in _cache:
        with open(path, encoding="utf-8") as f:
            _cache[path] = json.load(f)
    return _cache[path]


def age_group_for(age):
    """Map an age in years to the table's age group name."""
    if isinstance(age, bool) or not isinstance(age, int) or age < 17:
        raise ScoringError("Field 'age': must be a whole number, 17 or older.")
    for group in load_tables()["meta"]["age_groups"][:-1]:
        low, high = (int(x) for x in group.split("-"))
        if low <= age <= high:
            return group
    return load_tables()["meta"]["age_groups"][-1]


def _seconds(text):
    m = _TIME.match(text)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def parse_raw(event, raw):
    """Typed raw value -> (number used for scoring, normalized text to store).

    MDL and HRP: a whole number. SDC, PLK, 2MR: m:ss, for example 1:29 or 13:22."""
    if event not in EVENT_CODES:
        raise ScoringError(f"Field 'component': must be one of {sorted(EVENT_CODES)}.")
    if not isinstance(raw, str) or not raw.strip():
        raise ScoringError("Field 'raw_value': required.")
    raw = raw.strip()
    unit = load_tables()["events"][event]["unit"]
    low, high = LIMITS[event]
    if unit == "m:ss":
        value = _seconds(raw)
        if value is None:
            raise ScoringError("Field 'raw_value': must be a time like 3:40 (minutes:seconds).")
        text = f"{value // 60}:{value % 60:02d}"
    else:
        if not raw.isdigit():
            raise ScoringError(f"Field 'raw_value': must be a whole number of {unit}.")
        value = int(raw)
        text = str(value)
    if not low <= value <= high:
        shown = (f"{low // 60}:{low % 60:02d} to {high // 60}:{high % 60:02d}" if unit == "m:ss"
                 else f"{low} to {high} {unit}")
        raise ScoringError(f"Field 'raw_value': out of range for {event} ({shown}).")
    return value, text


def _check_context(age_group, column):
    if age_group not in load_tables()["meta"]["age_groups"]:
        raise ScoringError("Field 'age_group': must be one of "
                           f"{load_tables()['meta']['age_groups']}.")
    if column not in COLUMNS:
        raise ScoringError(f"Field 'scoring_column': must be one of {list(COLUMNS)}.")


def points(event, raw, age_group, column):
    """Points (0 to 100) for one event result. Returns (points, normalized raw text)."""
    value, text = parse_raw(event, raw)
    _check_context(age_group, column)
    spec = load_tables()["events"][event]
    rows = spec["tables"][age_group][column]
    best = 0
    for pts_text, threshold in rows.items():
        pts = int(pts_text)
        limit = _seconds(threshold) if spec["unit"] == "m:ss" else threshold
        ok = value >= limit if spec["better"] == "higher" else value <= limit
        if ok and pts > best:
            best = pts
    return best, text


def summarize(points_by_event):
    """Total and pass or fail from the points saved for each event.

    `points_by_event` maps event code to points (or None). Pass needs all five events and at
    least the per-event minimum on every one (PLACEHOLDER rule until the DPE answers)."""
    meta = load_tables()["meta"]
    minimum = meta["pass_rule"]["min_points_per_event"]
    have = {e: p for e, p in points_by_event.items() if p is not None}
    complete = all(e in have for e in EVENT_CODES)
    failed = sorted(e for e, p in have.items() if p < minimum)
    if not complete:
        result = "INCOMPLETE"
    else:
        result = "FAIL" if failed else "PASS"
    return {
        "total_points": sum(have[e] for e in EVENT_CODES) if complete else None,
        "result": result,
        "failed_events": failed if complete else [],
        "min_points_per_event": minimum,
        "standards_status": STANDARDS_STATUS,
    }


def standards_summary():
    """What the form needs to build its dropdowns and to label the standards honestly."""
    t = load_tables()
    meta = t["meta"]
    return {
        "name": meta["name"],
        "approved": meta["approved"],
        "effective": meta["effective"],
        "standards_status": STANDARDS_STATUS,
        "status_note": meta["status_note"],
        "age_groups": meta["age_groups"],
        "scoring_columns": list(COLUMNS),
        "pass_rule": {"min_points_per_event": meta["pass_rule"]["min_points_per_event"],
                      "status": meta["pass_rule"]["status"]},
        "events": [{"component": e, "unit": t["events"][e]["unit"],
                    "better": t["events"][e]["better"]} for e in EVENT_CODES],
    }


# ---- IOCT -------------------------------------------------------------------

def _clock(ms):
    minutes, rem = divmod(ms, 60_000)
    return f"{minutes}:{rem // 1000:02d}.{rem % 1000 // 10:02d}"


def _lookup(spec, column, whole_seconds):
    for row in spec["rows"]:
        band = row[column]
        if whole_seconds >= band["min_s"] and (band["max_s"] is None or whole_seconds <= band["max_s"]):
            return row
    raise ScoringError("IOCT table has a gap")  # cannot happen with the DPE table


def ioct_grade(elapsed_ms, path=IOCT_FILE):
    """Grade one computed IOCT time with the DPE grade scale (data/ioct_standard.json).

    Returns the points, letter grade, PASS or FAIL and TAB for BOTH columns (M|C = men, F = women),
    because the Vault does not store gender. The web app, which asked for it first, shows its
    column: grading["by_column"][column]. Pass is a D (5 points) or better. A- (40 points) or better
    is a TAB, which never has to be retaken. The slide prints whole seconds, so the gate time is cut
    down to the whole second before the lookup. The time itself is never changed here."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    whole = elapsed_ms // 1000
    by_column = {}
    for column in spec["columns"]:
        row = _lookup(spec, column, whole)
        by_column[column] = {"points": row["points"], "grade": row["grade"],
                             "result": "PASS" if row["points"] >= spec["pass_points"] else "FAIL",
                             "tab": row["points"] >= spec["tab_points"]}
    return {"elapsed_ms": elapsed_ms, "elapsed": _clock(elapsed_ms),
            "pass_rule": "D (5 points) or better", "tab_rule": "A- (40 points) or better",
            "by_column": by_column, "standards_status": spec["status"]}
