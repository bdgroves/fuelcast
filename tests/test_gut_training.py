"""Tests for gut-training progression.

Weight parsing moved to test_energy.py along with the rest of the athlete state."""

from __future__ import annotations

from datetime import date

import pytest

from fuelcast.prescriptions.gut_training import (
    MIN_REHEARSAL_MIN,
    build_gut_plan,
    gut_plan_flag,
    race_target,
)

TODAY = date(2026, 9, 21)


# ─── race targets ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "distance,expected",
    [(70.3, 90), ("70.3", 90), (140.6, 90), (26.2, 75),
     ("marathon", 75), (24.8, 60), (10, 30), (None, 60), ("nonsense", 60)],
)
def test_race_target(distance, expected):
    assert race_target(distance) == expected


# ─── the ladder ──────────────────────────────────────────────────────

def test_builds_ladder_to_race_target():
    p = build_gut_plan(today=TODAY, days_to_race=251, current_g_hr=75,
                       race_distance=70.3)
    assert p.race_target_g_hr == 90
    assert p.gap_g_hr == 15
    assert [s.target_g_hr for s in p.ladder] == [80, 85, 90]
    assert p.ladder[0].is_current is True
    assert p.feasible is True


def test_no_ladder_when_already_trained():
    p = build_gut_plan(today=TODAY, days_to_race=251, current_g_hr=95,
                       race_distance=70.3)
    assert p.gap_g_hr == 0
    assert p.ladder == []
    assert gut_plan_flag(p) is None      # nothing useful to say


def test_flags_infeasible_when_time_is_short():
    """30 g/hr to close in 8 weeks cannot be done at 5 g/4 weeks."""
    p = build_gut_plan(today=TODAY, days_to_race=56, current_g_hr=60,
                       race_distance=70.3)
    assert p.feasible is False
    f = gut_plan_flag(p)
    assert f["level"] == "warn"


def test_taper_weeks_are_excluded_from_usable_time():
    """Adaptation work must not be scheduled into taper and race week."""
    # 12 weeks out, needing 12 weeks of work: infeasible once 3 are removed.
    p = build_gut_plan(today=TODAY, days_to_race=12 * 7, current_g_hr=75,
                       race_distance=70.3)
    assert p.weeks_needed == 12
    assert p.feasible is False


def test_no_race_means_no_plan():
    assert build_gut_plan(today=TODAY, days_to_race=None, current_g_hr=75,
                          race_distance=70.3) is None


# ─── rehearsal detection ─────────────────────────────────────────────

def test_short_session_is_not_a_rehearsal():
    p = build_gut_plan(today=TODAY, days_to_race=251, current_g_hr=75,
                       race_distance=70.3, session_duration_min=60)
    assert p.today_is_rehearsal is False
    assert p.today_target_g_hr is None


def test_long_session_rehearses_the_next_rung_not_the_current_one():
    """Practising what you already tolerate trains nothing."""
    p = build_gut_plan(today=TODAY, days_to_race=251, current_g_hr=75,
                       race_distance=70.3,
                       session_duration_min=MIN_REHEARSAL_MIN)
    assert p.today_is_rehearsal is True
    assert p.today_target_g_hr == 80        # next rung, not 75
    assert gut_plan_flag(p)["title"] == "Gut rehearsal"


def test_rehearsal_at_target_when_ladder_complete():
    p = build_gut_plan(today=TODAY, days_to_race=251, current_g_hr=95,
                       race_distance=70.3, session_duration_min=240)
    assert p.today_target_g_hr == 90        # the race demand
