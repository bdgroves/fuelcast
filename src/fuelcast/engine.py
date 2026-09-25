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
from fuelcast.localtime import local_today
from fuelcast.prescriptions.carbs import (
    MEAL_CARBS_G,
    daily_carbs_grams,
    meal_breakdown,
    session_color,
)
from fuelcast.prescriptions.energy import energy_flag, estimate_expenditure, fit_macros
from fuelcast.prescriptions.gut_training import build_gut_plan, gut_plan_flag
from fuelcast.prescriptions.protein import daily_protein_g_per_kg
from fuelcast.prescriptions.session import in_session_plan
from fuelcast.sources import hrv4training, weather
from fuelcast.sources.garmin import (
    AthleteState,
    GarminLoadUnavailable,
    fetch_athlete_state,
    fetch_daily_tss,
    series_bounds,
)
from fuelcast.sources.trainingpeaks import (
    Workout,
    all_workouts_for,
    fetch_ics,
    parse_workouts,
    workout_for,
)
from fuelcast.training_load import (
    carb_adjustment_pct,
    compute_training_load,
    latest_load,
    training_load_flag,
    tsb_state,
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
    energy: dict | None = None
    body: dict | None = None
    gut_training: dict | None = None
    hrv: dict | None = None
    session_source: str = "planned"
    planned_workout: dict | None = None
    weather: dict | None = None
    hydration_l: float | None = None
    training_load: dict | None = None
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
    targets: dict | None = None,
) -> list[dict]:
    """Construct the meal timeline for the day.

    ``targets`` carries the day's final carbs/protein/fat in grams. When
    given, every meal is scaled so the meals actually sum to the day.

    Previously the per-meal protein and fat were fixed constants that summed
    to 140 g protein against a daily prescription of 218 g, and any carb
    shortfall was dumped wholesale into dinner — which is how the page came
    to recommend 390 g of carbohydrate in a single evening meal.
    """
    breakdown = meal_breakdown(workout, phase=phase)
    total_carbs = ((targets or {}).get("carbs_g")
                   or daily_carbs_grams(weight_kg, workout, phase=phase))

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
    # Scale each macro across the meals so they sum to the day's target,
    # preserving the traffic-light shape (a GREEN lunch stays the biggest
    # carb meal). Rounding residue lands on the meal that is already largest
    # for that macro, where a few grams are least noticeable.
    day_totals = {"carbs_g": total_carbs}
    if targets:
        day_totals.update({k: targets[k] for k in ("protein_g", "fat_g") if targets.get(k)})
    for key, total in day_totals.items():
        current = sum(m[key] for m in meals)
        if not current or not total:
            continue
        f = total / current
        for m in meals:
            m[key] = round(m[key] * f)
        residue = total - sum(m[key] for m in meals)
        if residue:
            max(meals, key=lambda m: m[key])[key] += residue

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


# Garmin buckets activities as run/ride/swim/strength/yoga; the rest of
# FuelCast speaks TrainingPeaks' vocabulary.
GARMIN_TO_TP_SPORT = {"run": "run", "ride": "bike", "swim": "swim",
                      "strength": "strength", "yoga": "other", "other": "other"}


def build_day_plan(
    target_date: date,
    *,
    athlete: Athlete,
    workouts: list[Workout],
    panel: BloodworkPanel | None,
    daily_tss: dict[str, float] | None = None,
    athlete_state: AthleteState | None = None,
    hrv_state: hrv4training.HRVState | None = None,
    weather_today: weather.Weather | None = None,
) -> DayPlan:
    """Build a complete day plan for the given date."""
    planned = workout_for(target_date, workouts)
    primary = planned

    # ─── Plan vs reality ─────────────────────────────────────────────
    # If Garmin shows training completed today, fuel the day that actually
    # happened. A planned 60-minute run that became a 30-minute ride burns
    # half as much, so the rest of the day's food should reflect that.
    # Rule: once any training is recorded, it replaces the plan. A second
    # planned session later in the day is picked up by the next refresh
    # once it too has been done.
    done_today = [a for a in ((athlete_state.activities if athlete_state else ()) or ())
                  if a.get("local_date") == target_date.isoformat()]
    measured_session_kcal = None
    if done_today:
        longest = max(done_today, key=lambda a: float(a["duration_min"]))
        tss_vals = [float(a["tss"]) for a in done_today if a.get("tss") is not None]
        primary = Workout(
            date=target_date,
            title=" + ".join(a.get("name") or a["sport"].title() for a in done_today),
            sport=GARMIN_TO_TP_SPORT.get(longest["sport"], "other"),
            duration_min=sum(float(a["duration_min"]) for a in done_today),
            tss=sum(tss_vals) if tss_vals else None,
            is_completed=True,
        )
        measured_session_kcal = sum(float(a.get("kcal_net") or 0) for a in done_today)
    all_today = all_workouts_for(target_date, workouts)

    color = session_color(primary)
    state = athlete_state or AthleteState()
    # Measured weight (7-day mean from the Index scale) beats the hand-edited
    # value in athlete.yaml, whose comment said "update monthly" — meaning
    # every per-kg macro drifted between edits.
    weight = state.weight_kg or athlete.weight_kg
    # Say what the weight actually is — a genuine mean, or one old reading.
    if state.weight_kg:
        weight_source = f"garmin scale · {state.weight_basis or 'recent'}"
    else:
        weight_source = "athlete.yaml"
    age = calculate_age(athlete)
    phase = athlete.phase
    diet = athlete.diet

    # Training load — compute CTL/ATL/TSB from completed workout history.
    #
    # Preference order:
    #   1. Garmin's measured daily TSS. It covers ~180 days, so the 42-day
    #      filter is fully converged and no manual seed is needed — the
    #      window itself carries the fitness. initial_ctl/atl are reset to
    #      zero here on purpose: seeding *and* supplying real history would
    #      double-count the same fitness.
    #   2. The athlete.yaml seed plus the TrainingPeaks iCal window. This
    #      is the original path and is only correct when the seed date is
    #      close to the feed's 7-day lookback. When it drifts months away
    #      (as it had), every uncovered day scores TSS 0 and decays CTL to
    #      nothing — which is the bug this ordering exists to avoid.
    tl_config = athlete.raw.get("training_load_seed", {})
    initial_ctl = float(tl_config.get("ctl", 0.0))
    initial_atl = float(tl_config.get("atl", 0.0))
    # If a seed_date is provided, use that as our window start — we trust
    # the seed values *at* that date, then accumulate forward from workouts.
    seed_days_param = 60
    seed_date_str = tl_config.get("date")
    if seed_date_str:
        try:
            sd = datetime.strptime(str(seed_date_str), "%Y-%m-%d").date()
            seed_days_param = max(1, (target_date - sd).days)
        except (ValueError, TypeError):
            pass

    load_source = "trainingpeaks_ical"
    if daily_tss:
        first, last = series_bounds(daily_tss)
        seed_days_param = max(1, (target_date - first).days)
        initial_ctl = 0.0
        initial_atl = 0.0
        load_source = "garmin"
        # Surfaced in the log because a silent switch between two load
        # sources that disagree by an order of magnitude is not something
        # that should ever have to be inferred from the numbers.
        print(
            f"training load: garmin series {first}..{last} "
            f"({len(daily_tss)} days, {sum(1 for v in daily_tss.values() if v > 0)} active)"
        )
    else:
        print(
            f"training load: no garmin series — falling back to iCal "
            f"with seed CTL {initial_ctl} ATL {initial_atl} from {seed_date_str}"
        )

    load_history = compute_training_load(
        workouts,
        target_date=target_date,
        seed_days=seed_days_param,
        initial_ctl=initial_ctl,
        initial_atl=initial_atl,
        daily_tss=daily_tss,
    )
    current_load = latest_load(load_history)

    # ─── Macros ─────────────────────────────────────────────────────
    # Carbs are still periodized by session — that part of the original
    # design was right. What's new is that they now have a ceiling: the
    # energy model estimates what today costs, applies the goal, and fits
    # the macros inside that target. Before this, calories were simply
    # whatever the per-kg macros summed to, and athlete.yaml's
    # "goal: weight_loss" was never read by any code.
    carbs_periodized = daily_carbs_grams(weight, primary, phase=phase)

    # Recovery-aware carb bump from TSB (heavy load / overreached).
    carb_bump_pct = 0
    if current_load is not None:
        carb_bump_pct = carb_adjustment_pct(current_load.tsb)
        if carb_bump_pct > 0:
            carbs_periodized = round(carbs_periodized * (1 + carb_bump_pct / 100))

    # The per-kg rate, not grams: daily_protein_grams rounds, so passing a
    # weight of 1.0 would turn 2.4 g/kg into 2.
    protein_per_kg = daily_protein_g_per_kg(age=age, diet=diet, phase=phase)
    goal = athlete.raw.get("goal", "maintenance")
    physical = athlete.raw.get("physical", {})

    expenditure = estimate_expenditure(
        session_sport=primary.sport if primary else None,
        session_duration_hr=primary.duration_hr if primary else 0.0,
        measured_tdee=state.tdee_kcal,
        measured_training_kcal=state.training_kcal,
        measured_bmr=state.bmr_kcal,
        kcal_per_hour=state.kcal_per_hour,
        weight_kg=weight,
        height_cm=float(physical.get("height_cm") or 175),
        age=age,
        sex=athlete.raw.get("sex", "M"),
        measured_session_kcal=measured_session_kcal,
    )
    energy = fit_macros(
        goal=goal,
        color=color,
        expenditure=expenditure,
        weight_kg=weight,
        ffm_kg=state.ffm_kg,
        protein_g_per_kg=protein_per_kg,
        carbs_periodized_g=carbs_periodized,
        weight_trend_kg_wk=state.weight_trend_kg_wk,
        sex=athlete.raw.get("sex", "M"),
        load_state=tsb_state(current_load.tsb) if current_load else None,
        hrv_reason=hrv4training.recovery_level(hrv_state)[1] if hrv_state else None,
    )
    carbs_g, protein_g, fat_g = energy.carbs_g, energy.protein_g, energy.fat_g
    cals = energy.target_kcal
    print(f"energy: {goal} · burn ~{expenditure.total_kcal} ({expenditure.method}) · "
          f"target {cals} ({energy.balance_kcal:+}) · EA {energy.energy_availability}"
          + (f" · floors: {', '.join(energy.floors_binding)}" if energy.floors_binding else ""))

    # Meals now sum to the day's real totals.
    meals = build_meals(weight, primary, phase=phase, diet=diet,
                        targets={"carbs_g": carbs_g, "protein_g": protein_g, "fat_g": fat_g})

    # In-session fuel plan
    hot = bool(weather_today and weather_today.hot)
    session = in_session_plan(
        primary,
        gut_trained_to=athlete.gut_trained_to_g_hr,
        hot_day=hot,
    )

    # Daily fluid target: ~35 ml/kg baseline plus sweat replacement for the
    # session, more in heat. Replaces the page's fixed "3+ L".
    sess_hr = primary.duration_hr if primary else 0.0
    hydration_l = round((0.035 * weight + sess_hr * (0.9 if hot else 0.6)) * 2) / 2
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

    # Training load flag — recovery state surfacing
    tl_flag = training_load_flag(current_load)
    if tl_flag is not None:
        # Insert at top so it's the first thing the athlete sees
        flags.insert(0, tl_flag)

    # Energy position leads the card — it's the number that now drives
    # everything else on it.
    flags.insert(0, energy_flag(energy))

    if done_today:
        def _label(w):
            # TrainingPeaks titles often already carry the duration.
            mins = f"{w.duration_min:.0f} min"
            return w.title if mins in w.title else f"{w.title} ({mins})"
        did = _label(primary)
        if planned is not None:
            text = (f"Planned {_label(planned)} · you did {did}. "
                    "The rest of today is fuelled for what you actually did.")
        else:
            text = f"Recorded {did}. The rest of today is fuelled for it."
        flags.insert(1, {"level": "ok", "title": "Session done", "text": text})
    if hot:
        flags.append({"level": "warn", "title": "Hot day",
                      "text": f"Forecast high {weather_today.high_f:.0f}°F — extra sodium in "
                              f"bottles and about {hydration_l:g} L of fluid across the day."})

    # HRV4Training's own daily verdict, shown as it gave it.
    hrv_dict = None
    if hrv_state and hrv_state.latest:
        lr = hrv_state.latest
        stale = not hrv_state.usable_today
        hrv_dict = {
            "date": lr.day.isoformat(),
            "age_days": hrv_state.age_days,
            "usable_today": hrv_state.usable_today,
            "rmssd": lr.rmssd,
            "hr": lr.hr,
            "verdict": lr.verdict,
            "advice": lr.advice,
            "rolling_ln": hrv_state.rolling_ln,
            "baseline_ln": hrv_state.baseline_ln,
            "baseline_sd": hrv_state.baseline_sd,
            "readings_30d": hrv_state.readings_30d,
            "history": [
                {"date": h.day.isoformat(), "rmssd": h.rmssd, "verdict": h.verdict}
                for h in hrv_state.history
            ],
        }
        if stale:
            text = (f"Last reading {hrv_state.age_days} days ago ({lr.rmssd:.0f} ms) — "
                    "take one tomorrow morning so today's plan can use it.")
            level = "ok"
        else:
            advice = lr.advice or "no verdict"
            text = f"rMSSD {lr.rmssd:.0f} ms this morning. HRV4Training: {advice}."
            level = "warn" if lr.verdict in ("below", "unusually_high") else "ok"
        flags.insert(1, {"level": level, "title": "HRV", "text": text})
    for note in energy.adjustments:
        flags.append({"level": "warn" if "rising" in note or "faster" in note else "ok",
                      "title": "Energy adjustment", "text": note})

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

    # ─── Gut training ────────────────────────────────────────────────
    # in_session_carbs_g_per_hr caps at gut_trained_to_g_hr — correct, but
    # static. Nothing raised it and nothing told the athlete to try, so the
    # tool would have prescribed the same tolerance on race day as eight
    # months out.
    gut_plan = build_gut_plan(
        today=target_date,
        days_to_race=race_dict["days_to_go"] if race_dict else None,
        current_g_hr=athlete.gut_trained_to_g_hr,
        race_distance=race_dict["distance"] if race_dict else None,
        session_duration_min=primary.duration_min if primary else 0.0,
    )
    gut_dict = None
    if gut_plan is not None:
        gut_dict = {
            "current_g_hr": gut_plan.current_g_hr,
            "race_target_g_hr": gut_plan.race_target_g_hr,
            "gap_g_hr": gut_plan.gap_g_hr,
            "weeks_to_race": gut_plan.weeks_to_race,
            "feasible": gut_plan.feasible,
            "today_is_rehearsal": gut_plan.today_is_rehearsal,
            "today_target_g_hr": gut_plan.today_target_g_hr,
            "note": gut_plan.note,
            "ladder": [{"target_g_hr": x.target_g_hr, "start_date": x.start_date.isoformat(),
                        "weeks": x.weeks, "is_current": x.is_current} for x in gut_plan.ladder],
        }
        gf = gut_plan_flag(gut_plan)
        if gf:
            flags.append(gf)

    # Energy + body block for the dashboard
    energy_dict = {
        "goal": energy.goal,
        "expenditure_kcal": expenditure.total_kcal,
        "rest_baseline_kcal": expenditure.rest_baseline_kcal,
        "session_kcal": expenditure.session_kcal,
        "method": expenditure.method,
        "target_kcal": energy.target_kcal,
        "balance_kcal": energy.balance_kcal,
        "energy_availability": energy.energy_availability,
        "ea_floor": energy.ea_floor,
        "floors_binding": energy.floors_binding,
        "adjustments": energy.adjustments,
        "protein_basis": energy.protein_basis,
        "carbs_periodized_g": carbs_periodized,
    }
    body_dict = {
        "weight_kg": round(weight, 1),
        "weight_lb": round(weight * 2.20462, 1),
        "weight_source": weight_source,
        "trend_kg_per_week": state.weight_trend_kg_wk,
        "trend_lb_per_week": (round(state.weight_trend_kg_wk * 2.20462, 2)
                              if state.weight_trend_kg_wk is not None else None),
        "body_fat_pct": state.body_fat_pct,
        "ffm_kg": state.ffm_kg,
        "rhr_7d": state.rhr_7d,
        "hrv_last_night": state.hrv_last_night,
        "hrv_status": state.hrv_status,
        "notes": list(state.notes),
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

    # Training load dict for the dashboard
    training_load_dict = None
    if current_load is not None:
        # Send last 30 days of history for sparkline rendering
        recent_history = load_history[-30:] if len(load_history) > 30 else load_history
        training_load_dict = {
            "ctl": current_load.ctl,
            "atl": current_load.atl,
            "tsb": current_load.tsb,
            "tss_today": current_load.tss_today,
            "state": tsb_state(current_load.tsb),
            "carb_bump_pct": carb_bump_pct,
            # Which feed these numbers came from. Consumers should show
            # this: "garmin" and "trainingpeaks_ical" can differ by an
            # order of magnitude, and a reader deserves to know which
            # one produced the fatigue warning in front of them.
            "source": load_source,
            "days_modelled": len(load_history),
            "history": [
                {"date": h.date.isoformat(), "ctl": h.ctl, "atl": h.atl,
                 "tsb": h.tsb, "tss": h.tss_today}
                for h in recent_history
            ],
        }

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
            "carb_bump_pct": carb_bump_pct,
        },
        meals=meals,
        in_session=session_dict,
        flags=flags,
        biomarkers=biomarkers_out,
        biomarker_panel_date=panel_date_str,
        race=race_dict,
        training_load=training_load_dict,
        energy=energy_dict,
        body=body_dict,
        gut_training=gut_dict,
        hrv=hrv_dict,
        session_source="actual" if done_today else "planned",
        planned_workout=({"title": planned.title, "sport": planned.sport,
                          "duration_min": int(planned.duration_min)}
                         if planned is not None else None),
        weather=({"location": weather_today.location, "high_f": weather_today.high_f,
                  "precip_pct": weather_today.precip_pct, "hot": weather_today.hot}
                 if weather_today and weather_today.high_f is not None else None),
        hydration_l=hydration_l,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def run_engine(
    target_date: date | None = None,
    *,
    athlete_path: Path | str = "data/athlete.yaml",
    bloodwork_dir: Path | str = "data/bloodwork",
    output_path: Path | str = "data/today.json",
    ics_text: str | None = None,
    hrv_path: Path | str = "data/hrv4training.csv",
) -> DayPlan:
    """Top-level entry point: build the day plan and write JSON output."""
    target_date = target_date or local_today()

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

    # Measured training load from Garmin. Degrading to the iCal path is
    # acceptable (it is the old behaviour); silently degrading is not, so
    # the reason is always printed.
    daily_tss = None
    try:
        daily_tss = fetch_daily_tss(today=target_date)
    except GarminLoadUnavailable as e:
        print(f"training load: garmin feed unusable — {e}")

    # Body composition, measured energy and recovery ride in the same feed.
    # Fetched separately from the TSS series so a problem with one never
    # suppresses the other. Never raises — an empty state means every
    # consumer falls back to its configured value.
    athlete_state = fetch_athlete_state()

    # HRV4Training export. Arrives by manual export or automated Dropbox
    # fetch; either way it lands at the same path. A missing file is fine.
    weather_today = weather.fetch(today=target_date)
    print(f"weather: {weather_today.location or '?'} high {weather_today.high_f}°F"
          + (" — HOT" if weather_today.hot else "")
          + (f" ({weather_today.note})" if weather_today.note else ""))

    hrv_state = hrv4training.load(hrv_path, today=target_date)
    for note in hrv_state.notes:
        print(f"hrv: {note}")
    if hrv_state.latest:
        lr = hrv_state.latest
        print(f"hrv: {lr.day} rMSSD {lr.rmssd} — {lr.verdict or 'no verdict'} "
              f"({'used' if hrv_state.usable_today else 'shown only'}), "
              f"{hrv_state.readings_30d} readings in 30 days")
    for note in athlete_state.notes:
        print(f"athlete state: {note}")

    plan = build_day_plan(
        target_date,
        athlete=athlete,
        workouts=workouts,
        panel=panel,
        daily_tss=daily_tss,
        athlete_state=athlete_state,
        hrv_state=hrv_state,
        weather_today=weather_today,
    )

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(plan), indent=2, default=str))

    return plan
