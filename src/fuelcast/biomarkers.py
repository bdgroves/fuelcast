"""Biomarker ingestion and flag generation.

Reads YAML bloodwork files from data/bloodwork/, picks the most recent panel,
and generates context flags for the daily card.

Thresholds for vegetarian endurance athletes are conservative — we'd rather
flag early and let the athlete dismiss than miss something quietly drifting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml


@dataclass
class Biomarker:
    name: str
    value: float | None
    unit: str
    status: str         # "ok" | "watch" | "flag"
    note: str = ""
    trend: str = ""


@dataclass
class BloodworkPanel:
    date: date
    provider: str
    raw: dict
    markers: list[Biomarker]


# Reference ranges tuned for vegetarian endurance masters athletes.
# These are conservative thresholds for surfacing flags, not clinical
# diagnostic ranges — when in doubt, talk to the doctor.
THRESHOLDS = {
    "ferritin": {
        "watch_below": 80, "flag_below": 30,
        "name": "Ferritin", "unit": "ng/mL",
    },
    "active_b12": {
        "watch_below": 50, "flag_below": 35,
        "name": "Active B12 (holoTC)", "unit": "pmol/L",
    },
    "vitamin_d": {
        "watch_below": 40, "flag_below": 30,
        "name": "Vitamin D3", "unit": "ng/mL",
    },
    "omega_3_index": {
        "watch_below": 8.0, "flag_below": 4.0,
        "name": "Omega-3 Index", "unit": "%",
    },
    "hba1c": {
        "watch_above": 5.6, "flag_above": 6.5,
        "name": "HbA1c", "unit": "%",
    },
    "apoB": {
        "watch_above": 90, "flag_above": 130,
        "name": "ApoB", "unit": "mg/dL",
    },
    "free_t": {
        "watch_below": 8.0, "flag_below": 5.0,
        "name": "Free Testosterone", "unit": "pg/mL",
    },
    "hs_crp": {
        "watch_above": 1.5, "flag_above": 3.0,
        "name": "hs-CRP", "unit": "mg/L",
    },
}


def _classify(value: float | None, threshold: dict) -> str:
    if value is None:
        return "ok"
    if "flag_below" in threshold and value < threshold["flag_below"]:
        return "flag"
    if "watch_below" in threshold and value < threshold["watch_below"]:
        return "watch"
    if "flag_above" in threshold and value > threshold["flag_above"]:
        return "flag"
    if "watch_above" in threshold and value > threshold["watch_above"]:
        return "watch"
    return "ok"


def latest_panel(bloodwork_dir: Path | str = "data/bloodwork") -> BloodworkPanel | None:
    """Find and load the most recent panel by file date."""
    p = Path(bloodwork_dir)
    if not p.exists():
        return None

    panels = sorted(p.glob("*.yaml"))
    if not panels:
        return None

    latest = panels[-1]
    with open(latest) as f:
        raw = yaml.safe_load(f)

    panel_date = raw.get("date")
    if isinstance(panel_date, str):
        panel_date = datetime.strptime(panel_date, "%Y-%m-%d").date()

    markers = []
    m = raw.get("markers", {})

    # Pull each value we care about, classify against thresholds
    candidates = {
        "ferritin": m.get("iron", {}).get("ferritin_ng_ml"),
        "active_b12": m.get("vitamins", {}).get("active_b12_holotc_pmol_l"),
        "vitamin_d": m.get("vitamins", {}).get("vitamin_d3_ng_ml"),
        "omega_3_index": m.get("omega", {}).get("omega_3_index_pct"),
        "hba1c": m.get("metabolic", {}).get("hba1c_pct"),
        "apoB": m.get("lipids", {}).get("apoB_mg_dl"),
        "free_t": m.get("hormones", {}).get("free_testosterone_pg_ml"),
        "hs_crp": m.get("inflammation", {}).get("hs_crp_mg_l"),
    }

    for key, value in candidates.items():
        threshold = THRESHOLDS[key]
        markers.append(
            Biomarker(
                name=threshold["name"],
                value=value,
                unit=threshold["unit"],
                status=_classify(value, threshold),
            )
        )

    return BloodworkPanel(
        date=panel_date,
        provider=raw.get("provider", ""),
        raw=raw,
        markers=markers,
    )


def vegetarian_flags(
    panel: BloodworkPanel | None,
    *,
    diet: str = "vegetarian",
) -> list[dict]:
    """Generate flag entries for the daily card."""
    flags: list[dict] = []

    # Always-on vegetarian baseline flags
    if diet in ("vegetarian", "vegan"):
        flags.append({
            "level": "ok",
            "title": "B12",
            "text": "Daily methylcobalamin supplement on board.",
        })
        flags.append({
            "level": "warn",
            "title": "Iron",
            "text": (
                "Pair plant-iron meals (lentils, tofu, leafy greens) with "
                "vitamin C (citrus, peppers, strawberries) — triples non-heme iron "
                "absorption. Avoid coffee or tea within an hour of iron-rich meals."
            ),
        })
        flags.append({
            "level": "ok",
            "title": "Leucine",
            "text": (
                "Aim for ~2.5g leucine per main meal. Combine plant proteins "
                "(rice + beans, hummus + bread, soy + grain) for a complete "
                "amino acid profile."
            ),
        })

    if panel is None:
        return flags

    # Panel-driven flags
    for marker in panel.markers:
        if marker.status == "ok":
            continue
        if marker.value is None:
            continue

        if marker.name == "Ferritin":
            flags.append({
                "level": "alert" if marker.status == "flag" else "warn",
                "title": "Ferritin",
                "text": (
                    f"Ferritin at {marker.value} {marker.unit}. "
                    "Iron-focused recovery meals on long-ride days; "
                    "consider supplementing if this trend continues."
                ),
            })
        elif marker.name == "Active B12 (holoTC)":
            flags.append({
                "level": "alert" if marker.status == "flag" else "warn",
                "title": "Active B12",
                "text": (
                    f"holoTC at {marker.value} {marker.unit}. "
                    "Reassess methylcobalamin dosing."
                ),
            })
        elif marker.name == "Vitamin D3":
            flags.append({
                "level": "warn",
                "title": "Vitamin D3",
                "text": (
                    f"D3 at {marker.value} {marker.unit}. "
                    "PNW seasonal — bump supplement dose through winter."
                ),
            })
        elif marker.name == "Omega-3 Index":
            flags.append({
                "level": "warn",
                "title": "Omega-3 Index",
                "text": (
                    f"Index at {marker.value}% — below 8% target. "
                    "Algal DHA/EPA supplement (vegetarian) or daily flax + walnuts."
                ),
            })
        elif marker.name == "Free Testosterone":
            flags.append({
                "level": "warn",
                "title": "Free Testosterone",
                "text": (
                    f"Free T at {marker.value} {marker.unit}. "
                    "Watch for overreaching signs through the build."
                ),
            })

    return flags
