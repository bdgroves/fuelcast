"""TrainingPeaks iCal feed fetcher.

The TrainingPeaks ICS feed is the lowest-friction way to get planned + completed
workouts: no OAuth, no API quotas, just a single URL TP generates in your
account settings. The trade-off is that we get less structure than the REST API
(no per-interval data, sometimes no TSS/IF). For FuelCast v1 that's fine —
duration plus session-type keywords carry most of the prescription weight.

Set TP_ICS_URL as an environment variable. In production (GitHub Actions),
store it as a repo secret. Locally, use a .env file or `export TP_ICS_URL=...`.

Treat the URL like a password — anyone with it can read your full calendar.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from icalendar import Calendar


@dataclass
class Workout:
    """A single planned or completed training session."""

    date: date
    title: str
    sport: str          # bike | run | swim | strength | cross | other
    duration_min: float
    description: str = ""
    tss: float | None = None
    intensity_factor: float | None = None
    is_completed: bool = False

    @property
    def duration_hr(self) -> float:
        return self.duration_min / 60.0


# Sport keyword classifier — TrainingPeaks SUMMARY usually leads with the sport
SPORT_KEYWORDS = {
    "bike":     ("bike", "ride", "cycling", "trainer", "rouvy", "zwift", "sufferfest"),
    "run":      ("run", "running", "jog"),
    "swim":     ("swim", "pool", "ows"),
    "strength": ("strength", "lift", "gym", "weights", "core", "yoga", "pilates"),
    "cross":    ("xc", "ski", "row", "elliptical", "hike"),
}


def classify_sport(title: str, description: str = "") -> str:
    """Best-effort sport classification from text."""
    text = f"{title} {description}".lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return sport
    return "other"


# Patterns to extract TSS / IF / duration from TP description text
RE_TSS = re.compile(r"\bTSS\s*[:=]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
RE_IF = re.compile(r"\bIF\s*[:=]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
RE_PLANNED_DURATION = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:hr|hour|h)\b|(\d+)\s*(?:min|minutes)\b", re.IGNORECASE
)


def parse_tss(text: str) -> float | None:
    m = RE_TSS.search(text)
    return float(m.group(1)) if m else None


def parse_if(text: str) -> float | None:
    m = RE_IF.search(text)
    if not m:
        return None
    val = float(m.group(1))
    # IF can appear as "0.85" or "85" — normalize
    return val if val < 2 else val / 100


def fetch_ics(url: str | None = None) -> str:
    """Download the raw ICS body."""
    url = url or os.environ.get("TP_ICS_URL")
    if not url:
        raise RuntimeError(
            "No TrainingPeaks ICS URL provided. "
            "Set TP_ICS_URL env var or pass url= argument."
        )

    # webcal:// is just http(s) under the hood
    url = url.replace("webcal://", "https://")

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_workouts(
    ics_text: str,
    *,
    lookback_days: int = 1,
    lookahead_days: int = 7,
    today: date | None = None,
) -> list[Workout]:
    """Parse ICS text into a list of Workout records inside the date window."""
    today = today or date.today()
    start = today - timedelta(days=lookback_days)
    end = today + timedelta(days=lookahead_days)

    cal = Calendar.from_ical(ics_text)
    workouts: list[Workout] = []

    for event in cal.walk("VEVENT"):
        dtstart = event.get("dtstart")
        dtend = event.get("dtend")
        if dtstart is None:
            continue

        ev_start = dtstart.dt
        if isinstance(ev_start, datetime):
            ev_date = ev_start.date()
        else:
            ev_date = ev_start

        if not (start <= ev_date <= end):
            continue

        # Duration: prefer DTEND; fall back to parsing description
        duration_min: float | None = None
        if dtend is not None:
            ev_end = dtend.dt
            if isinstance(ev_end, datetime) and isinstance(ev_start, datetime):
                duration_min = (ev_end - ev_start).total_seconds() / 60.0

        title = str(event.get("summary", "")).strip()
        description = str(event.get("description", "")).strip()

        if duration_min is None:
            duration_min = _parse_duration_text(description) or _parse_duration_text(title) or 0

        # TrainingPeaks marks completed workouts in different ways across exports.
        # Common signal: a [Completed] prefix in summary, or " - Completed" suffix.
        is_completed = (
            "[completed]" in title.lower()
            or "completed" in title.lower()
            or ev_date < today
        )

        workouts.append(
            Workout(
                date=ev_date,
                title=title,
                sport=classify_sport(title, description),
                duration_min=duration_min,
                description=description,
                tss=parse_tss(description) or parse_tss(title),
                intensity_factor=parse_if(description) or parse_if(title),
                is_completed=is_completed,
            )
        )

    workouts.sort(key=lambda w: w.date)
    return workouts


def _parse_duration_text(text: str) -> float | None:
    """Pull a duration in minutes out of free-text like '90 min' or '1.5 hr'."""
    if not text:
        return None
    m = RE_PLANNED_DURATION.search(text)
    if not m:
        return None
    if m.group(1):  # hours
        return float(m.group(1)) * 60
    return float(m.group(2))


def workout_for(target_date: date, workouts: list[Workout]) -> Workout | None:
    """Pick the primary workout for a given date.

    If multiple workouts on the same day, prefer the longest non-strength session.
    """
    todays = [w for w in workouts if w.date == target_date]
    if not todays:
        return None
    # Prefer longest non-strength workout; fall back to anything
    non_strength = [w for w in todays if w.sport != "strength"]
    pool = non_strength or todays
    return max(pool, key=lambda w: w.duration_min)


def all_workouts_for(target_date: date, workouts: list[Workout]) -> list[Workout]:
    """All workouts on a given date, sorted by duration descending."""
    return sorted(
        [w for w in workouts if w.date == target_date],
        key=lambda w: w.duration_min,
        reverse=True,
    )


def save_workouts_cache(workouts: list[Workout], path: Path | str) -> None:
    """Persist parsed workouts to JSON for inspection / debugging."""
    import json

    out = [
        {
            "date": w.date.isoformat(),
            "title": w.title,
            "sport": w.sport,
            "duration_min": w.duration_min,
            "tss": w.tss,
            "intensity_factor": w.intensity_factor,
            "is_completed": w.is_completed,
            "description": w.description,
        }
        for w in workouts
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(out, indent=2))
