"""Carbohydrate prescriptions.

Two-tier system, calibrated against published sport-nutrition science and
Fuelin's reverse-engineered athlete program:

1. **Per-meal traffic-light** — RED 30g / YELLOW 50g / GREEN 100g of carbs
2. **Per-day g/kg** — periodized by session TSS and duration

References:
- Jeukendrup A. (2014). A step towards personalized sports nutrition:
  carbohydrate intake during exercise. *Sports Med*.
- Burke L. (2011). The IOC consensus on sports nutrition.
- Fuelin athlete nutrition program guide (2025).
"""

from __future__ import annotations

from typing import Literal

from fuelcast.sources.trainingpeaks import Workout

DayColor = Literal["RED", "YELLOW", "GREEN"]

# Per-meal carb gram targets (Fuelin-validated)
MEAL_CARBS_G: dict[DayColor, int] = {
    "RED": 30,
    "YELLOW": 50,
    "GREEN": 100,
}


def session_color(workout: Workout | None) -> DayColor:
    """Classify a session as RED / YELLOW / GREEN.

    Decision tree:
      1. No workout or very short / very easy → RED
      2. Has TSS → use TSS bands (most accurate)
      3. Otherwise → fall back to duration + intensity keywords
    """
    if workout is None or workout.duration_min < 20:
        return "RED"

    # TSS-based classification (preferred when TP provides it)
    if workout.tss is not None:
        if workout.tss < 30:
            return "RED"
        if workout.tss < 100:
            return "YELLOW"
        return "GREEN"

    # Duration + keyword fallback
    title_lower = workout.title.lower()
    dur_hr = workout.duration_hr

    quality_keywords = (
        "threshold", "vo2", "race", "tempo", "fartlek", "intervals",
        "sweet spot", "ftp", "cp", "race pace", "z4", "z5",
    )
    long_keywords = ("long", "endurance", "z2", "base")
    easy_keywords = ("recovery", "easy", "rest", "walk", "shake")

    if any(k in title_lower for k in easy_keywords):
        return "RED"

    if any(k in title_lower for k in quality_keywords):
        return "GREEN" if dur_hr >= 1 else "YELLOW"

    if any(k in title_lower for k in long_keywords) or dur_hr >= 2:
        return "GREEN"

    if dur_hr < 0.75:
        return "RED"
    if dur_hr < 1.5:
        return "YELLOW"
    return "GREEN"


def daily_carbs_g_per_kg(workout: Workout | None, *, phase: str = "base") -> float:
    """Daily carb prescription in g/kg bodyweight.

    Calibrated to Jeukendrup's periodization:
      3 g/kg   = recovery / rest
      5 g/kg   = light training day
      6-7 g/kg = moderate (≤1.5 hr quality)
      8-10 g/kg = long / hard endurance
      10-12 g/kg = race day or very long session
    """
    color = session_color(workout)

    base = {"RED": 3.5, "YELLOW": 5.5, "GREEN": 7.5}[color]

    # Bump for explicitly long sessions
    if workout is not None:
        if workout.duration_hr >= 3:
            base = max(base, 9.0)
        if workout.duration_hr >= 4:
            base = max(base, 10.0)

    # Race week / peak = top up the carbs
    if phase == "race_week":
        base = max(base, 8.0)
    elif phase == "peak":
        base = max(base, base + 0.5)
    elif phase == "recovery":
        base = min(base, 5.0)

    return base


def daily_carbs_grams(weight_kg: float, workout: Workout | None, *, phase: str = "base") -> int:
    """Daily carbs in grams for this athlete and session."""
    g_per_kg = daily_carbs_g_per_kg(workout, phase=phase)
    return round(g_per_kg * weight_kg)


def meal_breakdown(workout: Workout | None, *, phase: str = "base") -> dict:
    """Return a suggested per-meal carb breakdown for the day.

    Splits a day's carbs across 5 meal slots: breakfast, lunch, recovery,
    afternoon snack, dinner. Each meal gets a RED/YELLOW/GREEN color and
    target carb grams.

    Logic:
      - On a GREEN day, recovery + lunch are GREEN (front-load around session).
      - On a YELLOW day, lunch and dinner are YELLOW, snacks RED.
      - On a RED day, all meals are RED.
    """
    day_color = session_color(workout)

    if day_color == "GREEN":
        return {
            "breakfast": "RED",
            "lunch": "GREEN",
            "recovery": "GREEN" if workout and workout.duration_hr >= 1.5 else "YELLOW",
            "afternoon_snack": "YELLOW",
            "dinner": "YELLOW",
        }
    if day_color == "YELLOW":
        return {
            "breakfast": "RED",
            "lunch": "YELLOW",
            "recovery": "YELLOW",
            "afternoon_snack": "RED",
            "dinner": "YELLOW",
        }
    return {
        "breakfast": "RED",
        "lunch": "RED",
        "recovery": "RED",
        "afternoon_snack": "RED",
        "dinner": "RED",
    }
