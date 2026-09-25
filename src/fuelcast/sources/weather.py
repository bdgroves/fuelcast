"""Today's forecast high, from the weather feed the site already publishes.

in_session_plan has always had a ``hot_day`` switch (500 -> 700 mg/hr
sodium), but nothing ever turned it on. brooksgroves.com already refreshes a
daily forecast for Lakewood; this reads it.

Air temperature alone is a coarse heat signal — humidity and sun matter too
— so the threshold is set where sweat losses rise for most endurance
athletes rather than at the point of real heat stress.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

import requests

DEFAULT_URL = "https://raw.githubusercontent.com/bdgroves/bdgroves.github.io/main/weather.json"
DEFAULT_LOCATION = "lakewood"

# Forecast high at or above this counts as a hot training day.
HOT_F = 80
# A feed older than this is treated as unknown rather than trusted.
MAX_AGE_DAYS = 2

DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass
class Weather:
    location: str | None = None
    high_f: float | None = None
    precip_pct: float | None = None
    hot: bool = False
    note: str | None = None


def parse(doc: dict, *, today: date, location: str = DEFAULT_LOCATION) -> Weather:
    loc = doc.get(location) or {}
    if not loc:
        return Weather(note=f"no '{location}' in weather feed")
    updated = doc.get("updated")
    if updated:
        try:
            u = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).date()
            if (today - u).days > MAX_AGE_DAYS:
                return Weather(location=loc.get("label"), note=f"weather feed stale ({u})")
        except ValueError:
            pass
    # The forecast is keyed by weekday name; pick today's entry. Falling
    # back to the first entry would silently use the wrong day's high.
    fc = loc.get("forecast") or []
    want = DAY_NAMES[today.weekday()]
    # 1. exact date (the feed carries one since the label fix)
    day = next((f for f in fc if f.get("date") == today.isoformat()), None)
    # 2. weekday name
    if day is None:
        day = next((f for f in fc if str(f.get("name", "")).upper() == want), None)
    # 3. older feeds labelled today from the NWS period name ("Today" ->
    #    "TOD", "This Afternoon" -> "THI"). A first entry that isn't a
    #    weekday name is today.
    if day is None and fc and str(fc[0].get("name", "")).upper() not in DAY_NAMES:
        day = fc[0]
    if not day or day.get("high") is None:
        return Weather(location=loc.get("label"), note=f"no forecast for {want}")
    high = float(day["high"])
    return Weather(location=loc.get("label"), high_f=high,
                   precip_pct=day.get("precip"), hot=high >= HOT_F)


def fetch(*, today: date, url: str | None = None, timeout: int = 10) -> Weather:
    url = url or os.environ.get("FUELCAST_WEATHER_URL", DEFAULT_URL)
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return parse(r.json(), today=today)
    except Exception as e:
        return Weather(note=f"weather unavailable: {e}")
