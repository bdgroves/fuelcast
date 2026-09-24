"""Tests for the HRV4Training source and HRV as a recovery signal.

Several run against the athlete's real export (data/hrv4training.csv),
because the parsing traps were only discovered in that file.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fuelcast.prescriptions.energy import estimate_expenditure, fit_macros
from fuelcast.sources.hrv4training import (
    classify,
    load,
    parse_csv,
    recovery_level,
    summarise,
)

REAL = Path(__file__).resolve().parent.parent / "data" / "hrv4training.csv"
HDR = "date,rMSSD,HR,test_duration,HRV4T_Recovery_Points, alcohol,daily_message"


def _csv(*rows, sep="\r"):
    return sep.join([HDR, *rows]) + sep


# ─── parsing traps ───────────────────────────────────────────────────

def test_regression_bare_carriage_returns():
    """The real export uses bare \\r. A naive reader sees one giant row."""
    text = _csv("2026-09-23 00:01:00 +0000,41.5,55,1,7.6,nothing,x: go",
                "2026-09-24 00:01:00 +0000,34.2,60,1,7.4,nothing,x: go")
    assert "\n" not in text
    assert len(parse_csv(text)) == 2


def test_headers_with_leading_spaces_are_matched():
    """' alcohol' in the header must still map to 'alcohol'."""
    r = parse_csv(_csv("2026-09-24 00:01:00 +0000,34.2,60,1,7.4,a little,x: ok"))[0]
    assert r.alcohol == "a little"


def test_days_without_a_measurement_are_dropped():
    """Every calendar day has a row; missed mornings carry '-' and must go."""
    rs = parse_csv(_csv("2026-09-22 00:01:00 +0000,-,-,0,-,nothing,-",
                        "2026-09-23 00:01:00 +0000,41.5,55,1,7.6,nothing,x: go"))
    assert [r.day for r in rs] == [date(2026, 9, 23)]


GO, LIMIT, EASY = "Proceed as planned", "Limit intensity today", "Take it easy today"


@pytest.mark.parametrize("msg,verdict,advice", [
    (f"Your HRV is within your normal range: {GO}", "within", GO),
    (f"Your HRV is below your normal range. However: {LIMIT}", "below", LIMIT),
    (f"... but your HRV is unusually high: {EASY}", "unusually_high", EASY),
    (f"Your HRV is slightly above your normal range: {GO}", "slightly_above", GO),
    (f"Your HRV is above your normal range: {GO}", "above", GO),
    ("-", None, None),
])
def test_classifies_the_apps_verdict(msg, verdict, advice):
    assert classify(msg) == (verdict, advice)


# ─── the real export ─────────────────────────────────────────────────

@pytest.mark.skipif(not REAL.exists(), reason="no real export present")
def test_real_export_parses_fully():
    rs = parse_csv(REAL.read_text())
    assert len(rs) == 41                     # 100 calendar rows, 41 real readings
    assert rs[-1].day == date(2026, 9, 24)
    assert rs[-1].rmssd == pytest.approx(34.22)
    assert rs[-1].verdict == "within"


@pytest.mark.skipif(not REAL.exists(), reason="no real export present")
def test_real_export_summary():
    st = load(REAL, today=date(2026, 9, 24))
    assert st.usable_today and st.age_days == 0
    assert st.readings_30d == 12
    assert st.baseline_ln is not None


# ─── freshness ───────────────────────────────────────────────────────

def test_old_reading_is_shown_but_not_used():
    row = "2026-09-20 00:01:00 +0000,20.0,58,1,6.5,nothing,x below y: Limit intensity today"
    rs = parse_csv(_csv(row))
    st = summarise(rs, today=date(2026, 9, 24))
    assert st.latest is not None and not st.usable_today
    assert recovery_level(st) == (0, None)


def test_missing_file_is_an_empty_state(tmp_path):
    st = load(tmp_path / "nope.csv", today=date(2026, 9, 24))
    assert st.latest is None and st.notes


# ─── HRV as a recovery signal ────────────────────────────────────────

def _state(verdict_msg):
    row = f"2026-09-24 00:01:00 +0000,20,58,1,6.5,nothing,{verdict_msg}"
    return summarise(parse_csv(_csv(row)), today=date(2026, 9, 24))


def test_low_hrv_eases():
    assert recovery_level(_state("x below y: Limit intensity today"))[0] == 1


def test_unusually_high_hrv_also_eases():
    """HRV4Training itself says take it easy for these."""
    assert recovery_level(_state("x unusually high: Take it easy today"))[0] == 1


def test_normal_hrv_does_nothing():
    assert recovery_level(_state("x within y: Proceed as planned"))[0] == 0


LIVE = dict(measured_tdee=3231, measured_training_kcal=823, measured_bmr=2215,
            kcal_per_hour={"ride": 516}, weight_kg=94.8, height_cm=178, age=56, sex="M")


def _long_day(**kw):
    e = estimate_expenditure(session_sport="bike", session_duration_hr=3.0, **LIVE)
    return fit_macros(goal="weight_loss", color="GREEN", expenditure=e, weight_kg=94.8,
                      ffm_kg=67.1, protein_g_per_kg=2.4, carbs_periodized_g=900, sex="M", **kw)


def test_low_hrv_alone_halves_the_deficit():
    base, low = _long_day(), _long_day(hrv_reason="HRV below your normal range")
    assert low.balance_kcal > base.balance_kcal
    assert any("halved" in a for a in low.adjustments)


def test_heavy_load_plus_low_hrv_compound_to_suspension():
    """Two independent fatigue signals agreeing count double."""
    both = _long_day(load_state="heavy_load", hrv_reason="HRV below your normal range")
    assert abs(both.balance_kcal) < 60
    assert any("suspended" in a and "heavy training load" in a and "HRV" in a
               for a in both.adjustments)


# ─── privacy trim and Dropbox fetch ──────────────────────────────────

RAW = ("date,rMSSD,HR,test_duration,HRV4T_Recovery_Points, alcohol, sickness,"
       " latitude,daily_message\r"
       "2026-09-23 00:01:00 +0000,-,-,0,-,nothing,not sick,-,-\r"
       "2026-09-24 00:01:00 +0000,34.2,60,1,7.4,a little,not sick,47.1,x within y: go\r")


def test_trim_drops_private_columns():
    """The repo is public; only the six needed columns may be committed."""
    from fuelcast.sources.hrv4training import KEEP_COLUMNS, trim
    out = trim(RAW)
    assert out.splitlines()[0] == ",".join(KEEP_COLUMNS)
    for private in ("alcohol", "sick", "latitude", "a little", "47.1"):
        assert private not in out


def test_trim_keeps_only_real_readings_and_parses_identically():
    from fuelcast.sources.hrv4training import trim
    assert len(trim(RAW).splitlines()) == 2          # header + one reading
    raw_r, trim_r = parse_csv(RAW)[0], parse_csv(trim(RAW))[0]
    assert (raw_r.day, raw_r.rmssd, raw_r.verdict) == (trim_r.day, trim_r.rmssd, trim_r.verdict)


@pytest.mark.skipif(not REAL.exists(), reason="no real export present")
def test_committed_file_is_already_trimmed():
    head = REAL.read_text().splitlines()[0]
    assert "alcohol" not in head and "sickness" not in head


def test_dropbox_unconfigured_is_a_clean_skip(monkeypatch, capsys):
    from fuelcast.sources import dropbox_hrv
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "DROPBOX_HRV_PATH"):
        monkeypatch.delenv(k, raising=False)
    assert dropbox_hrv.main() == 0
    assert "not configured" in capsys.readouterr().out


def test_dropbox_failure_never_breaks_the_job(monkeypatch, tmp_path, capsys):
    """A Dropbox outage must leave the committed file in place and exit 0."""
    from fuelcast.sources import dropbox_hrv
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("DROPBOX_HRV_PATH", "/nope.csv")
    dest = tmp_path / "hrv.csv"
    dest.write_text("original")
    monkeypatch.setattr(dropbox_hrv, "DEST", dest)

    def boom(*a, **k):
        raise RuntimeError("503")
    monkeypatch.setattr(dropbox_hrv, "access_token", boom)
    assert dropbox_hrv.main() == 0
    assert dest.read_text() == "original"
    assert "fetch failed" in capsys.readouterr().out
