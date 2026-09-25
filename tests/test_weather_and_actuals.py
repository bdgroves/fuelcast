"""Weather-aware fuelling, plan-vs-reality, and the local-date fix."""

from __future__ import annotations

from datetime import date, datetime

from fuelcast.prescriptions.energy import estimate_expenditure
from fuelcast.sources.garmin import parse_athlete_state
from fuelcast.sources.weather import HOT_F, parse

THU = date(2026, 9, 24)


def _feed(*days, updated="2026-09-24T21:00:00Z"):
    return {"updated": updated, "lakewood": {"label": "Lakewood, WA", "forecast": list(days)}}


# ─── weather ─────────────────────────────────────────────────────────

def test_matches_today_by_date():
    w = parse(_feed({"name": "THU", "date": "2026-09-24", "high": 84},
                    {"name": "FRI", "date": "2026-09-25", "high": 60}), today=THU)
    assert w.high_f == 84 and w.hot


def test_regression_old_period_labels_still_resolve_to_today():
    """The site labelled today from the NWS period name: 'This Afternoon'
    became 'THI' and 'Today' became 'TOD', so a weekday match failed."""
    for label in ("THI", "TOD"):
        w = parse(_feed({"name": label, "high": 64}, {"name": "FRI", "high": 62}), today=THU)
        assert w.high_f == 64, label


def test_never_uses_another_days_forecast():
    """Evening feeds start at tomorrow; that must not be taken as today."""
    w = parse(_feed({"name": "FRI", "date": "2026-09-25", "high": 90}), today=THU)
    assert w.high_f is None and not w.hot


def test_hot_threshold():
    assert parse(_feed({"name": "THU", "high": HOT_F}), today=THU).hot
    assert not parse(_feed({"name": "THU", "high": HOT_F - 1}), today=THU).hot


def test_stale_feed_is_unknown_not_trusted():
    w = parse(_feed({"name": "THU", "high": 95}, updated="2026-09-10T00:00:00Z"), today=THU)
    assert w.high_f is None and not w.hot


def test_hot_day_turns_on_extra_sodium():
    from fuelcast.prescriptions.session import sodium_mg_per_hr
    from fuelcast.sources.trainingpeaks import Workout
    w = Workout(date=THU, title="Ride", sport="bike", duration_min=120, tss=None)
    assert sodium_mg_per_hr(w, hot_day=True) > sodium_mg_per_hr(w, hot_day=False)


# ─── measured session energy ─────────────────────────────────────────

def test_measured_session_kcal_beats_the_rate_estimate():
    kw = dict(session_sport="run", session_duration_hr=1.0, measured_tdee=3231,
              measured_training_kcal=823, measured_bmr=2215, kcal_per_hour={"run": 325},
              weight_kg=94.8, height_cm=178, age=56, sex="M")
    assert estimate_expenditure(**kw).session_kcal == 325
    e = estimate_expenditure(**kw, measured_session_kcal=255)
    assert e.session_kcal == 255 and "measured by Garmin" in e.method


def test_activities_parsed_from_feed():
    st = parse_athlete_state({"activities": [
        {"local_date": "2026-09-24", "name": "Ride", "sport": "ride", "duration_min": 30,
         "kcal_net": 255, "tss": 21},
        {"local_date": "2026-09-24", "name": "junk", "duration_min": "x"},
        {"local_date": None, "duration_min": 20},
    ]})
    assert len(st.activities) == 1


# ─── plan vs reality, end to end ─────────────────────────────────────

ICS = ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\nUID:1\n"
       "DTSTART;VALUE=DATE:20260924\nDTEND;VALUE=DATE:20260925\n"
       "SUMMARY:Run: Treadmill Running\nDESCRIPTION:easy 60 min\nEND:VEVENT\nEND:VCALENDAR\n")
BASE = {"weight": {"smoothed_kg": 94.8, "body_fat_pct": 29.2, "ffm_kg": 67.1, "stale_days": 0},
        "energy": {"tdee_7d": 3231, "bmr": 2215, "training_kcal_7d": 823, "days": 14,
                   "kcal_per_hour": {"run": 325, "ride": 516}}}
RIDE = [{"local_date": "2026-09-24", "name": "Lakewood Cycling", "sport": "ride",
         "duration_min": 30.0, "kcal_net": 255, "tss": 21.0}]


def _run(monkeypatch, tmp_path, activities, high=64):
    import fuelcast.engine as eng
    from fuelcast.sources import weather as wx
    monkeypatch.setattr(eng, "fetch_daily_tss", lambda **k: {})
    monkeypatch.setattr(eng, "fetch_athlete_state",
                        lambda **k: parse_athlete_state({**BASE, "activities": activities}))
    monkeypatch.setattr(eng.weather, "fetch", lambda today=None, **k: wx.parse(
        _feed({"name": "THU", "date": "2026-09-24", "high": high}), today=today))
    return eng.run_engine(target_date=THU, athlete_path="data/athlete.yaml",
                        bloodwork_dir="data/bloodwork", output_path=tmp_path / "t.json",
                        ics_text=ICS, hrv_path=tmp_path / "none.csv")


def test_planned_session_used_before_training(monkeypatch, tmp_path):
    p = _run(monkeypatch, tmp_path, [])
    assert p.session_source == "planned"
    assert not any(f["title"] == "Session done" for f in p.flags)


def test_regression_actual_session_replaces_the_plan(monkeypatch, tmp_path):
    """A planned 60-min run became a 30-min ride; the day should be fuelled
    for the ride."""
    before = _run(monkeypatch, tmp_path, [])
    after = _run(monkeypatch, tmp_path, RIDE)
    assert after.session_source == "actual"
    assert after.workout["title"] == "Lakewood Cycling"
    assert after.energy["session_kcal"] == 255
    assert after.energy["expenditure_kcal"] < before.energy["expenditure_kcal"]
    assert after.planned_workout["title"] == "Run: Treadmill Running"
    flag = next(f for f in after.flags if f["title"] == "Session done")
    assert "you did Lakewood Cycling (30 min)" in flag["text"]


def test_yesterdays_activity_does_not_count_today(monkeypatch, tmp_path):
    old = [{**RIDE[0], "local_date": "2026-09-23"}]
    assert _run(monkeypatch, tmp_path, old).session_source == "planned"


def test_hot_day_flag_and_more_fluid(monkeypatch, tmp_path):
    mild = _run(monkeypatch, tmp_path, RIDE, high=64)
    hot = _run(monkeypatch, tmp_path, RIDE, high=88)
    assert any(f["title"] == "Hot day" for f in hot.flags)
    assert not any(f["title"] == "Hot day" for f in mild.flags)
    assert hot.hydration_l >= mild.hydration_l


# ─── local date ──────────────────────────────────────────────────────

def test_regression_local_date_not_utc(monkeypatch):
    """At 01:00 UTC on the 25th it is still the 24th in Lakewood. The UTC
    runner used to build the next day's plan every evening."""
    from zoneinfo import ZoneInfo

    import fuelcast.localtime as lt

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 25, 1, 0, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    monkeypatch.setattr(lt, "datetime", Fixed)
    assert lt.local_today() == date(2026, 9, 24)
    assert lt.local_today("UTC") == date(2026, 9, 25)
