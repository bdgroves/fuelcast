# FuelCast — Setup Walkthrough

How to take this repo from `git clone` to a working dashboard at
`brooksgroves.com/fuelcast` in about an hour.

---

## 1. Push the repo

```bash
cd ~/Documents
mv ~/Downloads/fuelcast .       # or wherever you unpacked it
cd fuelcast

git init
git add .
git commit -m "fuelcast: initial scaffold"

# Create the GitHub repo (or use the gh CLI):
gh repo create bdgroves/fuelcast --public --source=. --push
```

Make it public unless you want to pay for private Actions minutes.
There's no athlete data exposed in the code itself — the ICS URL and any
sensitive bits live in repo secrets.

## 2. Get your TrainingPeaks ICS URL

1. Log into [trainingpeaks.com](https://www.trainingpeaks.com) on the web
2. Click your name → **Settings** → look for **Account Settings** → **iCal**
   (or **Calendar Sync**, depending on TP's UI version)
3. Enable the iCal feed and copy the URL — it looks like:
   ```
   webcal://www.trainingpeaks.com/ical/{token}.ics
   ```
4. **Treat this URL like a password** — anyone with it can read your
   training calendar.

If the iCal option isn't showing up, fall back to the TrainingPeaks REST
API (slightly more setup but stable). Open an issue and I'll walk through
the OAuth flow.

## 3. Add the URL as a GitHub secret

In the repo on GitHub:

1. Settings → Secrets and variables → Actions → New repository secret
2. Name: `TP_ICS_URL`
3. Value: paste the full URL (replace `webcal://` with `https://`)
4. Save

## 4. Personalize `data/athlete.yaml`

Edit these fields:

```yaml
date_of_birth: 1969-XX-XX     # your real DOB
physical:
  weight_kg: 80               # current weight
  height_cm: 178              # actual height
training:
  ftp_watts: 245              # most recent FTP test
  swim_css_pace: "1:38/100m"
  run_threshold_pace: "7:30/mile"
  gut_trained_to_g_hr: 75     # current carb tolerance ceiling
```

Update `weight_kg` monthly. Update `gut_trained_to_g_hr` whenever you
do a fuel/hydration test (build it up gradually — start at 60, add 10
every couple of months).

## 5. Drop in your latest bloodwork

Edit `data/bloodwork/2026-04.yaml` with your actual Function Health
results. Keep filenames in `YYYY-MM.yaml` format — the engine picks the
most recent automatically.

You only need to do this twice a year. If you're missing a marker,
leave it `null` — the flag logic skips empty values.

## 6. Test locally (optional but recommended)

```bash
# Install pixi if you don't have it: https://pixi.sh
pixi install

# Run with the sample feed (no TP_ICS_URL needed)
pixi run fuelcast --ics-file tests/sample_feed.ics --date 2026-04-28

# Run with your real feed
export TP_ICS_URL="https://www.trainingpeaks.com/ical/YOUR-TOKEN.ics"
pixi run fuelcast

# Run the test suite
pixi run test
```

You should see something like:

```
FuelCast · Tuesday 2026-04-28
  Phase: base · Day color: YELLOW
  Session: Bike - Threshold intervals (105 min, TSS=95.0)
  Macros: P 192g · F 88g · C 440g · 3320 kcal
  In-session: 75 g/hr × 1.8 hr = 131g total
  Next A-race: Ironman 70.3 Victoria, BC (397 days)
  Wrote: data/today.json
```

Open the generated `data/today.json` to inspect the full structured plan.

## 7. Deploy the dashboard

The dashboard is a single static HTML file:

```
site/fuelcast.html
```

Two deployment paths:

### Option A: serve from this repo (simplest)

Enable GitHub Pages on the `fuelcast` repo:

1. Settings → Pages → Source: `main` branch / root
2. Move `site/fuelcast.html` to repo root and rename to `index.html`
3. Update the `DATA_URL` constant in the HTML to `'data/today.json'`
   (already correct)
4. Site available at `https://bdgroves.github.io/fuelcast/`

### Option B: ship the page to bdgroves.github.io (recommended)

Lives at `brooksgroves.com/fuelcast.html` next to your other projects.

1. Copy `site/fuelcast.html` into your `bdgroves.github.io` repo
2. Add a workflow on `bdgroves.github.io` that pulls `data/today.json`
   from the `fuelcast` repo daily, OR
3. Simpler: change the dashboard's `DATA_URL` to point at the raw
   GitHub URL of `data/today.json`:
   ```js
   const DATA_URL = 'https://raw.githubusercontent.com/bdgroves/fuelcast/main/data/today.json';
   ```

Option B keeps the dashboard with the rest of your site's aesthetic and
lets you link to it from the nav.

## 8. Change the passphrase

In `site/fuelcast.html`, find:

```js
const PASSPHRASE = 'lakemorraine';
```

Change it to whatever you want. SessionStorage means it persists per
browser session.

(Picked `lakemorraine` because it felt right — the phrase from your
athlete.yaml hint reads like a place that earns the climb. Change it.)

## 9. Verify the GitHub Action runs

Once everything is committed:

1. Go to the **Actions** tab on GitHub
2. Click "FuelCast Daily" → "Run workflow"
3. Watch the run. If it goes green, `data/today.json` will be committed
   automatically.

The cron is set to fire at 12:00 UTC daily (5am PT during PDT, 4am PT
during PST). Adjust in `.github/workflows/fuelcast-daily.yml` if you
want it earlier.

## 10. Daily flow (after setup)

Nothing. The Action fires every morning, generates the plan, commits it,
and your dashboard reads it on next page load.

Things you'll touch occasionally:

- **Monthly**: update `weight_kg` in `athlete.yaml`
- **After FTP test**: update `ftp_watts`
- **Twice yearly**: drop a new `bloodwork/YYYY-MM.yaml` file
- **When you switch phases**: change `phase: base` to `phase: build`
  etc. in `athlete.yaml`
- **When you do a carb tolerance test**: bump `gut_trained_to_g_hr`

Everything else is automatic.

---

## Future TODOs

Live in the README's status section. Highlights:

- **Sweat rate calculator** — interactive form on the dashboard, stores
  results to a YAML file the engine can read. Critical for race-day
  hydration math.
- **Race-week carb-load protocol** — engine enters `phase: race_week`
  automatically 7 days before the next A-race, ramps carbs accordingly.
- **Lose It! actual-vs-target overlay** — CSV export from Lose It!,
  parsed and shown alongside the prescription so you can see drift.
- **Race-day rehearsal tracker** — log each long-session fueling
  attempt, compare to target, identify gut-training gaps.

We can pick these up one at a time. None are blockers for v1.
