"""The athlete's local date.

GitHub Actions runs on UTC. FuelCast used ``date.today()``, and since the
daily workflow runs every two hours, every run from ~17:00 Pacific onward
built *tomorrow's* plan — the wrong forecast, and the wrong day to match a
completed workout against. The athlete lives in one timezone; "today" means
theirs.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Los_Angeles"


def local_today(tz_name: str | None = None) -> date:
    tz = tz_name or os.environ.get("FUELCAST_TZ") or DEFAULT_TZ
    return datetime.now(ZoneInfo(tz)).date()
