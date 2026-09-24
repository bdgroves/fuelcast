"""Gut training — progressing carb tolerance toward race demand.

The problem this solves
-----------------------
``athlete.yaml`` carries ``gut_trained_to_g_hr``, and in_session_carbs_g_per_hr
caps every prescription at it. That is correct — exceeding trained tolerance is
how athletes buy GI distress in a race. But it is also static: nothing in the
system ever raises it, and nothing tells the athlete to try.

So an athlete with a 75 g/hr gut and an A-race 251 days out will arrive at the
start line with a 75 g/hr gut, having been advised to stay there the whole time
by the tool that was supposed to prepare them.

Carb absorption is trainable. Repeated exposure upregulates intestinal
transporters — SGLT1 for glucose, and importantly GLUT5 for fructose, which is
the one that responds most to training and the one that makes >60 g/hr possible
at all. The adaptation takes weeks, needs to happen in sessions that resemble
the race, and cannot be crammed.

References
----------
- Jeukendrup A. (2017). Training the Gut for Athletes. Sports Medicine 47(S1).
- Costa RJS et al. (2017). Gut-training: the impact of two weeks repetitive
  gut-challenge during exercise on gastrointestinal status.
- Jeukendrup A. (2014). A step towards personalized sports nutrition:
  carbohydrate intake during exercise.

What this module does NOT do
----------------------------
It does not raise the athlete's tolerance by itself. ``gut_trained_to_g_hr``
stays a value the athlete sets, because only they know whether a rehearsal
actually went well. This module produces the *plan* and the prompt; confirming
the adaptation stays a human decision. Auto-ratcheting a tolerance number on a
schedule would be inventing data about someone's gut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Race-day demand by event duration. A 70.3 for an age-grouper is typically
# 4.5-6 hours, which sits squarely in the band where multi-transportable
# carbs and a trained gut make a measurable difference.
RACE_TARGET_G_HR = {
    "sprint":  30,    # ~1.0-1.5 hr — body stores cover it
    "olympic": 60,    # ~2-3 hr
    "70.3":    90,    # ~4.5-6 hr
    "140.6":   90,    # ~10-14 hr, but pacing is lower; 90 is a sane ceiling
    "marathon": 75,   # ~3-5 hr, running tolerates less than cycling
}

# Grams per hour to add per progression step, and how long the gut needs at
# each level before the next increase. Four weeks is deliberately
# conservative: two is the shortest interval with published support, and
# stacking increases faster than adaptation is how gut training becomes a
# series of ruined long rides.
STEP_G_HR = 5
WEEKS_PER_STEP = 4

# Below this duration a session cannot rehearse race fueling — there is not
# enough time under load for the gut to be challenged meaningfully.
MIN_REHEARSAL_MIN = 120


@dataclass
class GutStep:
    """One rung on the progression ladder."""

    target_g_hr: int
    start_date: date
    weeks: int
    is_current: bool = False


@dataclass
class GutPlan:
    current_g_hr: int          # what the athlete is trained to now
    race_target_g_hr: int      # what race day asks for
    gap_g_hr: int              # how far there is to go
    weeks_to_race: int
    weeks_needed: int          # at STEP_G_HR every WEEKS_PER_STEP
    feasible: bool             # is there time to close the gap
    ladder: list[GutStep] = field(default_factory=list)
    today_is_rehearsal: bool = False
    today_target_g_hr: int | None = None
    note: str = ""


def race_target(distance: str | float | None) -> int:
    """Carb target race day will demand, in g/hr."""
    if distance is None:
        return 60
    key = str(distance).strip().lower()
    if key in RACE_TARGET_G_HR:
        return RACE_TARGET_G_HR[key]
    # Numeric distances from the race block (e.g. 70.3)
    try:
        d = float(key)
    except ValueError:
        return 60
    if d >= 140:
        return RACE_TARGET_G_HR["140.6"]
    if d >= 70:
        return RACE_TARGET_G_HR["70.3"]
    if d >= 26:
        return RACE_TARGET_G_HR["marathon"]
    if d >= 24:
        return RACE_TARGET_G_HR["olympic"]
    return RACE_TARGET_G_HR["sprint"]


def build_gut_plan(
    *,
    today: date,
    days_to_race: int | None,
    current_g_hr: int,
    race_distance: str | float | None,
    session_duration_min: float = 0.0,
) -> GutPlan | None:
    """Build the progression ladder from today's tolerance to race demand.

    Returns None when there is no A-race to build toward — without a date
    there is no deadline, and a progression with no deadline is just a
    number going up.
    """
    if days_to_race is None or days_to_race < 0:
        return None

    target = race_target(race_distance)
    gap = max(0, target - current_g_hr)
    weeks_to_race = days_to_race // 7
    steps_needed = -(-gap // STEP_G_HR) if gap > 0 else 0   # ceiling division
    weeks_needed = steps_needed * WEEKS_PER_STEP

    # The last three weeks are taper and race week. Adaptation work stops;
    # by then the job is rehearsing what is already trained, not adding load.
    usable_weeks = max(0, weeks_to_race - 3)
    feasible = weeks_needed <= usable_weeks

    ladder: list[GutStep] = []
    if gap > 0:
        # Work backwards is tempting, but starting now and stepping forward
        # is what the athlete actually experiences.
        level = current_g_hr
        week_cursor = 0
        while level < target and week_cursor <= usable_weeks:
            level = min(target, level + STEP_G_HR)
            ladder.append(
                GutStep(
                    target_g_hr=level,
                    start_date=date.fromordinal(today.toordinal() + week_cursor * 7),
                    weeks=WEEKS_PER_STEP,
                    is_current=(week_cursor == 0),
                )
            )
            week_cursor += WEEKS_PER_STEP

    today_is_rehearsal = session_duration_min >= MIN_REHEARSAL_MIN
    today_target = None
    if today_is_rehearsal:
        # On a long day, practise the next rung rather than the comfortable
        # one — that is the entire mechanism.
        today_target = ladder[0].target_g_hr if ladder else target

    if gap == 0:
        note = (
            f"Gut is already trained to {current_g_hr} g/hr, at or above the "
            f"{target} g/hr a {race_distance} asks for. Keep rehearsing it on "
            "long sessions so it holds."
        )
    elif not feasible:
        note = (
            f"{gap} g/hr to close and only {usable_weeks} usable weeks before "
            f"taper — that needs {weeks_needed}. Either start now and accept "
            f"arriving short of {target}, or plan race fueling around a lower "
            "rate you can actually absorb."
        )
    else:
        note = (
            f"{gap} g/hr to close in {usable_weeks} usable weeks. "
            f"+{STEP_G_HR} g/hr every {WEEKS_PER_STEP} weeks, practised on "
            f"sessions of {MIN_REHEARSAL_MIN // 60}hr or longer. Raise "
            "gut_trained_to_g_hr in athlete.yaml once a rung sits comfortably "
            "across two or three long sessions."
        )

    return GutPlan(
        current_g_hr=current_g_hr,
        race_target_g_hr=target,
        gap_g_hr=gap,
        weeks_to_race=weeks_to_race,
        weeks_needed=weeks_needed,
        feasible=feasible,
        ladder=ladder,
        today_is_rehearsal=today_is_rehearsal,
        today_target_g_hr=today_target,
        note=note,
    )


def gut_plan_flag(plan: GutPlan | None) -> dict | None:
    """Daily-card flag for gut training, or None when there's nothing to say."""
    if plan is None:
        return None

    if plan.today_is_rehearsal and plan.today_target_g_hr:
        return {
            "level": "ok",
            "title": "Gut rehearsal",
            "text": (
                f"Long session today — practise {plan.today_target_g_hr} g/hr "
                f"(current trained tolerance {plan.current_g_hr}). Multi-source "
                "carbs, 2:1 glucose:fructose. Note how it sits."
            ),
        }
    if plan.gap_g_hr > 0 and not plan.feasible:
        return {
            "level": "warn",
            "title": "Gut training behind schedule",
            "text": plan.note,
        }
    if plan.gap_g_hr > 0:
        return {
            "level": "ok",
            "title": "Gut training",
            "text": (
                f"{plan.current_g_hr} → {plan.race_target_g_hr} g/hr, "
                f"{plan.weeks_to_race} weeks to race. Next rung "
                f"{plan.ladder[0].target_g_hr} g/hr on long sessions."
            ),
        }
    return None
