"""Athlete profile and configuration loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Race:
    name: str
    date: date
    priority: str
    distance: float
    notes: str = ""


@dataclass
class Athlete:
    name: str
    sex: str
    weight_kg: float
    height_cm: float
    diet: str
    phase: str
    ftp_watts: int | None
    gut_trained_to_g_hr: int
    races: list[Race] = field(default_factory=list)
    supplements: list[dict] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def next_a_race(self) -> Race | None:
        """The next A-priority race in the future."""
        today = date.today()
        upcoming = [r for r in self.races if r.priority == "A" and r.date >= today]
        return min(upcoming, key=lambda r: r.date) if upcoming else None

    @property
    def next_race(self) -> Race | None:
        """The next race of any priority."""
        today = date.today()
        upcoming = [r for r in self.races if r.date >= today]
        return min(upcoming, key=lambda r: r.date) if upcoming else None

    def days_to_next_a_race(self) -> int | None:
        race = self.next_a_race
        if race is None:
            return None
        return (race.date - date.today()).days


def load_athlete(path: Path | str = "data/athlete.yaml") -> Athlete:
    """Load athlete config from YAML."""
    with open(path) as f:
        data = yaml.safe_load(f)

    races = []
    for r in data.get("races", []):
        rd = r["date"]
        if isinstance(rd, str):
            rd = datetime.strptime(rd, "%Y-%m-%d").date()
        races.append(
            Race(
                name=r["name"],
                date=rd,
                priority=r.get("priority", "C"),
                distance=float(r.get("distance", 0)),
                notes=r.get("notes", ""),
            )
        )

    physical = data.get("physical", {})
    training = data.get("training", {})

    return Athlete(
        name=data["name"],
        sex=data["sex"],
        weight_kg=physical.get("weight_kg", 75),
        height_cm=physical.get("height_cm", 175),
        diet=data.get("diet", "omnivore"),
        phase=data.get("phase", "base"),
        ftp_watts=training.get("ftp_watts"),
        gut_trained_to_g_hr=training.get("gut_trained_to_g_hr", 60),
        races=races,
        supplements=data.get("supplements", []),
        raw=data,
    )
