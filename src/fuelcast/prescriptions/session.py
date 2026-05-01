"""In-session fueling — the bottle-math layer.

For sessions over ~60 minutes, FuelCast prescribes carbs per hour, sodium per
hour, and a multi-source carb mix above 60 g/hr to take advantage of separate
intestinal transporters (SGLT1 for glucose/maltodextrin + GLUT5 for fructose).

References:
- Jeukendrup A. (2014). Carbohydrate intake during exercise.
- Jeukendrup A. & Killer S. (2010). The myths surrounding pre-exercise
  carbohydrate feedings.
- Maughan & Shirreffs (2010). Hydration and electrolyte balance.
"""

from __future__ import annotations

from dataclasses import dataclass

from fuelcast.sources.trainingpeaks import Workout


@dataclass
class FuelStep:
    """One step in the in-session fueling timeline."""

    when: str           # "−15 min" | "0–60 min" | "30 min" | "Run-off"
    what: str           # human-readable instruction
    carbs_g: int = 0    # estimated carb grams in this step


@dataclass
class SessionFuelPlan:
    target_carbs_g_per_hr: int
    total_carbs_g: int
    sodium_mg: int
    glucose_pct: int
    fructose_pct: int
    bottle_count: int
    steps: list[FuelStep]
    note: str = ""


def in_session_carbs_g_per_hr(
    workout: Workout,
    *,
    gut_trained_to: int = 60,
) -> int:
    """How many grams of carbs per hour for this session.

    Tiered by duration:
      < 60 min  → 0 (real food only, no in-session needed)
      60-90 min → 30 g/hr (sports drink usually sufficient)
      90-120 min → 60 g/hr (single-source OK)
      2-3 hr    → 75 g/hr (multi-source recommended)
      3+ hr     → up to 90+ g/hr (multi-source required)

    Capped at the athlete's gut-trained tolerance.
    """
    if workout.duration_min < 60:
        return 0

    dur_hr = workout.duration_hr

    if dur_hr < 1.5:
        base = 30
    elif dur_hr < 2:
        base = 60
    elif dur_hr < 3:
        base = 75
    else:
        base = 90

    # Quality / threshold sessions warrant more carbs at the top end
    if workout.intensity_factor and workout.intensity_factor >= 0.85:
        base = min(base + 15, 90)

    return min(base, gut_trained_to)


def multi_source_carb_mix(g_per_hr: int) -> tuple[int, int]:
    """Return (glucose_pct, fructose_pct) for the given carb rate.

    Above 60 g/hr we recommend multi-source (2:1 glucose:fructose) to leverage
    a second transporter pathway and avoid GI distress.
    """
    if g_per_hr <= 60:
        return (100, 0)
    return (67, 33)


def sodium_mg_per_hr(workout: Workout, *, hot_day: bool = False) -> int:
    """Sodium target in mg/hr.

    Generic baseline: 300-700 mg/hr for endurance work. Hot day or known high
    sweat rate → top of range. Sub-90-minute sessions usually don't need it.
    """
    if workout.duration_min < 75:
        return 0
    if hot_day:
        return 700
    return 500


def in_session_plan(
    workout: Workout | None,
    *,
    gut_trained_to: int = 60,
    hot_day: bool = False,
) -> SessionFuelPlan | None:
    """Build a complete in-session fueling plan for a workout."""
    if workout is None or workout.duration_min < 60:
        return None

    rate = in_session_carbs_g_per_hr(workout, gut_trained_to=gut_trained_to)
    if rate == 0:
        return None

    glucose_pct, fructose_pct = multi_source_carb_mix(rate)
    total = round(rate * workout.duration_hr)
    sodium = round(sodium_mg_per_hr(workout, hot_day=hot_day) * workout.duration_hr)

    # Bottle math: typical 750 ml bottle holds ~75g of mixed carb at strong concentration
    bottle_count = max(1, round(total / 75))

    steps: list[FuelStep] = []

    # Pre-session sip (if longer than 90 min)
    if workout.duration_hr >= 1.5:
        steps.append(
            FuelStep(
                when="−15 min",
                what=f"Sip ~200 ml sports drink (≈ 15g carbs) before starting",
                carbs_g=15,
            )
        )

    # First bottle / first hour
    bottle_carbs = min(90, round(rate))
    steps.append(
        FuelStep(
            when=f"0–{min(60, int(workout.duration_min))} min",
            what=(
                f"Bottle 1: ~{bottle_carbs}g carbs in 750 ml + ½ tsp salt. "
                "Drain steadily through first hour."
            ),
            carbs_g=bottle_carbs,
        )
    )

    # Mid-session gel for longer sessions
    if workout.duration_hr >= 1.5:
        gel_size = 25 if rate < 75 else 30
        steps.append(
            FuelStep(
                when="45 min",
                what=(
                    f"1 gel (≈ {gel_size}g)"
                    + (", multi-source (glucose + fructose 2:1)" if rate > 60 else "")
                ),
                carbs_g=gel_size,
            )
        )

    # Additional bottles for 2+ hr sessions
    if workout.duration_hr >= 2:
        steps.append(
            FuelStep(
                when="60–120 min",
                what=(
                    f"Bottle 2: ~{bottle_carbs}g carbs + electrolytes. "
                    "Solid food (banana, fig bar) optional."
                ),
                carbs_g=bottle_carbs,
            )
        )

    # 3+ hr → solid food window
    if workout.duration_hr >= 3:
        steps.append(
            FuelStep(
                when="120+ min",
                what="Real-food calories OK now: rice cake, fig bar, salted potatoes.",
                carbs_g=40,
            )
        )

    # Run-off / brick
    title_lower = workout.title.lower()
    if "brick" in title_lower or "transition" in title_lower or " + run" in title_lower:
        steps.append(
            FuelStep(
                when="Run-off",
                what="Sip ~100 ml sports drink at transition; sodium tab if hot.",
                carbs_g=10,
            )
        )

    steps.append(
        FuelStep(
            when="Total",
            what=f"≈ {total}g carbs · ≈ {sodium} mg sodium across the session",
        )
    )

    note = ""
    if rate >= 75:
        note = (
            "Multi-source carb mix recommended at this rate. "
            "Train your gut — start at lower g/hr in early sessions and build up."
        )

    return SessionFuelPlan(
        target_carbs_g_per_hr=rate,
        total_carbs_g=total,
        sodium_mg=sodium,
        glucose_pct=glucose_pct,
        fructose_pct=fructose_pct,
        bottle_count=bottle_count,
        steps=steps,
        note=note,
    )
