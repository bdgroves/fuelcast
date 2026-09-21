"""Garmin daily-TSS feed.

Why this exists
---------------
FuelCast's training-load model is only as good as the TSS history it is
given. Its original source, the TrainingPeaks iCal feed, is parsed with
``lookback_days=7`` — while ``athlete.yaml`` seeds CTL/ATL at a date
months earlier. Every day between the seed date and the feed window was
therefore scored TSS 0, and the exponential filter decayed a real CTL of
54 down to 2.1 before the first actual workout appeared.

Nothing errored. The model simply believed the athlete had done nothing
for four months, reported "Heavy load — significant fatigue", and bumped
carbs 10% on the strength of it.

Garmin has the real history. The companion repo (bdgroves.github.io)
publishes it as a flat daily series from the same fetch that powers the
training dashboard, and this module reads it.

Contract
--------
``https://raw.githubusercontent.com/bdgroves/bdgroves.github.io/main/data/training-load.json``

    {
      "source": "garmin",
      "updated": "2026-09-21T21:37:13Z",
      "window_days": 180,
      "start": "2026-03-25",
      "end": "2026-09-21",
      "from_garmin": 412,       # activities with Garmin's own load value
      "estimated": 0,           # activities where TSS was duration-derived
      "daily": [ {"date": "2026-03-25", "tss": 61.0}, ... ]
    }

Every day in the window is present, including rest days at 0.0. That is
load-bearing: a rest day and a missing day mean different things, and
conflating them is what caused the original bug. Callers must treat an
explicit 0.0 as real and only fall back to another source for dates the
series does not cover at all.
"""

from __future__ import annotations

import os
from datetime import date, datetime

import requests

DEFAULT_URL = (
    "https://raw.githubusercontent.com/bdgroves/bdgroves.github.io"
    "/main/data/training-load.json"
)

# A series older than this is treated as unusable rather than trusted.
# A stale feed decays CTL exactly the way the original bug did, so it is
# better to fail loudly back to the TrainingPeaks path than to quietly
# extend a frozen history with zeros.
MAX_AGE_DAYS = 7


class GarminLoadUnavailable(RuntimeError):
    """Raised when the feed is missing, malformed, or too stale to trust."""


def fetch_daily_tss(
    url: str | None = None,
    *,
    timeout: int = 15,
    today: date | None = None,
) -> dict[str, float]:
    """Return ``{"YYYY-MM-DD": tss}`` for the published window.

    Raises GarminLoadUnavailable rather than returning a partial or empty
    mapping — a caller that silently accepted ``{}`` here would recreate
    the all-zeros failure this module was written to fix.
    """
    url = url or os.environ.get("GARMIN_LOAD_URL", DEFAULT_URL)
    today = today or date.today()

    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        doc = r.json()
    except Exception as e:  # network, HTTP, JSON — all the same to the caller
        raise GarminLoadUnavailable(f"could not fetch {url}: {e}") from e

    return parse_daily_tss(doc, today=today)


def parse_daily_tss(doc: dict, *, today: date | None = None) -> dict[str, float]:
    """Validate a fetched document and flatten it to a date->TSS mapping.

    Split out from fetch_daily_tss so the validation can be tested without
    a network round-trip.
    """
    today = today or date.today()

    rows = doc.get("daily")
    if not isinstance(rows, list) or not rows:
        raise GarminLoadUnavailable("feed contains no 'daily' rows")

    end_str = doc.get("end") or rows[-1].get("date")
    try:
        end = datetime.strptime(str(end_str), "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:
        raise GarminLoadUnavailable(f"unparseable 'end' date: {end_str!r}") from e

    age = (today - end).days
    if age > MAX_AGE_DAYS:
        raise GarminLoadUnavailable(
            f"feed is {age} days stale (ends {end}); refusing to extend it with zeros"
        )

    series: dict[str, float] = {}
    for row in rows:
        d = row.get("date")
        t = row.get("tss")
        if not d or t is None:
            continue
        try:
            series[str(d)] = float(t)
        except (TypeError, ValueError):
            continue

    if not series:
        raise GarminLoadUnavailable("no usable rows in 'daily'")

    # A window that is entirely zero is indistinguishable from no data and
    # is almost certainly a broken upstream fetch, not four months of rest.
    if not any(v > 0 for v in series.values()):
        raise GarminLoadUnavailable(
            f"all {len(series)} days are TSS 0 — treating as a broken feed"
        )

    return series


def series_bounds(series: dict[str, float]) -> tuple[date, date]:
    """First and last date present in the series."""
    ds = sorted(datetime.strptime(k, "%Y-%m-%d").date() for k in series)
    return ds[0], ds[-1]
