"""Fat prescriptions.

Fat is the flexible macro: once carbs and protein are set, fat fills the
remaining calorie budget. Fuelin's stated range (0.8–2.0 g/kg) covers most
training scenarios.

For an endurance athlete in build, ~1.0 g/kg is a reasonable floor (gives
adequate hormonal support and fat-soluble vitamin absorption without
displacing performance carbs).
"""

from __future__ import annotations

# Calorie-per-gram constants (Atwater factors)
KCAL_PER_G_CARB = 4
KCAL_PER_G_PROTEIN = 4
KCAL_PER_G_FAT = 9


def daily_fat_g_per_kg(*, phase: str = "base") -> float:
    """Floor for daily fat prescription, in g/kg."""
    if phase == "race_week":
        return 0.8     # carbs displace some fat in the days before
    if phase == "build":
        return 1.0
    return 1.1         # base / health / general training


def daily_calories_estimate(
    weight_kg: float,
    carbs_g: int,
    protein_g: int,
    fat_g: int,
) -> int:
    """Estimate total calories from macros."""
    return round(
        carbs_g * KCAL_PER_G_CARB
        + protein_g * KCAL_PER_G_PROTEIN
        + fat_g * KCAL_PER_G_FAT
    )


def fat_grams_to_meet_calories(
    target_calories: int | None,
    carbs_g: int,
    protein_g: int,
    *,
    weight_kg: float,
    phase: str = "base",
) -> int:
    """Compute fat grams. If a calorie target is provided, balance to it;
    otherwise use the floor."""
    floor = round(daily_fat_g_per_kg(phase=phase) * weight_kg)
    if target_calories is None:
        return floor

    remaining_kcal = target_calories - (
        carbs_g * KCAL_PER_G_CARB + protein_g * KCAL_PER_G_PROTEIN
    )
    fat_from_calories = max(0, round(remaining_kcal / KCAL_PER_G_FAT))
    return max(floor, fat_from_calories)
