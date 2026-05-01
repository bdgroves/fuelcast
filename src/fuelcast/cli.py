"""FuelCast CLI.

Usage:
    pixi run fuelcast                         # generate today's plan
    pixi run fuelcast --date 2026-05-15       # specific date
    pixi run fuelcast --ics-file feed.ics     # from local file (testing)
    pixi run fuelcast --output data/today.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from fuelcast.engine import run_engine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="fuelcast", description=__doc__)
    p.add_argument(
        "--date",
        default="today",
        help="Date to generate the plan for (YYYY-MM-DD or 'today'). Default: today.",
    )
    p.add_argument(
        "--athlete",
        default="data/athlete.yaml",
        help="Path to athlete profile YAML. Default: data/athlete.yaml",
    )
    p.add_argument(
        "--bloodwork-dir",
        default="data/bloodwork",
        help="Directory of bloodwork YAML files. Default: data/bloodwork",
    )
    p.add_argument(
        "--output",
        default="data/today.json",
        help="Output JSON path. Default: data/today.json",
    )
    p.add_argument(
        "--ics-file",
        default=None,
        help="Read ICS from a local file instead of fetching (useful for testing).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.date == "today":
        target = date.today()
    else:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()

    ics_text = None
    if args.ics_file:
        ics_text = Path(args.ics_file).read_text()

    try:
        plan = run_engine(
            target_date=target,
            athlete_path=args.athlete,
            bloodwork_dir=args.bloodwork_dir,
            output_path=args.output,
            ics_text=ics_text,
        )
    except Exception as e:
        print(f"FuelCast failed: {e}", file=sys.stderr)
        return 1

    # Friendly summary to stdout
    print(f"FuelCast · {plan.weekday} {plan.date}")
    print(f"  Phase: {plan.phase} · Day color: {plan.day_color}")
    if plan.workout:
        print(
            f"  Session: {plan.workout['title']} ({plan.workout['duration_min']} min, "
            f"TSS={plan.workout.get('tss')})"
        )
    else:
        print("  Session: rest")
    print(
        f"  Macros: P {plan.macros['protein_g']}g · "
        f"F {plan.macros['fat_g']}g · "
        f"C {plan.macros['carbs_g']}g · "
        f"{plan.macros['calories']} kcal"
    )
    if plan.in_session:
        print(
            f"  In-session: {plan.in_session['target_carbs_g_per_hr']} g/hr × "
            f"{plan.workout['duration_min']/60:.1f} hr = "
            f"{plan.in_session['total_carbs_g']}g total"
        )
    if plan.race:
        print(f"  Next A-race: {plan.race['name']} ({plan.race['days_to_go']} days)")
    print(f"  Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
