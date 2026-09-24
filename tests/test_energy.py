"""Tests for the energy model, the Garmin athlete state, and meal totals.

Several of these pin bugs found while building the model, so they can't
quietly return:
- calories floating free of expenditure (a surplus on every day type)
- the EA floor calibrated on the wrong population, cancelling every deficit
- protein applied to total mass so the floors alone exceeded a rest day
- a warning that fired every day
- meals that never summed to the day, and 390 g of carbs at dinner
"""

from __future__ import annotations

import pytest

from fuelcast.prescriptions.energy import (
    EA_FLOOR_KCAL_PER_KG_FFM,
    Expenditure,
    energy_flag,
    estimate_expenditure,
    fit_macros,
    mifflin_st_jeor_bmr,
)
from fuelcast.sources.garmin import parse_athlete_state

BROOKS = dict(weight_kg=91, height_cm=178, age=56, sex="M")
RATES = {"run": 340, "ride": 535, "swim": 260, "strength": 170, "yoga": 90}


def _exp(sport=None, hrs=0.0, **kw):
    base = dict(measured_tdee=2900, measured_training_kcal=750, measured_bmr=1790,
                kcal_per_hour=RATES, **BROOKS)
    base.update(kw)
    return estimate_expenditure(session_sport=sport, session_duration_hr=hrs, **base)


# ─── expenditure ─────────────────────────────────────────────────────

def test_mifflin_matches_published_formula():
    # 10*91 + 6.25*178 - 5*56 + 5
    bmr = mifflin_st_jeor_bmr(weight_kg=91, height_cm=178, age=56, sex="M")
    assert bmr == pytest.approx(1747.5)


def test_rest_day_subtracts_training_from_measured_tdee():
    """The 7-day TDEE already contains a week of training. Using it raw
    would give a rest day the same budget as a long ride."""
    e = _exp()
    assert e.rest_baseline_kcal == 2150
    assert e.session_kcal == 0
    assert "measured" in e.method


def test_session_uses_athletes_own_rate():
    e = _exp("bike", 3.0)
    assert e.session_kcal == 3 * 535          # "bike" maps to measured "ride"


def test_falls_back_to_mifflin_when_nothing_measured():
    e = _exp(measured_tdee=None, measured_training_kcal=None, measured_bmr=None)
    assert "Mifflin" in e.method
    assert e.rest_baseline_kcal == round(1747.5 * 1.2)


def test_inconsistent_inputs_cannot_prescribe_starvation():
    """Training kcal larger than TDEE would drive the baseline below BMR."""
    e = _exp(measured_training_kcal=2500)
    assert e.rest_baseline_kcal >= 1790
    assert "clamped" in e.method


# ─── goals ───────────────────────────────────────────────────────────

def _fit(goal, color="RED", sport=None, hrs=0.0, carbs=318, ffm=70.6, trend=None, sex="M"):
    return fit_macros(goal=goal, color=color, expenditure=_exp(sport, hrs),
                      weight_kg=91, ffm_kg=ffm, protein_g_per_kg=2.4,
                      carbs_periodized_g=carbs, weight_trend_kg_wk=trend, sex=sex)


def test_weight_loss_produces_a_deficit():
    """Regression: before the energy model every day type was a surplus."""
    assert _fit("weight_loss").balance_kcal < 0


def test_maintenance_matches_expenditure():
    assert abs(_fit("maintenance", carbs=200).balance_kcal) < 60


def test_training_focus_adds_surplus_on_green_days():
    p = _fit("training_focus", color="GREEN", sport="bike", hrs=2, carbs=900)
    assert p.balance_kcal > 0


def test_unknown_goal_treated_as_maintenance():
    assert _fit("nonsense", carbs=200).goal == "nonsense"
    assert abs(_fit("nonsense", carbs=200).balance_kcal) < 60


# ─── floors ──────────────────────────────────────────────────────────

def test_ea_floor_is_sex_specific():
    """The 30 kcal/kg FFM threshold comes largely from studies of women."""
    assert EA_FLOOR_KCAL_PER_KG_FFM["M"] < EA_FLOOR_KCAL_PER_KG_FFM["F"]


def test_regression_male_ea_floor_does_not_cancel_every_deficit():
    """At 30 kcal/kg FFM, this athlete's rest-day maintenance (2150/70.6 =
    30.5) sat on the floor, so no deficit was ever possible."""
    p = _fit("weight_loss")
    assert p.balance_kcal < -100


def test_energy_availability_never_below_floor():
    for color, sport, hrs, carbs in [("RED", None, 0, 318), ("GREEN", "bike", 5, 910),
                                     ("YELLOW", "run", 1, 500)]:
        p = _fit("weight_loss", color=color, sport=sport, hrs=hrs, carbs=carbs)
        assert p.energy_availability >= EA_FLOOR_KCAL_PER_KG_FFM["M"] - 0.5


def test_female_floor_is_honoured():
    p = _fit("weight_loss", sex="F")
    assert p.energy_availability >= EA_FLOOR_KCAL_PER_KG_FFM["F"] - 0.5


def test_regression_protein_uses_ffm_in_a_deficit():
    """At 2.4 g/kg of *total* mass the macro floors alone exceeded a rest
    day's expenditure, leaving no room for any deficit."""
    p = _fit("weight_loss", ffm=70.6)
    assert p.protein_g == round(2.4 * 70.6)
    assert "fat-free" in p.protein_basis


def test_protein_uses_total_mass_outside_a_deficit():
    p = _fit("maintenance", ffm=70.6)
    assert p.protein_g == round(2.4 * 91)


def test_green_day_carbs_never_below_minimum():
    p = _fit("weight_loss", color="GREEN", sport="bike", hrs=3, carbs=819)
    assert p.carbs_g >= 5.0 * 91


def test_carbs_trimmed_but_never_raised_above_periodized():
    p = _fit("weight_loss", color="RED", carbs=318)
    assert p.carbs_g <= 318


def test_fat_never_below_floor():
    p = _fit("weight_loss")
    assert p.fat_g >= round(0.7 * 91)


# ─── the feedback loop ───────────────────────────────────────────────

def test_losing_too_fast_halves_the_deficit():
    # A long-ride day, where the macro floors don't bind — on a rest day the
    # floors already hold the deficit tighter than halving would, so the
    # effect can't show there.
    kw = dict(color="GREEN", sport="bike", hrs=3, carbs=819)
    slow = _fit("weight_loss", trend=-0.2, **kw)
    fast = _fit("weight_loss", trend=-1.2, **kw)   # >1% of 91 kg per week
    assert not slow.floors_binding
    assert any("halved" in a for a in fast.adjustments)
    assert fast.balance_kcal > slow.balance_kcal     # smaller deficit


def test_gaining_during_loss_is_flagged_not_escalated():
    """Auto-correction only moves toward safety."""
    base = _fit("weight_loss")
    gaining = _fit("weight_loss", trend=+0.3)
    assert any("rising" in a for a in gaining.adjustments)
    assert gaining.balance_kcal == base.balance_kcal


# ─── the flag ────────────────────────────────────────────────────────

def test_regression_flag_does_not_warn_every_day():
    """A warning keyed on EA < 30 fired on every intended deficit."""
    p = _fit("weight_loss", color="GREEN", sport="bike", hrs=3, carbs=819)
    assert "energy availability" not in " ".join(p.floors_binding)
    assert energy_flag(p)["level"] == "ok"


def test_flag_warns_when_ea_floor_binds():
    p = fit_macros(goal="weight_loss", color="RED",
                   expenditure=Expenditure(1700, 0, 1700, "test"),
                   weight_kg=91, ffm_kg=70.6, protein_g_per_kg=1.0,
                   carbs_periodized_g=100)
    assert any("energy availability" in f for f in p.floors_binding)
    assert energy_flag(p)["level"] == "warn"


# ─── athlete state parsing ───────────────────────────────────────────

def _doc(**sections):
    return sections


def test_parses_full_state():
    st = parse_athlete_state(_doc(
        weight={"smoothed_kg": 90.4, "trend_kg_per_week": -0.2, "body_fat_pct": 22.3,
                "ffm_kg": 70.2, "stale_days": 0},
        energy={"tdee_7d": 2900, "training_kcal_7d": 750, "bmr": 1790, "days": 14,
                "kcal_per_hour": {"ride": 535}},
        recovery={"rhr_7d": 48.3, "hrv_last_night": 51, "hrv_status": "BALANCED"}))
    assert st.weight_kg == 90.4 and st.ffm_kg == 70.2
    assert st.tdee_kcal == 2900 and st.training_kcal == 750
    assert st.kcal_per_hour == {"ride": 535.0}
    assert st.hrv_status == "BALANCED"


def test_sections_fail_independently():
    """A bad weight must not suppress a good TDEE."""
    st = parse_athlete_state(_doc(weight={"smoothed_kg": 900, "stale_days": 0},
                                  energy={"tdee_7d": 2900, "days": 10}))
    assert st.weight_kg is None
    assert st.tdee_kcal == 2900


@pytest.mark.parametrize("weight", [{"smoothed_kg": 90, "stale_days": 40},
                                    {"smoothed_kg": 12, "stale_days": 0},
                                    {"smoothed_kg": 900, "stale_days": 0}])
def test_rejects_stale_or_implausible_weight(weight):
    assert parse_athlete_state(_doc(weight=weight)).weight_kg is None


def test_rejects_tdee_from_too_few_days():
    st = parse_athlete_state(_doc(energy={"tdee_7d": 2900, "days": 2}))
    assert st.tdee_kcal is None
    assert st.notes


def test_rejects_training_kcal_exceeding_tdee():
    st = parse_athlete_state(_doc(energy={"tdee_7d": 2500, "training_kcal_7d": 2600, "days": 10}))
    assert st.training_kcal is None


def test_empty_feed_is_an_empty_state():
    st = parse_athlete_state({})
    assert st.weight_kg is None and st.tdee_kcal is None


# ─── meals sum to the day ────────────────────────────────────────────

def test_regression_meals_sum_to_daily_targets():
    """Meals used fixed per-meal protein summing to 140 g against a 218 g
    prescription, and dumped any carb shortfall into dinner."""
    from datetime import date

    from fuelcast.engine import build_meals
    from fuelcast.sources.trainingpeaks import Workout

    wk = Workout(date=date(2026, 9, 24), title="Long Ride", sport="bike",
                 duration_min=180, tss=160)
    targets = {"carbs_g": 593, "protein_g": 168, "fat_g": 63}
    meals = build_meals(91, wk, phase="base", diet="vegetarian", targets=targets)
    for k, v in targets.items():
        assert sum(m[k] for m in meals) == v, k
    dinner = next(m for m in meals if m["slot"] == "dinner")
    assert dinner["carbs_g"] < 250            # not the 390 g dump


# ─── regressions from the first live run (2026-09-24) ────────────────

LIVE = dict(measured_tdee=3231, measured_training_kcal=823, measured_bmr=2215,
            kcal_per_hour={"run": 325, "ride": 516}, weight_kg=94.8,
            height_cm=178, age=56, sex="M")


def test_regression_no_body_fat_does_not_produce_a_surplus():
    """First live run: the scale sent no body fat, protein fell back to 100%
    of body weight (228 g), the floors exceeded expenditure, and a
    weight-loss day prescribed +101 kcal."""
    e = estimate_expenditure(session_sport="run", session_duration_hr=1.0, **LIVE)
    p = fit_macros(goal="weight_loss", color="YELLOW", expenditure=e, weight_kg=94.8,
                   ffm_kg=None, protein_g_per_kg=2.4, carbs_periodized_g=521, sex="M")
    assert p.balance_kcal < 0
    assert p.protein_g < 228


def test_protein_and_ea_use_the_same_lean_mass_estimate():
    """They previously assumed 100% and 80% of body weight respectively."""
    e = estimate_expenditure(session_sport=None, session_duration_hr=0, **LIVE)
    p = fit_macros(goal="weight_loss", color="RED", expenditure=e, weight_kg=94.8,
                   ffm_kg=None, protein_g_per_kg=2.4, carbs_periodized_g=330, sex="M")
    assert p.ffm_estimated is True
    assert p.protein_g == round(2.4 * 94.8 * 0.80)
    assert "estimated" in p.protein_basis


def test_measured_ffm_is_not_labelled_estimated():
    e = estimate_expenditure(session_sport=None, session_duration_hr=0, **LIVE)
    p = fit_macros(goal="weight_loss", color="RED", expenditure=e, weight_kg=94.8,
                   ffm_kg=72.0, protein_g_per_kg=2.4, carbs_periodized_g=330, sex="M")
    assert p.ffm_estimated is False
    assert "estimated" not in p.protein_basis


def test_weight_basis_carried_through():
    """A single weigh-in from 15 days ago must not be labelled a 7-day mean."""
    st = parse_athlete_state({"weight": {"smoothed_kg": 94.8, "stale_days": 15,
                                         "basis": "last weigh-in Sep 9"}})
    assert st.weight_basis == "last weigh-in Sep 9"
    assert st.weight_stale_days == 15
