"""Training load math — CTL, ATL, TSB.

Implements the Banister/Coggan exponentially-weighted training-load model:

    CTL_today = CTL_yesterday + (TSS_today - CTL_yesterday) * (1 - exp(-1/42))
    ATL_today = ATL_yesterday + (TSS_today - ATL_yesterday) * (1 - exp(-1/7))
    TSB_today = CTL_today - ATL_today

Where:
- CTL (Chronic Training Load, "Fitness")  — 42-day exponential average of TSS
- ATL (Acute Training Load, "Fatigue")    — 7-day exponential average of TSS
- TSB (Training Stress Balance, "Form")   — CTL minus ATL

References:
- Banister EW (1991). Modeling elite athletic performance.
- Coggan AR. Training and Racing with a Power Meter (3rd ed., 2019).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from fuelcast.sources.trainingpeaks import Workout

# Exponential decay constants
CTL_DAYS = 42
ATL_DAYS = 7
CTL_LAMBDA = 1 - math.exp(-1 / CTL_DAYS)
ATL_LAMBDA = 1 - math.exp(-1 / ATL_DAYS)


@dataclass
class TrainingLoad:
    """Daily training load snapshot."""

    date: date
    ctl: float        # fitness
    atl: float        # fatigue
    tsb: float        # form (ctl - atl)
    tss_today: float  # daily TSS contributing


def _daily_tss(workouts: list[Workout], target_date: date) -> float:
    """Sum TSS for all workouts on a given date.

    Strategy:
      1. Use explicit TSS from the iCal description when present (preferred).
      2. If duration is known but TSS isn't, estimate **conservatively**:
         use 15 TSS/hr — roughly Z1 walking pace. This is intentionally low
         because the iCal feed often omits TSS on short / recovery work, and
         we'd rather under-estimate fitness than inflate it.
      3. Strength sessions without TSS get 20 TSS/hr (rough Garmin convention).

    Why so conservative? In the original implementation 50 TSS/hr was used,
    which is a moderate Z2 endurance rate. That over-credited every untagged
    workout and inflated ATL dramatically. Real Z2 endurance does run ~50
    TSS/hr — but TP almost always tags those with explicit TSS, so the
    fallback is hit mostly on easy / recovery sessions where 15 is closer
    to truth.
    """
    total = 0.0
    for w in workouts:
        if w.date != target_date:
            continue
        if not w.is_completed:
            continue
        if w.tss is not None:
            total += w.tss
        elif w.duration_min > 0:
            # Conservative fallback — see docstring
            rate = 20 if w.sport == "strength" else 15
            total += rate * w.duration_hr
    return total


def compute_training_load(
    workouts: list[Workout],
    *,
    target_date: date | None = None,
    seed_days: int = 60,
    initial_ctl: float = 0.0,
    initial_atl: float = 0.0,
) -> list[TrainingLoad]:
    """Compute daily CTL/ATL/TSB for the period leading up to target_date.

    Returns a list of TrainingLoad records, one per day, oldest first.
    The last entry is target_date itself.

    For accuracy, pass `initial_ctl` and `initial_atl` from the athlete's
    actual TrainingPeaks values at the start of the seed window. Otherwise
    seeds at zero, which under-counts fitness for the first 6 weeks until
    the exponential filter converges.
    """
    target_date = target_date or date.today()
    start = target_date - timedelta(days=seed_days)

    ctl = initial_ctl
    atl = initial_atl
    history: list[TrainingLoad] = []

    current = start
    while current <= target_date:
        tss = _daily_tss(workouts, current)
        ctl = ctl + (tss - ctl) * CTL_LAMBDA
        atl = atl + (tss - atl) * ATL_LAMBDA
        history.append(
            TrainingLoad(
                date=current,
                ctl=round(ctl, 1),
                atl=round(atl, 1),
                tsb=round(ctl - atl, 1),
                tss_today=round(tss, 1),
            )
        )
        current += timedelta(days=1)

    return history


def latest_load(history: list[TrainingLoad]) -> TrainingLoad | None:
    return history[-1] if history else None


# ─── Interpretation layer ─────────────────────────────────────────

def tsb_state(tsb: float) -> str:
    """Classify TSB into a human-meaningful state."""
    if tsb > 15:
        return "fresh"
    if tsb >= 5:
        return "recovered"
    if tsb >= -10:
        return "productive"   # the "sweet spot" of training
    if tsb >= -20:
        return "heavy_load"
    return "overreached"


def carb_adjustment_pct(tsb: float) -> int:
    """How much to bump (or trim) daily carbs based on TSB state.

    Returns a percentage adjustment to apply on top of the normal prescription.
    Positive = more carbs for refueling and recovery.
    """
    state = tsb_state(tsb)
    return {
        "fresh":       0,    # already recovered, no need to over-fuel
        "recovered":   0,
        "productive":  0,
        "heavy_load": 10,    # +10% to support recovery
        "overreached": 15,   # +15% AND flag for rest
    }[state]


def ctl_to_gut_ceiling(ctl: float, current_ceiling: int = 60) -> int:
    """Use fitness level to inform a reasonable carb-tolerance ceiling.

    Higher CTL → more trained gut → can tolerate more g/hr.
    This doesn't override an explicit gut-trained value the athlete sets,
    but provides a sanity-check ceiling for in-session fueling math.

    Loose calibration:
      CTL < 30  → 60 g/hr ceiling (newer / detrained)
      CTL 30-50 → 75 g/hr
      CTL 50-80 → 90 g/hr
      CTL 80+   → 100+ g/hr (well-trained gut likely)
    """
    if ctl < 30:
        return min(60, current_ceiling)
    if ctl < 50:
        return min(75, current_ceiling) if current_ceiling > 75 else current_ceiling
    if ctl < 80:
        return current_ceiling
    return current_ceiling  # trust the explicit value at high fitness


def training_load_flag(load: TrainingLoad | None) -> dict | None:
    """Generate a daily-card flag for the current training load state."""
    if load is None:
        return None

    state = tsb_state(load.tsb)
    if state == "fresh":
        return {
            "level": "ok",
            "title": "Recovery state",
            "text": (
                f"TSB {load.tsb:+.0f}, CTL {load.ctl:.0f}. "
                "Fresh — good day for quality work or a key session."
            ),
        }
    if state == "recovered":
        return {
            "level": "ok",
            "title": "Recovery state",
            "text": (
                f"TSB {load.tsb:+.0f}, CTL {load.ctl:.0f}. "
                "Recovered, ready for normal training."
            ),
        }
    if state == "productive":
        return {
            "level": "ok",
            "title": "Recovery state",
            "text": (
                f"TSB {load.tsb:+.0f}, CTL {load.ctl:.0f}. "
                "Productive load zone — fitness is building."
            ),
        }
    if state == "heavy_load":
        return {
            "level": "warn",
            "title": "Heavy load",
            "text": (
                f"TSB {load.tsb:+.0f}, CTL {load.ctl:.0f}, ATL {load.atl:.0f}. "
                "Significant fatigue — carbs bumped 10% for recovery. "
                "Quality session OK if planned, otherwise consider easy."
            ),
        }
    # overreached
    return {
        "level": "alert",
        "title": "Overreached",
        "text": (
            f"TSB {load.tsb:+.0f}, CTL {load.ctl:.0f}, ATL {load.atl:.0f}. "
            "Fatigue significantly exceeds fitness — carbs bumped 15%. "
            "Strongly consider a rest or easy day; pushing through risks "
            "deeper hole."
        ),
    }
