"""Tests for the Garmin daily-TSS source and its effect on training load.

The regression test at the bottom pins the actual bug: a CTL seed months
older than the workout feed's lookback window decays to nothing, and the
model then reports a well-trained athlete as unfit and fatigued.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fuelcast.sources.garmin import (
    GarminLoadUnavailable,
    parse_daily_tss,
    series_bounds,
)
from fuelcast.training_load import compute_training_load, tsb_state


def _doc(rows, end=None):
    return {"source": "garmin", "daily": rows, "end": end or (rows[-1]["date"] if rows else None)}


def _series(start: date, days: int, tss: float):
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "tss": tss}
        for i in range(days)
    ]


# ─── parsing / validation ────────────────────────────────────────────

def test_parses_a_normal_series():
    today = date(2026, 9, 21)
    rows = _series(date(2026, 9, 1), 21, 50.0)
    got = parse_daily_tss(_doc(rows), today=today)
    assert len(got) == 21
    assert got["2026-09-01"] == 50.0


def test_explicit_zero_is_preserved():
    """A rest day is a measurement, not a gap. This distinction is the
    whole reason the original bug was possible."""
    today = date(2026, 9, 21)
    rows = _series(date(2026, 9, 15), 7, 40.0)
    rows[3]["tss"] = 0.0
    got = parse_daily_tss(_doc(rows), today=today)
    assert got[rows[3]["date"]] == 0.0
    assert rows[3]["date"] in got          # present, not dropped


def test_rejects_empty_feed():
    with pytest.raises(GarminLoadUnavailable):
        parse_daily_tss({"daily": []}, today=date(2026, 9, 21))


def test_rejects_stale_feed():
    """A frozen feed extended with zeros decays CTL exactly like the bug."""
    today = date(2026, 9, 21)
    rows = _series(date(2026, 8, 1), 10, 50.0)     # ends 2026-08-10, 42 days old
    with pytest.raises(GarminLoadUnavailable, match="stale"):
        parse_daily_tss(_doc(rows), today=today)


def test_rejects_all_zero_feed():
    """All-zero is indistinguishable from a broken upstream fetch."""
    today = date(2026, 9, 21)
    rows = _series(date(2026, 9, 1), 21, 0.0)
    with pytest.raises(GarminLoadUnavailable, match="broken feed"):
        parse_daily_tss(_doc(rows), today=today)


def test_skips_malformed_rows_but_keeps_good_ones():
    today = date(2026, 9, 21)
    rows = _series(date(2026, 9, 10), 5, 45.0)
    rows.append({"date": None, "tss": 30})
    rows.append({"date": "2026-09-16", "tss": None})
    rows.append({"date": "2026-09-17", "tss": "not a number"})
    got = parse_daily_tss(_doc(rows, end="2026-09-17"), today=today)
    assert len(got) == 5


def test_series_bounds():
    s = {"2026-09-03": 1.0, "2026-09-01": 2.0, "2026-09-02": 0.0}
    assert series_bounds(s) == (date(2026, 9, 1), date(2026, 9, 3))


# ─── effect on the load model ────────────────────────────────────────

def test_daily_tss_overrides_workouts():
    target = date(2026, 9, 21)
    series = {
        (target - timedelta(days=i)).isoformat(): 60.0
        for i in range(180)
    }
    hist = compute_training_load(
        [], target_date=target, seed_days=179,
        initial_ctl=0.0, initial_atl=0.0, daily_tss=series,
    )
    ctl = hist[-1].ctl
    # 180 days of steady 60 TSS converges on CTL ~= 60
    assert 58 <= ctl <= 60, ctl
    assert tsb_state(hist[-1].tsb) in ("productive", "recovered", "fresh")


def test_zero_days_in_series_are_used_not_skipped():
    """If explicit zeros fell through to the workout estimator they would
    be replaced by guesses, defeating the point of a measured series."""
    target = date(2026, 9, 21)
    series = {(target - timedelta(days=i)).isoformat(): 0.0 for i in range(60)}
    series[target.isoformat()] = 100.0        # keep it from looking empty
    hist = compute_training_load(
        [], target_date=target, seed_days=59,
        initial_ctl=50.0, initial_atl=50.0, daily_tss=series,
    )
    # A real 59-day layoff should decay a CTL of 50 substantially.
    assert hist[-1].ctl < 15, hist[-1].ctl


def test_dates_absent_from_series_fall_back_to_workouts():
    from fuelcast.sources.trainingpeaks import Workout
    target = date(2026, 9, 21)
    older = target - timedelta(days=5)
    w = Workout(date=older, title="Ride", sport="bike",
                duration_min=120, tss=80.0, is_completed=True)
    # Series covers only the last 2 days, so `older` must come from the workout.
    series = {(target - timedelta(days=i)).isoformat(): 0.0 for i in range(2)}
    hist = compute_training_load(
        [w], target_date=target, seed_days=10,
        initial_ctl=0.0, initial_atl=0.0, daily_tss=series,
    )
    day = next(h for h in hist if h.date == older)
    assert day.tss_today == 80.0


# ─── the regression ──────────────────────────────────────────────────

def test_regression_stale_seed_with_short_feed_decays_to_nothing():
    """The original failure, pinned.

    athlete.yaml seeded CTL 54 / ATL 40 at 2026-05-03. run_engine parsed
    the iCal with lookback_days=7. So 134 days carried no workouts at all
    and were scored TSS 0, decaying CTL from 54 to ~2 before the feed's
    first real day. The model then called a 700-session-a-year athlete
    'heavy_load'.
    """
    from fuelcast.sources.trainingpeaks import Workout

    target = date(2026, 9, 21)
    seed_date = date(2026, 5, 3)
    seed_days = (target - seed_date).days           # 141

    # Only the last few days have any workouts — the iCal window.
    workouts = [
        Workout(date=date(2026, 9, 17), title="Ride", sport="bike",
                duration_min=90, tss=65.0, is_completed=True),
        Workout(date=date(2026, 9, 18), title="Run", sport="run",
                duration_min=90, tss=95.0, is_completed=True),
        Workout(date=date(2026, 9, 19), title="Easy", sport="run",
                duration_min=30, tss=15.0, is_completed=True),
        Workout(date=date(2026, 9, 20), title="Run", sport="run",
                duration_min=45, tss=30.0, is_completed=True),
    ]

    broken = compute_training_load(
        workouts, target_date=target, seed_days=seed_days,
        initial_ctl=54.0, initial_atl=40.0,
    )
    assert broken[-1].ctl < 10                      # 6.3 in production
    assert tsb_state(broken[-1].tsb) == "heavy_load"   # the false alarm

    # With the measured series the same day reads correctly.
    series = {}
    d = target - timedelta(days=180)
    while d <= target:
        # ~5 sessions a week at a realistic mix
        series[d.isoformat()] = 0.0 if d.weekday() == 0 else 70.0
        d += timedelta(days=1)

    fixed = compute_training_load(
        workouts, target_date=target, seed_days=180,
        initial_ctl=0.0, initial_atl=0.0, daily_tss=series,
    )
    assert fixed[-1].ctl > 45, fixed[-1].ctl
    assert tsb_state(fixed[-1].tsb) != "heavy_load"
