# app.py — Live User Status Dashboard
# Includes stale-session detection (>12h without Punch Out)
# Uses GitHub Raw JSON + TTL cache
# Date: 2026-01-06 (IST)

import os
import io
import requests
import pandas as pd
import streamlit as st
from time import time
from datetime import datetime

# -----------------------------
# CONFIG
# -----------------------------
IST_TZ = "Asia/Kolkata"
SHOW_WINDOW = True
CACHE_TTL_SEC = int(os.getenv("CACHE_TTL_SEC", "600"))
MAX_ONLINE_HOURS = 12

# GitHub Raw URL (Streamlit Cloud → Secrets)
try:
    GITHUB_RAW_URL = st.secrets["GITHUB_RAW_URL"]
except Exception:
    GITHUB_RAW_URL = ""

JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")

# -----------------------------
# PAGE SETUP
# -----------------------------
st.set_page_config(page_title="User Status Dashboard", layout="wide")

# Manual cache clear (safe fallback if auto refresh not available)
if st.sidebar.button("Reload data"):
    st.cache_data.clear()
    st.rerun()

# -----------------------------
# DATA LOADERS
# -----------------------------
@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_json_from_github(url: str, bucket: int) -> pd.DataFrame:
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "application/json",
        "User-Agent": "streamlit-app",
    }
    full_url = f"{url}?v={bucket}"
    r = requests.get(full_url, headers=headers, timeout=30)
    r.raise_for_status()
    return pd.read_json(io.StringIO(r.text))


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def load_local_json(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)


# -----------------------------
# HELPERS
# -----------------------------
def parse_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "datetime_iso" in df.columns:
        dt = pd.to_datetime(df["datetime_iso"], errors="coerce", utc=True)
        df["datetime"] = dt.dt.tz_convert(IST_TZ)
    else:
        raise KeyError("Expected datetime_iso column")
    return df.dropna(subset=["datetime"]).copy()


def apply_window(df: pd.DataFrame):
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4

    days_back_to_friday = (weekday - 4) % 7
    last_friday = today_ist - pd.to_timedelta(days_back_to_friday, unit="D")

    if weekday == 4:  # today is Friday → previous Friday
        last_friday = today_ist - pd.to_timedelta(7, unit="D")

    next_monday = last_friday + pd.to_timedelta(3, unit="D")
    window_end = next_monday if weekday == 0 else today_ist

    mask = (
        df["datetime"].dt.tz_convert(IST_TZ) >= last_friday
    ) & (
        df["datetime"].dt.tz_convert(IST_TZ) <= window_end
    )

    return df.loc[mask], last_friday.date(), window_end.date()


# -----------------------------
# LOAD DATA
# -----------------------------
try:
    if GITHUB_RAW_URL:
        bucket = int(time() // CACHE_TTL_SEC)
        raw_df = fetch_json_from_github(GITHUB_RAW_URL, bucket)
        data_source_desc = "GitHub Raw"
    else:
        raw_df = load_local_json(JSON_PATH)
        data_source_desc = "Local file"
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# -----------------------------
# TRANSFORM
# -----------------------------
df = parse_datetime_columns(raw_df)
df = df.sort_values("datetime", ascending=False).copy()

df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S")

# Last punch-out per user
last_punch_out = (
    df[df["event"] == "Punch Out"]
    .groupby("user_id")["datetime"]
    .max()
)

_now_ist = pd.Timestamp.now(tz=IST_TZ)
_today_ist_date = _now_ist.floor("D").date()

ACTIVE_EVENTS = {"Punch In", "Break Start", "Break End"}

def map_display_status(row) -> str:
    evt = str(row.get("event", ""))
    dt = row.get("datetime_ist")
    note = str(row.get("note", "")).lower()
    user_id = row.get("user_id")

    # 🔴 STALE SESSION CHECK (any active state >12h without Punch Out)
    if evt in ACTIVE_EVENTS and pd.notna(dt):
        hours_open = (_now_ist - dt).total_seconds() / 3600
        last_out = last_punch_out.get(user_id)

        if hours_open >= MAX_ONLINE_HOURS and (
            last_out is None or last_out <= dt
        ):
            return f"🔴 no punch out ({int(hours_open)}h+)"

    # NORMAL STATUS LOGIC
    if evt == "Punch In":
        return "🟢 active"

    if evt == "Break Start":
        return "🟠 on break"

    if evt == "Break End":
        return "🟢 active"

    if evt == "Punch Out":
        if "left for the day" in note or dt.date() == _today_ist_date:
            return "🟡 left for the day"
        else:
            return "🔴 on leave"

    if evt == "On Leave":
        return "🔴 on leave"

    return "⚪ unknown"


df["status"] = df.apply(map_display_status, axis=1)
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')} {r['status']}", axis=1)

# Work Mode (UNCHANGED & WORKING)
def map_work_mode(val):
    if pd.isna(val) or val is None:
        return "Unknown"
    return "In Office" if bool(val) else "Work from home"

if "is_at_approved_location" not in df.columns:
    df["is_at_approved_location"] = None

df["Work mode"] = df["is_at_approved_location"].apply(map_work_mode)

# -----------------------------
# WINDOW FILTER
# -----------------------------
window_info = ""
if SHOW_WINDOW:
    df, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"

# -----------------------------
# VIEW MODE
# -----------------------------
view_mode = st.sidebar.radio(
    "Rows to show",
    options=("Latest per user", "All events"),
    index=0,
)

if view_mode == "Latest per user":
    df_view = (
        df.sort_values("datetime", ascending=False)
        .drop_duplicates(subset=["user_id"], keep="first")
        .sort_values("datetime", ascending=False)
    )
else:
    df_view = df

# -----------------------------
# UI
# -----------------------------
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows the latest status per user — **IST (Asia/Kolkata)**." + window_info)

columns_to_show = ["Name & Status", "Work mode", "Date", "event", "Time"]
rename_map = {"event": "Event"}

st.dataframe(
    df_view[columns_to_show].rename(columns=rename_map),
    use_container_width=True,
    hide_index=True,
)

# Footer
if "sort_key" in raw_df.columns:
    last_ist = (
        pd.to_datetime(raw_df["sort_key"], errors="coerce", utc=True)
        .dt.tz_convert(IST_TZ)
        .max()
    )
else:
    last_ist = df["datetime"].dt.tz_convert(IST_TZ).max()

st.caption(
    f"Data last event time (IST): **{last_ist}** · Data source: {data_source_desc}"
)