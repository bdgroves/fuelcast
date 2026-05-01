"""Tests for the TrainingPeaks ICS parser."""

from datetime import date
from pathlib import Path

from fuelcast.sources.trainingpeaks import (
    classify_sport,
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
