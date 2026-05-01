"""Prescription engine — translates training data into fueling targets."""

from fuelcast.prescriptions.carbs import (
    MEAL_CARBS_G,
    daily_carbs_g_per_kg,
    session_color,
)
from fuelcast.prescriptions.fat import daily_fat_g_per_kg
from fuelcast.prescriptions.protein import daily_protein_g_per_kg
from fuelcast.prescriptions.session import (
    in_session_carbs_g_per_hr,
    in_session_plan,
    multi_source_carb_mix,
)

__all__ = [
    "MEAL_CARBS_G",
    "daily_carbs_g_per_kg",
    "daily_fat_g_per_kg",
    "daily_protein_g_per_kg",
    "in_session_carbs_g_per_hr",
    "in_session_plan",
    "multi_source_carb_mix",
    "session_color",
]
