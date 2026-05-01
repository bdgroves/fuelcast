"""Allow `python -m fuelcast` to run the CLI."""
from fuelcast.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
