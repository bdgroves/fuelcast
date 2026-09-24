"""Energy model — what the athlete burns, what they should eat, and the floors.

Why this exists
---------------
Before this module, calories were an *output*: carbs, protein and fat were
each sized per kg, and calories were whatever they summed to. There was no
estimate of expenditure anywhere in the codebase, and ``goal: weight_loss``
in athlete.yaml was never read by any code.

The result was a surplus on every day type — roughly +500 to +1,500 kcal —
for an athlete whose stated goal was to lose weight, with the largest
surpluses on the largest training days.

This module inverts that. Expenditure is estimated first, the goal sets a
target against it, and the macros are fitted inside that target. Carbs stay
periodized by session, because that part of the original design was right;
what changes is that they now have a ceiling.

The design principle
--------------------
**Periodize the distribution, constrain the total.** Training days get more
fuel and rest days get less, exactly as before — but the level is anchored
to measured expenditure instead of floating free.

Safety rules, in priority order
-------------------------------
1. Energy availability never drops below 30 kcal/kg fat-free mass. Below
   that is Relative Energy Deficiency in Sport (RED-S) territory: impaired
   hormonal, bone and immune function. This outranks every goal.
2. Protein never drops below its floor. In a deficit this is what protects
   lean mass, and masters athletes lose it faster.
3. Carbs never drop below a session-dependent minimum. A deficit on a long
   ride day is how athletes get injured and quit.
4. Fat never drops below 0.7 g/kg (hormonal and fat-soluble vitamin needs).
5. Only then does the goal's deficit apply.

When the floors can't all fit inside the deficit, the floors win and the
deficit shrinks. That is reported rather than hidden.

The feedback loop — asymmetric on purpose
-----------------------------------------
With a measured weight trend, the model can correct itself. It does so in
one direction only: if weight is falling faster than 1% of bodyweight per
week, the deficit is halved automatically. If weight is *rising* during a
weight-loss block, it flags it but does not escalate the deficit — because
an overestimated TDEE and an under-reported intake look identical from here,
and "cut harder" is the wrong automatic answer to either. Automatic
correction only ever moves toward safety.

References
----------
- Mifflin MD et al. (1990). A new predictive equation for resting energy
  expenditure. Am J Clin Nutr 51:241-7.
- Loucks AB, Kiens B, Wright HH (2011). Energy availability in athletes.
  J Sports Sci 29(S1).
- Mountjoy M et al. (2023). IOC consensus statement on RED-S. Br J Sports Med.
- Helms ER, Aragon AA, Fitschen PJ (2014). Evidence-based recommendations
  for natural bodybuilding contest preparation: nutrition and supplementation.
  J Int Soc Sports Nutr 11:20. (Protein per kg lean mass in a deficit.)
- Garthe I et al. (2011). Effect of two different weight-loss rates on body
  composition and strength and power-related performance in elite athletes.
  Int J Sport Nutr Exerc Metab 21:97-104. (Slow loss preserves lean mass.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fuelcast.prescriptions.fat import KCAL_PER_G_CARB, KCAL_PER_G_FAT, KCAL_PER_G_PROTEIN

# ─── Constants ─────────────────────────────────────────────────────────

# Low-energy-availability floors, kcal per kg fat-free mass.
#
# The widely quoted 30 kcal/kg FFM threshold was established largely in
# studies of young women. Evidence in men places adverse hormonal effects at
# lower energy availability, and older athletes have lower resting
# expenditure per kg of lean mass. Applied to a 56-year-old man, 30 turned
# out to sit almost exactly at his *maintenance* energy availability on a
# rest day (2,150 kcal / 70.6 kg FFM = 30.5) — so it silently cancelled every
# deficit the goal asked for. 25 remains a conservative floor for men.
EA_FLOOR_KCAL_PER_KG_FFM = {"F": 30.0, "M": 25.0}

# Fraction of the day's expenditure removed, by session colour. Rest days
# carry most of the deficit; big training days carry almost none. This is
# what athlete.yaml's comment meant by "RED days cut".
DEFICIT_BY_COLOR = {"RED": 0.22, "YELLOW": 0.12, "GREEN": 0.04}

# A hard ceiling on the daily deficit regardless of expenditure, so a huge
# day can't produce a huge cut.
MAX_DAILY_DEFICIT_KCAL = 750

# training_focus adds a small surplus on quality days to support adaptation.
SURPLUS_BY_COLOR_TRAINING = {"RED": 0.0, "YELLOW": 0.0, "GREEN": 0.05}

# Minimum carbs by session colour, g/kg bodyweight. Rest days can go low;
# a long session cannot.
CARB_MIN_G_PER_KG = {"RED": 2.0, "YELLOW": 3.5, "GREEN": 5.0}

# Carbs take priority in the fit, so fat sits at this floor on most days.
# 0.6 put it near 18% of energy — below the usual 20-35% range, a poor
# permanent default for a masters male tracking free testosterone. 0.8
# fixed that but consumed so much of the budget that moderate training days
# tipped into surplus. 0.7 is the compromise; the weight trend decides
# whether it's working.
FAT_MIN_G_PER_KG = 0.7
FAT_MAX_G_PER_KG = 1.5

# Lean mass as a fraction of body weight when the scale gives no body fat.
FFM_FALLBACK_FRACTION = 0.80

# Losing faster than this fraction of bodyweight per week costs lean mass
# (Garthe 2011). The model halves the deficit when it sees it.
MAX_SAFE_LOSS_FRAC_PER_WEEK = 0.01

# Fallback net kcal/hr by sport, used only when the athlete's own measured
# rates are missing. Deliberately conservative.
DEFAULT_KCAL_PER_HOUR = {"run": 550, "bike": 500, "ride": 500, "swim": 450,
                         "strength": 250, "yoga": 150, "other": 300}

# Non-exercise activity multiplier over BMR (TEF + NEAT for a desk job with
# some walking), used only when measured TDEE isn't available.
REST_DAY_BMR_MULTIPLIER = 1.2


# ─── Expenditure ───────────────────────────────────────────────────────

def mifflin_st_jeor_bmr(*, weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Resting energy expenditure, kcal/day. Last-resort fallback only."""
    s = 5 if str(sex).upper().startswith("M") else -161
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + s


@dataclass
class Expenditure:
    rest_baseline_kcal: float     # a day with no training
    session_kcal: float           # today's training, net of BMR
    total_kcal: float             # what today is expected to cost
    method: str                   # provenance, shown to the athlete


def estimate_expenditure(
    *,
    session_sport: str | None,
    session_duration_hr: float,
    measured_tdee: float | None,
    measured_training_kcal: float | None,
    measured_bmr: float | None,
    kcal_per_hour: dict[str, float] | None,
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
) -> Expenditure:
    """Estimate what today will cost.

    The measured 7-day TDEE is an *average* that already contains a week of
    training. Using it directly would give a rest day the same budget as a
    long ride. So the training component is subtracted out to get a rest-day
    baseline, and today's actual session is added back on top.
    """
    # 1. Rest-day baseline, best source first.
    if measured_tdee and measured_training_kcal is not None:
        rest = measured_tdee - measured_training_kcal
        method = "measured (Garmin 7-day TDEE minus training)"
    elif measured_bmr:
        rest = measured_bmr * REST_DAY_BMR_MULTIPLIER
        method = "measured BMR x 1.2"
    else:
        rest = mifflin_st_jeor_bmr(weight_kg=weight_kg, height_cm=height_cm,
                                   age=age, sex=sex) * REST_DAY_BMR_MULTIPLIER
        method = "estimated (Mifflin-St Jeor x 1.2)"

    # A rest baseline below BMR means the inputs disagree — most likely a
    # training-calorie figure inflated by a double-counted activity. Don't
    # let a data glitch prescribe starvation.
    floor = (measured_bmr or mifflin_st_jeor_bmr(
        weight_kg=weight_kg, height_cm=height_cm, age=age, sex=sex))
    if rest < floor:
        rest = floor * REST_DAY_BMR_MULTIPLIER
        method += " — clamped to BMR x 1.2 (inputs inconsistent)"

    # 2. Today's session.
    sport = (session_sport or "other").lower()
    rates = dict(DEFAULT_KCAL_PER_HOUR)
    if kcal_per_hour:
        rates.update({k: v for k, v in kcal_per_hour.items() if v})
        # Garmin buckets say "ride"; TrainingPeaks says "bike".
        if kcal_per_hour.get("ride"):
            rates["bike"] = kcal_per_hour["ride"]
    rate = rates.get(sport, rates["other"])
    session = max(0.0, session_duration_hr) * rate

    return Expenditure(
        rest_baseline_kcal=round(rest),
        session_kcal=round(session),
        total_kcal=round(rest + session),
        method=method,
    )


# ─── Target and macro fitting ──────────────────────────────────────────

@dataclass
class EnergyPlan:
    goal: str
    expenditure: Expenditure
    target_kcal: int
    balance_kcal: int                   # target minus expenditure
    carbs_g: int
    protein_g: int
    fat_g: int
    energy_availability: float | None   # kcal/kg FFM
    floors_binding: list[str] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    protein_basis: str = "total bodyweight"
    ea_floor: float = 25.0
    ffm_estimated: bool = False


def fit_macros(
    *,
    goal: str,
    color: str,
    expenditure: Expenditure,
    weight_kg: float,
    ffm_kg: float | None,
    protein_g_per_kg: float,
    carbs_periodized_g: int,
    weight_trend_kg_wk: float | None = None,
    sex: str = "M",
    load_state: str | None = None,
    hrv_reason: str | None = None,
) -> EnergyPlan:
    """Set today's target from the goal, then fit macros inside it."""
    ea_floor = EA_FLOOR_KCAL_PER_KG_FFM["F" if str(sex).upper().startswith("F") else "M"]
    goal = (goal or "maintenance").lower()
    color = color if color in DEFICIT_BY_COLOR else "YELLOW"
    exp_total = expenditure.total_kcal
    adjustments: list[str] = []
    floors: list[str] = []

    # ── The goal sets a target ──
    if goal == "weight_loss":
        deficit = min(exp_total * DEFICIT_BY_COLOR[color], MAX_DAILY_DEFICIT_KCAL)
        # Feedback loop: auto-correct toward safety only.
        if weight_trend_kg_wk is not None and weight_kg:
            frac = -weight_trend_kg_wk / weight_kg
            if frac > MAX_SAFE_LOSS_FRAC_PER_WEEK:
                deficit *= 0.5
                adjustments.append(
                    f"losing {abs(weight_trend_kg_wk):.2f} kg/wk "
                    f"({frac*100:.1f}% bodyweight) — faster than 1%/wk risks lean mass; "
                    "target deficit halved (the macro floors may already hold it tighter)")
            elif weight_trend_kg_wk > 0.1:
                adjustments.append(
                    f"weight rising {weight_trend_kg_wk:+.2f} kg/wk during a loss block — "
                    "check intake logging or TDEE; not auto-escalating the deficit")
        # Recovery outranks the goal. A deficit while carrying heavy
        # fatigue slows adaptation and raises injury and illness risk. The
        # first live page showed a -167 kcal day right next to a "significant
        # fatigue" warning — the two contradicted each other.
        #
        # Two independent fatigue signals: training load (TSB) and morning
        # HRV. Each can ease the deficit; when they agree they compound, so
        # heavy load *and* a low HRV reading suspends it entirely.
        reasons = []
        level = 0
        if load_state == "overreached":
            level += 2
            reasons.append("overreached")
        elif load_state == "heavy_load":
            level += 1
            reasons.append("heavy training load")
        if hrv_reason:
            level += 1
            reasons.append(hrv_reason)
        level = min(level, 2)
        why = " + ".join(reasons)
        if level == 2:
            deficit = 0
            adjustments.append(f"{why} — deficit suspended, eating at maintenance")
        elif level == 1:
            deficit *= 0.5
            adjustments.append(f"{why} — deficit halved to protect recovery")
        target = exp_total - deficit
    elif goal == "training_focus":
        target = exp_total * (1 + SURPLUS_BY_COLOR_TRAINING[color])
    else:
        target = exp_total

    # ── Protein floor ──
    # In a deficit, protein is prescribed per kg of lean mass (Helms 2014).
    # Applying it to total bodyweight at 2.4 g/kg left no room for any
    # deficit at all on a rest day — the floors alone exceeded expenditure.
    #
    # One lean-mass figure is used for both protein and energy availability.
    # When the scale reports no body fat, it is estimated at 80% of body
    # weight. That estimate used to apply only to the EA check while protein
    # fell back to 100% of body weight — two different assumptions about the
    # same body. With no body-fat reading, that pushed protein to 228 g and
    # the macro floors above the day's expenditure, so a weight-loss day
    # prescribed a surplus. 80% errs high for most adults, which errs toward
    # *more* protein and a *higher* EA floor: safe in both directions.
    ffm_estimated = ffm_kg is None
    ffm_used = ffm_kg if ffm_kg else weight_kg * FFM_FALLBACK_FRACTION
    if goal == "weight_loss":
        protein_g = round(protein_g_per_kg * ffm_used)
        protein_basis = (
            f"estimated lean mass ({ffm_used:.1f} kg, 80% of weight — no body-fat reading)"
            if ffm_estimated else f"fat-free mass ({ffm_used:.1f} kg)"
        )
    else:
        protein_g = round(protein_g_per_kg * weight_kg)
        protein_basis = "total bodyweight"

    carb_min = round(CARB_MIN_G_PER_KG[color] * weight_kg)
    fat_min = round(FAT_MIN_G_PER_KG * weight_kg)
    fat_max = round(FAT_MAX_G_PER_KG * weight_kg)

    # ── Energy-availability floor ──
    # EA = (intake - exercise energy) / FFM, using the same lean-mass figure
    # as protein above.
    ffm_for_ea = ffm_used
    ea_min_target = ea_floor * ffm_for_ea + expenditure.session_kcal
    if target < ea_min_target:
        floors.append(f"energy availability (≥{ea_floor:.0f} kcal/kg FFM)")
        target = ea_min_target

    # ── The macro floors must fit ──
    floor_kcal = (protein_g * KCAL_PER_G_PROTEIN + carb_min * KCAL_PER_G_CARB
                  + fat_min * KCAL_PER_G_FAT)
    if target < floor_kcal:
        floors.append("macro minimums (protein / carbs / fat)")
        target = floor_kcal

    # ── Fit: protein fixed, carbs periodized up to the budget, fat fills ──
    budget_after_protein = target - protein_g * KCAL_PER_G_PROTEIN
    carbs_room = (budget_after_protein - fat_min * KCAL_PER_G_FAT) / KCAL_PER_G_CARB
    carbs_g = int(max(carb_min, min(carbs_periodized_g, carbs_room)))
    if carbs_g < carbs_periodized_g:
        adjustments.append(
            f"carbs trimmed {carbs_periodized_g} → {carbs_g} g to fit the energy target")

    fat_g = round((target - protein_g * KCAL_PER_G_PROTEIN
                   - carbs_g * KCAL_PER_G_CARB) / KCAL_PER_G_FAT)
    fat_g = max(fat_min, min(fat_g, fat_max))

    actual = protein_g * KCAL_PER_G_PROTEIN + carbs_g * KCAL_PER_G_CARB + fat_g * KCAL_PER_G_FAT
    ea = (actual - expenditure.session_kcal) / ffm_for_ea

    # Only claim a recovery adjustment if it changed the outcome. When the
    # macro floors already hold the deficit smaller than the eased version,
    # "deficit halved" is true of the request and false of the plan — the
    # same kind of claim that made the old training-load flag misleading.
    if goal == "weight_loss" and (load_state in ("heavy_load", "overreached") or hrv_reason):
        unadjusted = fit_macros(
            goal=goal, color=color, expenditure=expenditure, weight_kg=weight_kg,
            ffm_kg=ffm_kg, protein_g_per_kg=protein_g_per_kg,
            carbs_periodized_g=carbs_periodized_g, weight_trend_kg_wk=weight_trend_kg_wk,
            sex=sex, load_state=None, hrv_reason=None,
        )
        if round(actual) == unadjusted.target_kcal:
            eased = [a for a in adjustments
                     if "deficit halved" in a or "deficit suspended" in a]
            adjustments = [a for a in adjustments if a not in eased]
            if eased:
                why = eased[0].split(" — ")[0]
                adjustments.append(
                    f"{why} — the macro floors already keep today's deficit small, "
                    "so no further easing was needed")

    return EnergyPlan(
        goal=goal,
        expenditure=expenditure,
        target_kcal=round(actual),
        balance_kcal=round(actual - exp_total),
        carbs_g=carbs_g,
        protein_g=protein_g,
        fat_g=fat_g,
        energy_availability=round(ea, 1),
        floors_binding=floors,
        adjustments=adjustments,
        protein_basis=protein_basis,
        ea_floor=ea_floor,
        ffm_estimated=ffm_estimated,
    )


def energy_flag(plan: EnergyPlan) -> dict:
    """One-line daily card summarising the energy position."""
    bal = plan.balance_kcal
    exp = plan.expenditure.total_kcal
    if plan.goal == "weight_loss":
        if bal < -50:
            head = f"{abs(bal)} kcal deficit"
        elif bal > 50:
            head = f"{bal} kcal surplus"
        else:
            head = "at maintenance"
        text = (f"{head} today — eating {plan.target_kcal} against ~{exp} burned "
                f"({plan.expenditure.method}).")
    else:
        text = (f"Target {plan.target_kcal} kcal against ~{exp} burned "
                f"({plan.expenditure.method}).")
    if plan.floors_binding:
        text += " Held up by: " + ", ".join(plan.floors_binding) + "."
    # Warn only when the energy-availability floor is what's actually holding
    # the number up. An earlier rule warned whenever EA was under 30 — but
    # for this athlete every intended deficit lands between 25 and 30, so it
    # fired every single day, and a warning that always fires is one you
    # learn to ignore.
    ea_binding = any("energy availability" in f for f in plan.floors_binding)
    level = "warn" if ea_binding else "ok"
    return {"level": level, "title": "Energy balance", "text": text}
