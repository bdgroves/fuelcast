"""Tests for prescription logic."""

from datetime import date

import pytest

from fuelcast.prescriptions.carbs import (
    MEAL_CARBS_G,
    daily_carbs_g_per_kg,
    meal_breakdown,
    session_color,
)
from fuelcast.prescriptions.protein import daily_protein_g_per_kg
from fuelcast.prescriptions.session import (
    in_session_carbs_g_per_hr,
    in_session_plan,
    multi_source_carb_mix,
)
from fuelcast.sources.trainingpeaks import Workout


def make_workout(
    *, duration_min=60, tss=None, intensity_factor=None, title="Bike - Endurance",
    sport="bike",
) -> Workout:
    return Workout(
        date=date(2026, 4, 28),
        title=title,
        sport=sport,
        duration_min=duration_min,
        tss=tss,
        intensity_factor=intensity_factor,
    )


# ──────── session_color ────────

def test_no_workout_is_red():
    assert session_color(None) == "RED"


def test_short_session_is_red():
    assert session_color(make_workout(duration_min=15)) == "RED"


def test_low_tss_is_red():
    assert session_color(make_workout(tss=20)) == "RED"


def test_moderate_tss_is_yellow():
    assert session_color(make_workout(tss=60)) == "YELLOW"


def test_high_tss_is_green():
    assert session_color(make_workout(tss=180)) == "GREEN"


def test_threshold_keyword_is_green():
    wo = make_workout(duration_min=75, title="Bike - Threshold intervals")
    assert session_color(wo) == "GREEN"


def test_recovery_keyword_is_red():
    wo = make_workout(duration_min=45, title="Bike - Recovery spin")
    assert session_color(wo) == "RED"


def test_long_endurance_is_green():
    wo = make_workout(duration_min=180, title="Bike - Long endurance")
    assert session_color(wo) == "GREEN"


# ──────── daily carbs ────────

def test_rest_day_low_carbs():
    assert daily_carbs_g_per_kg(None) == 3.5


def test_quality_day_higher_carbs():
    wo = make_workout(tss=150, duration_min=90)
    assert daily_carbs_g_per_kg(wo) >= 7.0


def test_long_session_bumps_carbs():
    wo = make_workout(tss=180, duration_min=210)  # 3.5 hr
    assert daily_carbs_g_per_kg(wo) >= 9.0


def test_race_week_floor():
    # Even on a rest day, race week pushes carbs up
    assert daily_carbs_g_per_kg(None, phase="race_week") >= 8.0


# ──────── meal breakdown ────────

def test_green_day_has_green_lunch():
    wo = make_workout(tss=150, duration_min=120)
    breakdown = meal_breakdown(wo)
    assert breakdown["lunch"] == "GREEN"


def test_red_day_all_red():
    breakdown = meal_breakdown(None)
    assert all(c == "RED" for c in breakdown.values())


def test_meal_carb_buckets_match_fuelin():
    """Confirm the per-meal targets stay calibrated to the Fuelin spec."""
    assert MEAL_CARBS_G["RED"] == 30
    assert MEAL_CARBS_G["YELLOW"] == 50
    assert MEAL_CARBS_G["GREEN"] == 100


# ──────── protein ────────

def test_masters_vegetarian_protein_floor():
    """Brooks's profile: 56yo male, vegetarian, base phase."""
    g_per_kg = daily_protein_g_per_kg(age=56, diet="vegetarian", phase="base")
    assert g_per_kg >= 2.4   # 2.2 floor + 0.2 veg
    assert g_per_kg <= 3.0


def test_young_omnivore_lower_protein():
    g_per_kg = daily_protein_g_per_kg(age=30, diet="omnivore", phase="base")
    assert g_per_kg < 2.2


def test_build_phase_bumps_protein():
    base = daily_protein_g_per_kg(age=56, diet="vegetarian", phase="base")
    build = daily_protein_g_per_kg(age=56, diet="vegetarian", phase="build")
    assert build > base


def test_protein_capped_at_3():
    g_per_kg = daily_protein_g_per_kg(age=70, diet="vegan", phase="build")
    assert g_per_kg <= 3.0


# ──────── in-session ────────

def test_short_session_no_in_session_fueling():
    wo = make_workout(duration_min=45)
    assert in_session_carbs_g_per_hr(wo) == 0
    assert in_session_plan(wo) is None


def test_long_session_recommends_carbs():
    wo = make_workout(duration_min=180, tss=180, intensity_factor=0.78)
    rate = in_session_carbs_g_per_hr(wo, gut_trained_to=90)
    assert rate >= 75


def test_multi_source_above_60():
    glucose, fructose = multi_source_carb_mix(75)
    assert fructose > 0
    assert glucose + fructose == 100


def test_single_source_at_or_below_60():
    glucose, fructose = multi_source_carb_mix(60)
    assert fructose == 0
    assert glucose == 100


def test_gut_tolerance_caps_rate():
    wo = make_workout(duration_min=240, intensity_factor=0.85)
    rate = in_session_carbs_g_per_hr(wo, gut_trained_to=60)
    assert rate <= 60


def test_session_plan_includes_total():
    wo = make_workout(duration_min=120, tss=110)
    plan = in_session_plan(wo, gut_trained_to=75)
    assert plan is not None
    assert plan.total_carbs_g > 0
    assert any("Total" in s.when for s in plan.steps)
