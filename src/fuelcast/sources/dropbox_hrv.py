"""Fetch the HRV4Training export from Dropbox, trimmed, into data/.

Runs as a step in the daily workflow. It needs three repository secrets:

    DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN

and one repository variable naming the file, e.g.

    DROPBOX_HRV_PATH = /Apps/HRV4Training/MyMeasurements.csv

A refresh token rather than an access token, because Dropbox access tokens
expire after a few hours and a daily job would break the first night.

Any failure exits 0 with a message. The previously committed file then
stays in place, and the engine's own freshness check notices if it has gone
stale — a Dropbox hiccup must never stop the daily plan from being built.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

from fuelcast.sources.hrv4training import parse_csv, trim

TOKEN_URL = "https://api.dropbox.com/oauth2/token"
DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"
DEST = Path("data/hrv4training.csv")


def access_token(app_key: str, app_secret: str, refresh_token: str) -> str:
    r = requests.post(TOKEN_URL, timeout=20, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": app_key, "client_secret": app_secret,
    })
    r.raise_for_status()
    return r.json()["access_token"]


def download(token: str, path: str) -> str:
    r = requests.post(DOWNLOAD_URL, timeout=30, headers={
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path}),
    })
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")


def main() -> int:
    need = ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "DROPBOX_HRV_PATH")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        print(f"hrv dropbox: not configured ({', '.join(missing)} unset) — using committed file")
        return 0
    try:
        tok = access_token(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"],
                           os.environ["DROPBOX_REFRESH_TOKEN"])
        trimmed = trim(download(tok, os.environ["DROPBOX_HRV_PATH"]))
    except Exception as e:  # network, auth, wrong path — all non-fatal
        print(f"hrv dropbox: fetch failed ({type(e).__name__}: {e}) — using committed file")
        return 0
    readings = parse_csv(trimmed)
    if not readings:
        print("hrv dropbox: file had no readings — keeping committed file")
        return 0
    DEST.write_text(trimmed, encoding="utf-8")
    print(f"hrv dropbox: {len(readings)} readings, latest {readings[-1].day}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
