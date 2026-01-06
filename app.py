# app.py — Live User Status Dashboard (GitHub Raw + TTL cache + Auto-refresh)
# Author: Alizain's M365 Copilot
# Date: 2026-01-06 (IST)

import os
import requests
import pandas as pd
import streamlit as st
from time import time
from datetime import datetime

# =========================================================
# CONFIG
# =========================================================
IST_TZ = "Asia/Kolkata"              # IANA timezone for IST
SHOW_WINDOW = True                   # Friday -> Today (or Friday -> Monday if today is Monday)
AUTO_REFRESH_MS = 300_000            # 5 minutes; triggers re-run
CACHE_TTL_SEC  = 600                 # 10 minutes; data cache TTL

# Prefer Streamlit Secrets for cloud; fallback to env; finally to local file.
GITHUB_RAW_URL = None
try:
    # Streamlit Community Cloud exposes secrets via st.secrets (preferred)
    if "GITHUB_RAW_URL" in st.secrets:
        GITHUB_RAW_URL = st.secrets["https://raw.githubusercontent.com/alizain-patel/shifts-online/master/user_status_dashboard.json"]
except Exception:
    pass

# Fallback: environment variable (useful for local/dev)
if not GITHUB_RAW_URL:
    GITHUB_RAW_URL = os.getenv(
        "GITHUB_RAW_URL",
        ""  # empty means we'll read from local file
    )

# Final fallback: local file (for on-prem runs)
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")

# =========================================================
# PAGE SETUP + AUTO REFRESH
# =========================================================
st.set_page_config(page_title="User Status Dashboard", layout="wide")
st.autorefresh(interval=AUTO_REFRESH_MS, key="auto_refresh")  # periodic re-run

# =========================================================
# DATA FETCHERS
# =========================================================
@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def fetch_json_from_github(url: str, bucket: int) -> pd.DataFrame:
    """
    Fetch latest JSON from GitHub Raw.
    'bucket' changes every TTL seconds to bust CDN caches and participate in Streamlit cache key.
    """
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "application/json",
        "User-Agent": "streamlit-app",
    }
    full_url = f"{url}?v={bucket}"  # cache-busting param
    r = requests.get(full_url, headers=headers, timeout=30)
    r.raise_for_status()
    # pd.read_json can read from string content via io, but simplest is to let pandas parse the text directly.
    return pd.read_json(r.text)

@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def load_local_json(path: str) -> pd.DataFrame:
    """
    Load JSON from local filesystem (on-prem/dev).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

# =========================================================
# TRANSFORMS
# =========================================================
def parse_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize to a single timezone-aware 'datetime' column in IST, supporting:
    - 'datetime_iso' (preferred, produced by your PowerShell in IST with +05:30),
    - 'datetime' legacy string like 'dd/MM/yyyy HH:mm:ss IST',
    - 'date' + 'time' columns.
    """
    if "datetime_iso" in df.columns:
        dt = pd.to_datetime(df["datetime_iso"], errors="coerce")
        # If naive, localize to IST; if tz-aware, convert to IST
        if getattr(dt.dt, "tz", None) is None:
            dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        else:
            dt = dt.dt.tz_convert(IST_TZ)
        df["datetime"] = dt
    elif "datetime" in df.columns:
        dt_legacy = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        dt = pd.to_datetime(dt_legacy, dayfirst=True, errors="coerce")
        dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        df["datetime"] = dt
    elif {"date", "time"}.issubset(df.columns):
        dt = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        df["datetime"] = dt
    else:
        raise KeyError("Expected 'datetime_iso' or 'datetime' or both 'date' and 'time' columns")

    df = df.dropna(subset=["datetime"]).copy()
    return df

def apply_window(df: pd.DataFrame):
    """
    Filter rows to Friday->Today (or Friday->Monday if today is Monday) using IST dates.
    """
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4
    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end = next_monday if weekday == 0 else today_ist

    ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    return df.loc[mask], last_friday.date(), window_end.date()

def map_status(event: str) -> str:
    if event == "Punch In":
        return "🟢 active"
    if event == "Break Start":
        return "🟠 on break"
    if event == "Break End":
        return "🟢 active"
    if event in ("Punch Out", "On Leave"):
        return "🔴 on leave"
    return "⚪ unknown"

def latest_per_user(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# =========================================================
# LOAD DATA (GitHub preferred; local fallback)
# =========================================================
try:
    if GITHUB_RAW_URL:
        # Change 'bucket' every TTL seconds to ensure fresh fetch and cache invalidation
        bucket = int(time() // CACHE_TTL_SEC)
        raw_df = fetch_json_from_github(GITHUB_RAW_URL, bucket)
        data_source_desc = f"GitHub Raw → {GITHUB_RAW_URL}"
        file_mtime_txt = "N/A (remote fetch)"
    else:
        # Local file mode (on-prem/dev runs)
        raw_df = load_local_json(JSON_PATH)
        data_source_desc = f"Local file → {os.path.abspath(JSON_PATH)}"
        try:
            mtime = os.path.getmtime(JSON_PATH)
            import pytz
            ist = pytz.timezone(IST_TZ)
            file_mtime_txt = datetime.fromtimestamp(mtime, ist).strftime("%d-%m-%Y %H:%M:%S")
        except Exception:
            file_mtime_txt = "unknown"
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# Parse datetimes
try:
    df = parse_datetime_columns(raw_df)
except Exception as e:
    st.error(f"Failed to parse date/time: {e}")
    st.stop()

# Sort and derive IST display fields
df = df.sort_values("datetime", ascending=False).copy()
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S")
df["status"] = df["event"].astype(str).map(map_status)
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')} {r['status']}", axis=1)

# Window filter (Friday->Today or Friday->Monday)
window_info = ""
if SHOW_WINDOW:
    df_view_base, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"
else:
    df_view_base = df

# =========================================================
# UI
# =========================================================
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows the latest status per user — **IST (Asia/Kolkata)**." + window_info)

st.sidebar.header("View Options")
view_mode = st.sidebar.radio(
    "Rows to show",
    options=("Latest per user", "All events"),
    index=0,
    help="Latest per user shows only the most recent event for each user.",
)

df_view = latest_per_user(df_view_base) if view_mode == "Latest per user" else df_view_base

columns_to_show = ["Name & Status", "Date", "work_mode", "event", "Time"]
rename_map = {"event": "Event", "work_mode": "Work mode"}
st.dataframe(
    df_view[columns_to_show].rename(columns=rename_map),
    use_container_width=True,
    hide_index=True,
)

# Footer: last event time (IST). Prefer raw_df's sort_key if present.
if "sort_key" in raw_df.columns:
    last_iso = pd.to_datetime(raw_df["sort_key"]).max()
    last_ist = pd.Timestamp(last_iso, tz=IST_TZ)
else:
    last_ist = df["datetime_ist"].max()

st.caption(
    f"Data last event time (IST): **{last_ist}** · Source: "
    f"`user_status_dashboard.json` · Data source: {data_source_desc} · "
    f"File last updated (IST): **{file_mtime_txt}**"
)

# Optional: debug line to confirm path/URL in prod (comment out after verifying)
# st.write("DEBUG → Using:", data_source_desc)


