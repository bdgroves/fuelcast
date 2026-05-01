"""FuelCast engine — orchestrates the full daily plan.

Pulls workouts, athlete profile, and bloodwork together; generates a complete
day plan with macros, meal breakdown, in-session fueling, and flags.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fuelcast.athlete import Athlete, load_athlete
from fuelcast.biomarkers import BloodworkPanel, latest_panel, vegetarian_flags
from fuelcast.prescriptions.carbs import (
    MEAL_CARBS_G,
    daily_carbs_grams,
    meal_breakdown,
    session_color,
)
from fuelcast.prescriptions.fat import (
    daily_calories_estimate,
    daily_fat_g_per_kg,
)
from fuelcast.prescriptions.protein import daily_protein_grams
from fuelcast.prescriptions.session import in_session_plan
from fuelcast.sources.trainingpeaks import (
    Workout,
    all_workouts_for,
    fetch_ics,
    parse_workouts,
    workout_for,
)


@dataclass
class DayPlan:
    """One day's complete fueling plan, ready to render as JSON."""

    date: str
    weekday: str
    phase: str
    day_color: str
    workout: dict | None
    secondary_workouts: list[dict] = field(default_factory=list)
    week_strip: list[dict] = field(default_factory=list)
    macros: dict = field(default_factory=dict)
    meals: list[dict] = field(default_factory=list)
    in_session: dict | None = None
    flags: list[dict] = field(default_factory=list)
    biomarkers: list[dict] = field(default_factory=list)
    biomarker_panel_date: str | None = None
    race: dict | None = None
    generated_at: str = ""


def calculate_age(athlete: Athlete) -> int:
    """Best-effort age from DOB if available; default to 50 otherwise.

    The placeholder DOB '1969-XX-XX' returns 56 for masters logic.
    """
    dob_str = athlete.raw.get("date_of_birth", "")
    if not dob_str or "X" in str(dob_str):
        # Reasonable masters default consistent with Brooks's profile
        return 56
    try:
        dob = datetime.strptime(str(dob_str), "%Y-%m-%d").date()
        today = date.today()
        return today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )
    except (ValueError, TypeError):
        return 56


def build_meals(
    weight_kg: float,
    workout: Workout | None,
    *,
    phase: str,
    diet: str,
) -> list[dict]:
    """Construct the meal timeline for the day."""
    breakdown = meal_breakdown(workout, phase=phase)
    total_carbs = daily_carbs_grams(weight_kg, workout, phase=phase)

    # Veg-friendly meal copy that picks up on the day's color and session
    color = session_color(workout)
    has_session = workout is not None and workout.duration_min >= 30

    meals = []

    # BREAKFAST
    bf_color = breakdown["breakfast"]
    if has_session:
        bf_note = "Pre-session · light, low-fiber. Greek yogurt, berries, honey. Coffee black."
    else:
        bf_note = "Greek yogurt, berries, walnuts, oats. Coffee."
    meals.append({
        "slot": "breakfast",
        "name": "Breakfast",
        "time": "6:30 AM",
        "color": bf_color,
        "carbs_g": MEAL_CARBS_G[bf_color],
        "protein_g": 35,
        "fat_g": 15,
        "note": bf_note,
        "label": "Pre-session · light, low-fiber" if has_session else "Steady start",
    })

    # LUNCH (could be RECOVERY MEAL on green days)
    lunch_color = breakdown["lunch"]
    if color == "GREEN":
        lunch_note = (
            "Recovery meal: lentils + brown rice bowl, tahini, roasted veg. "
            "Cottage cheese on the side covers leucine. Citrus or pepper for "
            "vitamin C → iron uptake."
        )
        lunch_label = "Recovery window · complete plant protein"
    else:
        lunch_note = "Hummus + whole-grain wrap with greens, sprouts, avocado. Side of fruit."
        lunch_label = "Steady mid-day fuel"

    meals.append({
        "slot": "lunch",
        "name": "Lunch",
        "time": "12:30 PM",
        "color": lunch_color,
        "carbs_g": MEAL_CARBS_G[lunch_color],
        "protein_g": 50 if color == "GREEN" else 40,
        "fat_g": 25,
        "note": lunch_note,
        "label": lunch_label,
    })

    # AFTERNOON SNACK
    snack_color = breakdown["afternoon_snack"]
    meals.append({
        "slot": "afternoon_snack",
        "name": "Afternoon Snack",
        "time": "4:00 PM",
        "color": snack_color,
        "carbs_g": MEAL_CARBS_G[snack_color],
        "protein_g": 20,
        "fat_g": 12,
        "note": "Apple + 2 tbsp almond butter, or Greek yogurt with walnuts + flaxseed.",
        "label": "Bridge to dinner",
    })

    # DINNER
    dinner_color = breakdown["dinner"]
    meals.append({
        "slot": "dinner",
        "name": "Dinner",
        "time": "7:00 PM",
        "color": dinner_color,
        "carbs_g": MEAL_CARBS_G[dinner_color],
        "protein_g": 45,
        "fat_g": 25,
        "note": (
            "Tofu stir-fry over quinoa, broccolini, bok choy, sesame oil. "
            "Quinoa + tofu = complete amino acids. Avocado for fat."
        ),
        "label": "Veg-forward, balanced",
    })

    # If the day's total carbs from meal-color buckets falls short of the
    # prescribed daily total, fold the difference into dinner (athlete's
    # easiest place to top up complex carbs).
    meal_carb_sum = sum(m["carbs_g"] for m in meals)
    deficit = total_carbs - meal_carb_sum
    if deficit > 15:
        meals[-1]["carbs_g"] += deficit
        meals[-1]["note"] += f" Add ~{deficit}g extra complex carbs (rice, sweet potato)."

    return meals


def build_week_strip(
    workouts: list[Workout],
    today: date,
) -> list[dict]:
    """Build the 7-day color strip for the dashboard."""
    # Find Monday of the current week
    monday = today - timedelta(days=today.weekday())
    days = []
    labels = ["M", "T", "W", "T", "F", "S", "S"]
    for i in range(7):
        day = monday + timedelta(days=i)
        wo = workout_for(day, workouts)
        days.append({
            "date": day.isoformat(),
            "label": labels[i],
            "color": session_color(wo).lower(),
            "is_today": day == today,
            "title": wo.title if wo else "Rest",
            "duration_min": int(wo.duration_min) if wo else 0,
        })
    return days


def build_day_plan(
    target_date: date,
    *,
    athlete: Athlete,
    workouts: list[Workout],
    panel: BloodworkPanel | None,
) -> DayPlan:
    """Build a complete day plan for the given date."""
    primary = workout_for(target_date, workouts)
    all_today = all_workouts_for(target_date, workouts)

    color = session_color(primary)
    weight = athlete.weight_kg
    age = calculate_age(athlete)
    phase = athlete.phase
    diet = athlete.diet

    # Macros
    carbs_g = daily_carbs_grams(weight, primary, phase=phase)
    protein_g = daily_protein_grams(weight, age=age, diet=diet, phase=phase)
    fat_g = round(daily_fat_g_per_kg(phase=phase) * weight)
    cals = daily_calories_estimate(weight, carbs_g, protein_g, fat_g)

    # Meals
    meals = build_meals(weight, primary, phase=phase, diet=diet)

    # In-session fuel plan
    session = in_session_plan(
        primary,
        gut_trained_to=athlete.gut_trained_to_g_hr,
    )
    session_dict = None
    if session is not None:
        session_dict = {
            "target_carbs_g_per_hr": session.target_carbs_g_per_hr,
            "total_carbs_g": session.total_carbs_g,
            "sodium_mg": session.sodium_mg,
            "glucose_pct": session.glucose_pct,
            "fructose_pct": session.fructose_pct,
            "bottle_count": session.bottle_count,
            "note": session.note,
            "steps": [asdict(s) for s in session.steps],
        }

    # Flags
    flags = vegetarian_flags(panel, diet=diet)

    # Biomarkers list for the panel
    biomarkers_out = []
    panel_date_str = None
    if panel is not None:
        panel_date_str = panel.date.isoformat()
        for m in panel.markers:
            if m.value is None:
                continue
            biomarkers_out.append({
                "name": m.name,
                "value": m.value,
                "unit": m.unit,
                "status": m.status,
                "trend": m.trend,
            })

    # Race countdown
    race_dict = None
    next_a = athlete.next_a_race
    if next_a is not None:
        race_dict = {
            "name": next_a.name,
            "date": next_a.date.isoformat(),
            "days_to_go": (next_a.date - target_date).days,
            "distance": next_a.distance,
            "priority": next_a.priority,
        }

    # Workout dicts
    primary_dict = None
    if primary is not None:
        primary_dict = {
            "title": primary.title,
            "sport": primary.sport,
            "duration_min": int(primary.duration_min),
            "tss": primary.tss,
            "intensity_factor": primary.intensity_factor,
            "is_completed": primary.is_completed,
        }

    secondary_dicts = [
        {
            "title": w.title,
            "sport": w.sport,
            "duration_min": int(w.duration_min),
        }
        for w in all_today if w is not primary
    ]

    return DayPlan(
        date=target_date.isoformat(),
        weekday=target_date.strftime("%A"),
        phase=phase,
        day_color=color,
        workout=primary_dict,
        secondary_workouts=secondary_dicts,
        week_strip=build_week_strip(workouts, target_date),
        macros={
            "carbs_g": carbs_g,
            "protein_g": protein_g,
            "fat_g": fat_g,
            "calories": cals,
            "carbs_g_per_kg": round(carbs_g / weight, 2),
            "protein_g_per_kg": round(protein_g / weight, 2),
        },
        meals=meals,
        in_session=session_dict,
        flags=flags,
        biomarkers=biomarkers_out,
        biomarker_panel_date=panel_date_str,
        race=race_dict,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def run_engine(
    target_date: date | None = None,
    *,
    athlete_path: Path | str = "data/athlete.yaml",
    bloodwork_dir: Path | str = "data/bloodwork",
    output_path: Path | str = "data/today.json",
    ics_text: str | None = None,
) -> DayPlan:
    """Top-level entry point: build the day plan and write JSON output."""
    target_date = target_date or date.today()

    athlete = load_athlete(athlete_path)
    panel = latest_panel(bloodwork_dir)

    if ics_text is None:
        ics_text = fetch_ics()

    workouts = parse_workouts(
        ics_text,
        lookback_days=7,
        lookahead_days=14,
        today=target_date,
    )

    plan = build_day_plan(
        target_date,
        athlete=athlete,
        workouts=workouts,
        panel=panel,
    )

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(plan), indent=2, default=str))

    return plan
