"""Tests for the TrainingPeaks ICS parser."""

from datetime import date
from pathlib import Path

from fuelcast.sources.trainingpeaks import (
    classify_sport,
    is_noise_event,
    parse_if,
    parse_tss,
    parse_workouts,
    workout_for,
)

SAMPLE = Path(__file__).parent / "sample_feed.ics"


def test_sample_feed_parses():
    workouts = parse_workouts(
        SAMPLE.read_text(),
        lookback_days=30,
        lookahead_days=30,
        today=date(2026, 4, 27),
    )
    assert len(workouts) >= 6


def test_tss_extraction():
    assert parse_tss("Easy Z1 spin\nDuration: 45 min\nTSS: 25\nIF: 0.55") == 25.0
    assert parse_tss("TSS=110") == 110.0
    assert parse_tss("no tss here") is None


def test_hr_tss_extraction():
    """TrainingPeaks emits hrTSS for heart-rate-based scoring on runs/treadmill."""
    assert parse_tss("9 hrTSS") == 9.0
    assert parse_tss("13 hrTSS") == 13.0
    assert parse_tss("Treadmill Running\n0:06:34\n1.03 mi\n13 hrTSS") == 13.0


def test_running_tss_extraction():
    """rTSS = pace-based running stress score."""
    assert parse_tss("12 rTSS") == 12.0
    assert parse_tss("85 rTSS") == 85.0


def test_decimal_tss():
    assert parse_tss("TSS: 95.5") == 95.5
    assert parse_tss("12.5 hrTSS") == 12.5


def test_if_extraction():
    assert parse_if("IF: 0.88") == 0.88
    assert parse_if("IF=0.72") == 0.72
    assert parse_if("no if here") is None


def test_sport_classification():
    assert classify_sport("Bike - Threshold intervals") == "bike"
    assert classify_sport("Run - Easy Z2") == "run"
    assert classify_sport("Strength - Lower body") == "strength"
    assert classify_sport("Pool swim 2000m") == "swim"


def test_workout_for_picks_longest():
    workouts = parse_workouts(
        SAMPLE.read_text(),
        lookback_days=30,
        lookahead_days=30,
        today=date(2026, 4, 27),
    )
    # Apr 28 has both threshold bike (105 min) and brick run (20 min)
    primary = workout_for(date(2026, 4, 28), workouts)
    assert primary is not None
    assert primary.sport == "bike"
    assert primary.duration_min >= 100


def test_rest_day_returns_none():
    workouts = parse_workouts(
        SAMPLE.read_text(),
        lookback_days=30,
        lookahead_days=30,
        today=date(2026, 4, 27),
    )
    # No workout on 2026-05-04
    assert workout_for(date(2026, 5, 4), workouts) is None


# ──────── noise filter ────────

def test_fuelin_targets_are_noise():
    """Fuelin posts daily macro targets to TP as calendar entries.
    These should be filtered out, not treated as workouts."""
    assert is_noise_event("Other: Fuelin Targets: Pro 210 | Fat 85 | Carb 190")
    assert is_noise_event("Fuelin Target Day")


def test_race_countdown_is_noise():
    assert is_noise_event("Race Day - 30 days to Victoria")
    assert is_noise_event("Race Countdown: 100 days")


def test_real_workouts_pass_filter():
    """Actual workouts should NOT be flagged as noise."""
    assert not is_noise_event("Run: Treadmill Running")
    assert not is_noise_event("Bike - Threshold intervals")
    assert not is_noise_event("Long run with race-pace pickups")


def test_treadmill_classified_as_run():
    """Brooks's TP feed uses 'Run: Treadmill Running' format."""
    assert classify_sport("Run: Treadmill Running") == "run"


def test_colon_format_workouts():
    """TP uses both 'Sport - Title' and 'Sport: Title' formats."""
    assert classify_sport("Bike: Recovery spin") == "bike"
    assert classify_sport("Swim: Pool intervals") == "swim"
