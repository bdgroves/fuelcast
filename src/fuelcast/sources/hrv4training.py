"""HRV4Training — morning HRV readings and the app's own daily verdict.

Why HRV4Training rather than Garmin's overnight HRV
---------------------------------------------------
athlete.yaml records wrist optical HR as unreliable for this athlete.
HRV4Training takes a short morning spot measurement (phone camera or chest
strap) under a controlled protocol, and it has a working baseline today,
whereas Garmin's HRV status needs weeks of overnight wear to establish one.

Use the app's verdict, don't reinvent it
----------------------------------------
Each reading carries a ``daily_message`` produced by HRV4Training's own
algorithm against the athlete's personal normal range, e.g.

    "Your HRV is within your normal range and your subjective scores are
     trending positively: Proceed as planned"
    "Your HRV is below your normal range. However your subjective scores
     are trending positively: Limit intensity today"
    "Your subjective scores are trending positively but your HRV is
     unusually high: Take it easy today"

That verdict is the primary signal. Note the third one: an unusually *high*
reading is flagged as a reason for caution, not freshness — so nothing here
treats high HRV as permission to push.

Parsing traps in the export (all real, all found in the first file)
-------------------------------------------------------------------
- Line endings are bare carriage returns. ``wc -l`` reports 0 lines, and a
  naive reader sees the whole file as a single row.
- Several headers carry a leading space (" trainingTSS").
- There is a row for every calendar day, including days with no reading.
  Those have "-" for rMSSD and ``test_duration`` 0, and must be dropped —
  otherwise a missed morning looks like missing data rather than no data.
- Missing values are "-".

The file arrives either by manual export or, later, by an automated Dropbox
fetch. Either way it lands at the same path and goes through this parser.
"""

from __future__ import annotations

import csv
import io
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# A reading older than this doesn't describe this morning. It is still
# shown, but never used to adjust today's plan.
MAX_READING_AGE_DAYS = 1

# Rolling window and normal-range window for the ln(rMSSD) trend, following
# the common Plews / Altini approach: 7-day mean against a ~60-day baseline.
ROLLING_DAYS = 7
BASELINE_DAYS = 60

VERDICTS = ("unusually_high", "above", "slightly_above", "within", "below")


@dataclass
class HRVReading:
    day: date
    rmssd: float
    hr: float | None
    recovery_points: float | None
    verdict: str | None       # one of VERDICTS, or None
    advice: str | None        # "Proceed as planned", "Limit intensity today", ...
    alcohol: str | None


@dataclass
class HRVState:
    latest: HRVReading | None = None
    age_days: int | None = None
    usable_today: bool = False        # fresh enough to adjust today's plan
    rolling_ln: float | None = None   # 7-day mean ln(rMSSD)
    baseline_ln: float | None = None  # 60-day mean ln(rMSSD)
    baseline_sd: float | None = None
    readings_30d: int = 0
    history: list[HRVReading] = field(default_factory=list)
    notes: tuple[str, ...] = ()


def classify(message: str | None) -> tuple[str | None, str | None]:
    """Map HRV4Training's daily message to (verdict, advice)."""
    if not message or message.strip() in ("", "-"):
        return None, None
    s = message.lower()
    if "unusually high" in s:
        v = "unusually_high"
    elif "slightly above" in s:
        v = "slightly_above"
    elif "above your normal" in s:
        v = "above"
    elif "below" in s:
        v = "below"
    elif "within" in s:
        v = "within"
    else:
        v = None
    advice = message.rsplit(":", 1)[-1].strip() if ":" in message else None
    return v, advice


def _num(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "", "-") else None
    except ValueError:
        return None


def parse_csv(text: str) -> list[HRVReading]:
    """Parse the export into real readings only, oldest first."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out: list[HRVReading] = []
    for row in rows[1:]:
        if not row:
            continue
        # strict=False on purpose: export rows can be shorter than the header.
        r = dict(zip(header, (c.strip() for c in row), strict=False))
        rmssd = _num(r.get("rMSSD"))
        if rmssd is None or rmssd <= 0 or r.get("test_duration") == "0":
            continue            # a calendar day with no measurement
        try:
            day = date.fromisoformat((r.get("date") or "")[:10])
        except ValueError:
            continue
        verdict, advice = classify(r.get("daily_message"))
        alcohol = r.get("alcohol")
        out.append(HRVReading(
            day=day, rmssd=rmssd, hr=_num(r.get("HR")),
            recovery_points=_num(r.get("HRV4T_Recovery_Points")),
            verdict=verdict, advice=advice,
            alcohol=alcohol if alcohol not in (None, "", "-") else None,
        ))
    out.sort(key=lambda x: x.day)
    return out


def summarise(readings: list[HRVReading], *, today: date) -> HRVState:
    """Reduce parsed readings to what the engine needs today."""
    past = [r for r in readings if r.day <= today]
    if not past:
        return HRVState(notes=("no HRV4Training readings",))
    latest = past[-1]
    age = (today - latest.day).days
    notes: list[str] = []
    if age > MAX_READING_AGE_DAYS:
        notes.append(f"latest HRV reading is {age} days old — shown, not used for today")

    def window(days: int) -> list[float]:
        return [math.log(r.rmssd) for r in past if (today - r.day).days < days]

    roll = window(ROLLING_DAYS)
    base = window(BASELINE_DAYS)
    return HRVState(
        latest=latest,
        age_days=age,
        usable_today=age <= MAX_READING_AGE_DAYS and latest.verdict is not None,
        rolling_ln=round(statistics.mean(roll), 3) if roll else None,
        baseline_ln=round(statistics.mean(base), 3) if len(base) >= 7 else None,
        baseline_sd=round(statistics.stdev(base), 3) if len(base) >= 7 else None,
        readings_30d=len(window(30)),
        history=[r for r in past if (today - r.day).days < BASELINE_DAYS],
        notes=tuple(notes),
    )


def load(path: Path | str, *, today: date) -> HRVState:
    """Read the export from disk. A missing file is an empty state, not an error."""
    p = Path(path)
    if not p.exists():
        return HRVState(notes=(f"no HRV file at {p}",))
    return summarise(parse_csv(p.read_text(encoding="utf-8", errors="replace")), today=today)


def recovery_level(state: HRVState) -> tuple[int, str | None]:
    """How much HRV says to ease off today: 0 none, 1 ease.

    Only moves toward caution. A below-range reading eases the plan, and so
    does an unusually high one — HRV4Training itself advises "take it easy"
    for those. Nothing here ever makes the plan more aggressive.
    """
    if not state.usable_today or state.latest is None:
        return 0, None
    v = state.latest.verdict
    if v == "below":
        return 1, "HRV below your normal range"
    if v == "unusually_high":
        return 1, "HRV unusually high (HRV4Training advises taking it easy)"
    return 0, None


# ─── Privacy trim ──────────────────────────────────────────────────────
#
# The FuelCast repo is public, and the raw export carries far more than
# HRV: alcohol, sickness, travel, sleep, subjective scores, notes and
# location columns. Only these six are needed, so only these six are ever
# committed — whether the file arrived by manual export or Dropbox.
KEEP_COLUMNS = ("date", "rMSSD", "HR", "test_duration", "HRV4T_Recovery_Points", "daily_message")


def trim(text: str) -> str:
    """Reduce a raw export to KEEP_COLUMNS and real readings only.

    Output uses ordinary newlines and parses with parse_csv exactly as the
    raw file did.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ""
    header = [h.strip() for h in rows[0]]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(KEEP_COLUMNS)
    for row in rows[1:]:
        if not row:
            continue
        r = dict(zip(header, (c.strip() for c in row), strict=False))
        if _num(r.get("rMSSD")) is None or r.get("test_duration") == "0":
            continue
        w.writerow([r.get(c, "") for c in KEEP_COLUMNS])
    return out.getvalue()
