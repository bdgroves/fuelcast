"""Import an HRV4Training export: trim it for privacy and write it in place.

    pixi run hrv-import ~/Downloads/2026_24_9_myMeasurements.csv

Keeps only the columns FuelCast uses (see hrv4training.KEEP_COLUMNS) and
writes data/hrv4training.csv. The Dropbox automation calls the same code,
so the committed file is identical in shape either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fuelcast.sources.hrv4training import KEEP_COLUMNS, parse_csv, trim

DEST = Path("data/hrv4training.csv")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: pixi run hrv-import <path-to-HRV4Training-export.csv>")
        return 2
    src = Path(args[0]).expanduser()
    if not src.exists():
        print(f"not found: {src}")
        return 1
    trimmed = trim(src.read_text(encoding="utf-8", errors="replace"))
    readings = parse_csv(trimmed)
    if not readings:
        print("no readings found — is this an HRV4Training export?")
        return 1
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(trimmed, encoding="utf-8")
    print(f"wrote {DEST}: {len(readings)} readings, "
          f"{readings[0].day} to {readings[-1].day}, columns: {', '.join(KEEP_COLUMNS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
