# FuelCast

> *A periodized, vegetarian-aware fueling engine for the build to Ironman 70.3 Victoria.*

A small Python tool that reads my TrainingPeaks calendar each morning,
runs the workouts through a prescription engine, and writes a single JSON
file that drives the dashboard at
[brooksgroves.com/fuelcast](https://brooksgroves.com/fuelcast).

This exists because I have everything I need to fuel a 70.3 properly —
TrainingPeaks for the plan, Garmin Connect+ for daily macros, Lose It! for
food logging, twice-yearly functional health bloodwork — except the part
that bridges them. Fuelin sells that bridge for a subscription. I'd rather
encode it.

## What it does

1. Pulls planned workouts from TrainingPeaks (iCal feed)
2. Classifies each session 🔴 Red / 🟡 Yellow / 🟢 Green using duration,
   TSS, and intensity keywords
3. Prescribes daily carbs, protein, fat, and calories using validated
   sport-nutrition science (Jeukendrup, Burke, Phillips), tuned for
   vegetarian masters athletes (~2.2 g/kg protein floor, leucine pairing,
   iron and B12 flags)
4. Generates an in-session fueling plan for any workout >60 min
   (multi-source carbs above 60 g/hr; sodium and bottle math)
5. Reads the most recent bloodwork panel and surfaces relevant flags
   on the daily card
6. Writes `data/today.json`, which the static HTML page reads and renders

## Stack

- **Python 3.12**, managed with `pixi` (consistent with my other projects)
- **`icalendar`** for TrainingPeaks parsing
- **`pyyaml`** for athlete + bloodwork config
- **GitHub Actions** for daily 5am PT cron
- Output: a single static JSON file committed back to the repo
- Frontend: plain HTML/CSS/JS (no framework), parchment aesthetic
  matching the rest of brooksgroves.com

## Running locally

```bash
pixi install
pixi run fuelcast --date today
# → writes data/today.json
```

## The science, briefly

| Macro              | Range          | Source                                    |
|--------------------|----------------|-------------------------------------------|
| Daily carbs        | 3–11 g/kg      | Jeukendrup; Burke 2011; periodized by TSS |
| Daily protein      | 2.0–2.5 g/kg   | Phillips; +0.2 for vegetarian; +0.3 build |
| Daily fat          | 0.8–1.2 g/kg   | flexible, fills calorie balance           |
| In-session carbs   | 30–90 g/hr     | duration + intensity, multi-source >60g   |

Per-meal traffic-light thresholds (RED 30g, YELLOW 50g, GREEN 100g) are
calibrated against Fuelin's published athlete program — they got that
specific framing right, and there's no point reinventing those numbers.

## Status

- [x] Repo scaffold
- [x] TrainingPeaks ICS fetcher
- [x] Prescription engine v1
- [x] JSON renderer
- [x] GitHub Action (daily 5am PT)
- [x] Static HTML dashboard
- [ ] Sweat rate calculator
- [ ] Race-week carb-load protocol
- [ ] Lose It! actual-vs-target overlay
- [ ] Race-day rehearsal tracker

## Acknowledgments

Calibrated against Asker Jeukendrup's carbohydrate periodization research,
Louise Burke's *Clinical Sports Nutrition*, Stuart Phillips on protein
for masters athletes — and against Fuelin, who packaged it well.

— Brooks Groves · Lakewood, WA
