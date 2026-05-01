"""Protein prescriptions, tuned for vegetarian masters athletes.

Baseline range from Fuelin's athlete program (2.0–3.0 g/kg) is consistent
with current sport-nutrition consensus for endurance + strength athletes
(Phillips, 2014; Jäger et al. ISSN position stand, 2017).

Adjustments:
- Vegetarian / vegan: +0.2 g/kg to compensate for lower leucine availability
  in plant proteins
- Masters athletes (≥50): floor at 2.2 g/kg to support muscle preservation
- Build / cut phases: +0.3 g/kg
"""

from __future__ import annotations


def daily_protein_g_per_kg(
    *,
    age: int,
    diet: str = "omnivore",
    phase: str = "base",
) -> float:
    """Daily protein prescription in g/kg bodyweight."""
    base = 2.0

    if age >= 50:
        base = max(base, 2.2)

    if diet in ("vegetarian", "vegan"):
        base += 0.2

    if phase in ("build", "peak"):
        base += 0.3

    if phase == "race_week":
        # Sufficient protein but emphasize carbs over protein this week
        base = min(base, 2.0)

    return round(min(base, 3.0), 2)


def daily_protein_grams(weight_kg: float, *, age: int, diet: str, phase: str) -> int:
    """Daily protein in grams for this athlete."""
    g_per_kg = daily_protein_g_per_kg(age=age, diet=diet, phase=phase)
    return round(g_per_kg * weight_kg)
