"""Tests for training load math (CTL/ATL/TSB)."""

from datetime import date, timedelta

from fuelcast.sources.trainingpeaks import Workout
from fuelcast.training_load import (
    carb_adjustment_pct,
    compute_training_load,
    latest_load,
    training_load_flag,
    tsb_state,
)


def make_workout(d: date, *, tss: float, completed: bool = True) -> Workout:
    return Workout(
        date=d,
        title="Bike - Endurance",
        sport="bike",
        duration_min=60,
        tss=tss,
        is_completed=completed,
    )


# ──────── tsb_state classification ────────

def test_fresh_state():
    assert tsb_state(20) == "fresh"
    assert tsb_state(15.1) == "fresh"


def test_recovered_state():
    assert tsb_state(10) == "recovered"
    assert tsb_state(5) == "recovered"


def test_productive_state():
    assert tsb_state(0) == "productive"
    assert tsb_state(-10) == "productive"


def test_heavy_load_state():
    assert tsb_state(-15) == "heavy_load"
    assert tsb_state(-20) == "heavy_load"


def test_overreached_state():
    assert tsb_state(-25) == "overreached"
    assert tsb_state(-50) == "overreached"


# ──────── carb adjustment ────────

def test_no_carb_bump_when_recovered():
    assert carb_adjustment_pct(10) == 0
    assert carb_adjustment_pct(0) == 0
    assert carb_adjustment_pct(-5) == 0


def test_carb_bump_when_heavy_load():
    assert carb_adjustment_pct(-15) == 10


def test_carb_bump_when_overreached():
    assert carb_adjustment_pct(-25) == 15


# ──────── CTL/ATL/TSB computation ────────

def test_zero_workouts_returns_zero_load():
    today = date(2026, 5, 1)
    history = compute_training_load([], target_date=today, seed_days=30)
    assert len(history) == 31  # seed_days + today inclusive
    assert history[-1].ctl == 0
    assert history[-1].atl == 0
    assert history[-1].tsb == 0


def test_consistent_training_builds_ctl():
    """100 TSS/day for 60 days should land CTL near 100."""
    today = date(2026, 5, 1)
    workouts = []
    for i in range(60):
        d = today - timedelta(days=i)
        workouts.append(make_workout(d, tss=100))

    history = compute_training_load(workouts, target_date=today, seed_days=60)
    final = history[-1]
    # After 60 days of 100 TSS, CTL should be in the 70-100 range
    # (decay constant means it asymptotes toward 100 but doesn't reach it from 0)
    assert 70 < final.ctl < 100
    # ATL converges faster — should be very close to 100
    assert 95 < final.atl <= 100


def test_taper_drops_atl_first():
    """A taper drops ATL faster than CTL → TSB rises."""
    today = date(2026, 5, 1)
    workouts = []
    # 30 days of 100 TSS, then 14 days of 30 TSS (taper)
    for i in range(44):
        d = today - timedelta(days=i)
        tss = 30 if i < 14 else 100
        workouts.append(make_workout(d, tss=tss))

    history = compute_training_load(workouts, target_date=today, seed_days=44)
    final = history[-1]
    # After the taper, ATL should have dropped meaningfully
    # CTL stays higher → TSB should be positive (fresh-ish)
    assert final.atl < final.ctl


def test_planned_workouts_dont_count():
    """Planned-but-not-completed workouts don't add to training load."""
    today = date(2026, 5, 1)
    yesterday = today - timedelta(days=1)
    # planned for today but not completed
    workouts = [make_workout(today, tss=200, completed=False)]
    history = compute_training_load(workouts, target_date=today, seed_days=10)
    # No completed TSS = no load
    assert history[-1].ctl == 0
    assert history[-1].atl == 0


def test_history_length_matches_seed_days():
    today = date(2026, 5, 1)
    history = compute_training_load([], target_date=today, seed_days=14)
    assert len(history) == 15  # 14 days + today


def test_latest_load_returns_most_recent():
    today = date(2026, 5, 1)
    workouts = [make_workout(today, tss=100)]
    history = compute_training_load(workouts, target_date=today, seed_days=10)
    final = latest_load(history)
    assert final is not None
    assert final.date == today


def test_latest_load_handles_empty():
    assert latest_load([]) is None


# ──────── flag generation ────────

def test_training_load_flag_for_overreached():
    today = date(2026, 5, 1)
    # Build a load history where ATL > CTL by a lot
    workouts = []
    # Last 7 days are huge
    for i in range(7):
        d = today - timedelta(days=i)
        workouts.append(make_workout(d, tss=300))
    # Older days were nothing (CTL ~ 0)
    history = compute_training_load(workouts, target_date=today, seed_days=14)
    final = latest_load(history)
    assert final is not None
    flag = training_load_flag(final)
    assert flag is not None
    # Should be alert-level
    assert flag["level"] in ("alert", "warn")


def test_training_load_flag_handles_none():
    assert training_load_flag(None) is None
