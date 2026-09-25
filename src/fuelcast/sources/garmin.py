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
from dataclasses import dataclass
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


# ─── Athlete state: weight, body composition, energy, recovery ─────────
#
# The same feed that carries daily TSS also carries what the athlete
# weighs and what they actually burn. Before this, FuelCast had neither:
# weight was a hand-edited number in athlete.yaml, and expenditure wasn't
# modelled at all — so a stated weight-loss goal produced a surplus on
# every single day type.
#
# Every field is optional and independently validated. A bad weight must
# not suppress a good TDEE, and vice versa.


# A weigh-in older than this stops describing the current athlete. Macros
# are sized per kg, so a stale weight silently mis-sizes every target.
MAX_WEIGHT_AGE_DAYS = 21

# Energy needs enough complete days to average out a single big ride or a
# lazy Sunday. Fewer than this and one outlier dominates the estimate.
MIN_ENERGY_DAYS = 5


@dataclass
class AthleteState:
    weight_kg: float | None = None           # 7-day smoothed
    weight_trend_kg_wk: float | None = None  # 30-day slope
    weight_basis: str | None = None          # what the weight number actually is
    weight_stale_days: int | None = None
    body_fat_pct: float | None = None
    ffm_kg: float | None = None              # fat-free mass
    tdee_kcal: float | None = None           # measured, 7-day mean
    active_kcal: float | None = None
    bmr_kcal: float | None = None
    training_kcal: float | None = None       # net of BMR, 7-day mean
    kcal_per_hour: dict | None = None        # athlete's own net rate by sport
    activities: tuple = ()                   # recent completed activities, local-dated
    energy_days: int = 0
    rhr_7d: float | None = None
    hrv_last_night: float | None = None
    hrv_status: str | None = None
    notes: tuple[str, ...] = ()


def parse_athlete_state(doc: dict) -> AthleteState:
    """Validate each section of the feed independently."""
    notes: list[str] = []
    st = AthleteState()

    w = doc.get("weight") or {}
    kg = w.get("smoothed_kg") or w.get("latest_kg")
    stale = w.get("stale_days")
    if kg:
        try:
            kg = float(kg)
        except (TypeError, ValueError):
            kg = None
    if kg and stale is not None and stale > MAX_WEIGHT_AGE_DAYS:
        notes.append(f"weight {stale}d stale — using configured")
        kg = None
    # A unit mix-up upstream (grams, or pounds mislabelled as kg) would
    # otherwise flow straight into every per-kg macro.
    if kg and not 30.0 <= kg <= 250.0:
        notes.append(f"weight {kg} outside plausible range — ignored")
        kg = None
    if kg:
        st.weight_kg = kg
        st.weight_trend_kg_wk = w.get("trend_kg_per_week")
        st.weight_basis = w.get("basis")
        st.weight_stale_days = stale
        bf = w.get("body_fat_pct")
        if bf and 3.0 <= float(bf) <= 60.0:
            st.body_fat_pct = float(bf)
            st.ffm_kg = w.get("ffm_kg") or round(kg * (1 - float(bf) / 100), 1)

    e = doc.get("energy") or {}
    days = int(e.get("days") or 0)
    tdee = e.get("tdee_7d")
    if tdee and days >= MIN_ENERGY_DAYS and 1200 <= float(tdee) <= 7000:
        st.tdee_kcal = float(tdee)
        st.active_kcal = e.get("active_7d")
        st.bmr_kcal = e.get("bmr")
        st.energy_days = days
        tk = e.get("training_kcal_7d")
        # Training can't exceed total expenditure; if it does, the split is
        # wrong (double-counted activity) and the baseline would go negative.
        if tk is not None and 0 <= float(tk) < float(tdee):
            st.training_kcal = float(tk)
        elif tk is not None:
            notes.append(f"training kcal {tk} >= TDEE {tdee} — split ignored")
        rates = e.get("kcal_per_hour")
        if isinstance(rates, dict):
            st.kcal_per_hour = {k: float(v) for k, v in rates.items()
                                if isinstance(v, (int, float)) and 0 < v < 1500}
    elif tdee:
        notes.append(f"TDEE from {days} days — below {MIN_ENERGY_DAYS}, not trusted")

    acts = []
    for a in doc.get("activities") or []:
        try:
            mins = float(a.get("duration_min") or 0)
        except (TypeError, ValueError):
            continue
        if a.get("local_date") and mins > 0:
            acts.append(a)
    st.activities = tuple(acts)

    r = doc.get("recovery") or {}
    st.rhr_7d = r.get("rhr_7d")
    st.hrv_last_night = r.get("hrv_last_night")
    st.hrv_status = r.get("hrv_status")

    st.notes = tuple(notes)
    return st


def fetch_athlete_state(
    url: str | None = None,
    *,
    timeout: int = 15,
) -> AthleteState:
    """Fetch the athlete-state sections of the Garmin feed.

    Returns an empty AthleteState (every field None) rather than raising
    when the feed is unreachable — each consumer then falls back to its
    configured value, which is exactly the pre-existing behaviour.
    """
    url = url or os.environ.get("GARMIN_LOAD_URL", DEFAULT_URL)
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        doc = r.json()
    except Exception as e:
        return AthleteState(notes=(f"feed unreachable: {e}",))
    return parse_athlete_state(doc)
