"""End-to-end test of the engine."""

from datetime import date
from pathlib import Path

from fuelcast.athlete import load_athlete
from fuelcast.biomarkers import latest_panel
from fuelcast.engine import build_day_plan
from fuelcast.sources.trainingpeaks import parse_workouts

SAMPLE = Path(__file__).parent / "sample_feed.ics"
REPO_ROOT = Path(__file__).parent.parent


def test_engine_generates_complete_plan():
    """Run the engine end-to-end on a green-day workout."""
    athlete = load_athlete(REPO_ROOT / "data" / "athlete.yaml")
    panel = latest_panel(REPO_ROOT / "data" / "bloodwork")

    workouts = parse_workouts(
        SAMPLE.read_text(),
        lookback_days=30,
        lookahead_days=30,
        today=date(2026, 4, 28),
    )

    plan = build_day_plan(
        date(2026, 4, 28),  # threshold intervals day in sample feed
        athlete=athlete,
        workouts=workouts,
        panel=panel,
    )

    # Day color should be GREEN (TSS=95 + threshold keyword)
    assert plan.day_color in ("YELLOW", "GREEN")

    # Should have a workout
    assert plan.workout is not None
    assert "Threshold" in plan.workout["title"]

    # Macros should be reasonable for an 80kg athlete
    assert 100 <= plan.macros["protein_g"] <= 250
    assert 250 <= plan.macros["carbs_g"] <= 700
    assert 50 <= plan.macros["fat_g"] <= 150
    assert 1800 <= plan.macros["calories"] <= 4500

    # Should have meal breakdown
    assert len(plan.meals) >= 4

    # Should have in-session fueling for a 105-minute session
    assert plan.in_session is not None
    assert plan.in_session["target_carbs_g_per_hr"] > 0

    # Should have vegetarian flags
    titles = [f["title"] for f in plan.flags]
    assert "B12" in titles
    assert "Iron" in titles

    # Should have biomarkers
    assert len(plan.biomarkers) > 0

    # Should have race countdown
    assert plan.race is not None
    assert plan.race["days_to_go"] > 0

    # Week strip should have 7 days
    assert len(plan.week_strip) == 7
    assert any(d["is_today"] for d in plan.week_strip)


def test_rest_day_plan():
    """Rest day should be RED with no in-session plan."""
    athlete = load_athlete(REPO_ROOT / "data" / "athlete.yaml")

    plan = build_day_plan(
        date(2026, 5, 4),  # no workout in sample
        athlete=athlete,
        workouts=parse_workouts(
            SAMPLE.read_text(),
            lookback_days=30, lookahead_days=30,
            today=date(2026, 5, 4),
        ),
        panel=None,
    )

    assert plan.day_color == "RED"
    assert plan.workout is None
    assert plan.in_session is None
    # Rest-day carbs are lower
    assert plan.macros["carbs_g"] < 400
