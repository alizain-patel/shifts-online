
# app.py — Live User Status Dashboard (GitHub Raw + TTL cache)
# Preserves Work mode & status logic from user's working version.
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
IST_TZ = "Asia/Kolkata"                 # IANA timezone for IST (UTC+05:30)
SHOW_WINDOW = True                      # Friday -> Today (or Friday -> Monday if today is Monday)
CACHE_TTL_SEC = int(os.getenv("CACHE_TTL_SEC", "600"))  # 10 minutes

# Prefer Streamlit Secrets (Community Cloud); fallback to env; else local file
try:
    GITHUB_RAW_URL = st.secrets["GITHUB_RAW_URL"]
except Exception:
    GITHUB_RAW_URL = os.getenv("GITHUB_RAW_URL", "")  # empty means use local file

JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")

# -----------------------------
# PAGE LAYOUT & MANUAL CACHE CLEAR
# -----------------------------
st.set_page_config(page_title="User Status Dashboard", layout="wide")

# Sidebar: manual reload to ensure no stale cache is used
if st.sidebar.button("Reload data"):
    st.cache_data.clear()
    st.rerun()

# -----------------------------
# DATA LOADERS (TTL-cached)
# -----------------------------
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
    return pd.read_json(io.StringIO(r.text))

@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def load_local_json(path: str) -> pd.DataFrame:
    """Load JSON from local filesystem (on-prem/dev)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

# -----------------------------
# HELPERS
# -----------------------------
def parse_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize to a single timezone-aware 'datetime' column in IST.
    Supports: 'datetime_iso' (preferred), legacy 'datetime' string, or 'date' + 'time'.
    """
    if "datetime_iso" in df.columns:
        # Force tz-aware parsing (handles '+05:30' and 'Z'), then convert to IST.
        dt = pd.to_datetime(df["datetime_iso"], errors="coerce", utc=True)
        dt = dt.dt.tz_convert(IST_TZ)
        df["datetime"] = dt
    elif "datetime" in df.columns:
        # Legacy text like "dd/MM/yyyy HH:mm:ss IST"
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
    return df.dropna(subset=["datetime"]).copy()

# Friday anchor logic (previous Friday if today is Friday)
def apply_window(df: pd.DataFrame):
    """Filter rows to previous Friday->Today (or previous Friday->Monday if today is Monday) using IST dates."""
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4
    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).floor("D")
    if weekday == 4:  # Friday -> previous Friday
        last_friday = (today_ist - pd.to_timedelta(7, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end = next_monday if weekday == 0 else today_ist
    ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    return df.loc[mask], last_friday.date(), window_end.date()

# Display status with 'left for the day' handling
_today_ist_date = pd.Timestamp.now(tz=IST_TZ).floor("D").date()

def map_display_status(row) -> str:
    evt = str(row.get("event", ""))
    note = str(row.get("note", "")).lower()
    dt = row.get("datetime_ist")
    dt_date = dt.date() if pd.notna(dt) else None
    if evt == "Punch In":
        return "🟢 active"
    if evt == "Break Start":
        return "🟠 on break"
    if evt == "Break End":
        return "🟢 active"
    if evt == "Punch Out":
        if "left for the day" in note or (dt_date is not None and dt_date == _today_ist_date):
            return "🟡 left for the day"  # 🟡
        else:
            return "🔴 on leave"          # 🔴
    if evt == "On Leave":
        return "🔴 on leave"
    return "⚪ unknown"

# -----------------------------
# LOAD
# -----------------------------
try:
    if GITHUB_RAW_URL:
        bucket = int(time() // CACHE_TTL_SEC)
        raw_df = fetch_json_from_github(GITHUB_RAW_URL, bucket)
        data_source_desc = f"GitHub Raw → {GITHUB_RAW_URL}"
        file_mtime_txt = "N/A (remote fetch)"
    else:
        file_mtime = os.path.getmtime(JSON_PATH)
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
    st.error(f"Failed to read JSON: {e}")
    st.stop()

# -----------------------------
# TRANSFORM
# -----------------------------
try:
    df = parse_datetime_columns(raw_df)
except Exception as e:
    st.error(f"Failed to parse date/time: {e}")
    st.stop()

# Sort by true datetimes (IST-aware)
df = df.sort_values("datetime", ascending=False).copy()

# Derive display fields explicitly in IST
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S")

# Status + display name (left-for-the-day logic preserved)
df["status"] = df.apply(map_display_status, axis=1)
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')} {r['status']}", axis=1)

# Work mode (In Office / Work from home / Unknown) — preserved mapping
if "is_at_approved_location" not in df.columns:
    df["is_at_approved_location"] = None

def map_work_mode(val):
    if pd.isna(val) or val is None:
        return "Unknown"
    return "In Office" if bool(val) else "Work from home"

df["Work mode"] = df["is_at_approved_location"].apply(map_work_mode)

# -----------------------------
# WINDOW FILTER
# -----------------------------
window_info = ""
if SHOW_WINDOW:
    df, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"

# -----------------------------
# SIDEBAR & VIEW MODE
# -----------------------------
st.sidebar.header("View Options")
view_mode = st.sidebar.radio(
    "Rows to show",
    options=("Latest per user", "All events"),
    index=0,
    help="Latest per user shows only the most recent event for each user."
)
work_mode_filter = st.sidebar.multiselect(
    "Work mode filter",
    options=["In Office", "Work from home", "Unknown"],
    default=["In Office", "Work from home", "Unknown"],
)

if view_mode == "Latest per user":
    df_view = (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )
else:
    df_view = df

if work_mode_filter:
    df_view = df_view[df_view["Work mode"].isin(work_mode_filter)]

# -----------------------------
# UI
# -----------------------------
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows the latest status per user in — **IST (Asia/Kolkata)**." + window_info)

columns_to_show = ["Name & Status", "Work mode", "Date", "event", "Time"]
rename_map = {"event": "Event"}
st.dataframe(
    df_view[columns_to_show].rename(columns=rename_map),
    use_container_width=True,
    hide_index=True,
)

# Footer: last event time in IST (parse with utc=True, then convert)
if "sort_key" in raw_df.columns:
    last_series = pd.to_datetime(raw_df["sort_key"], errors="coerce", utc=True).dt.tz_convert(IST_TZ)
    last_ist = last_series.max()
else:
    last_ist = df["datetime"].dt.tz_convert(IST_TZ).max()

st.caption(
    f"Data last event time (IST): **{last_ist}** · Source: `user_status_dashboard.json` · "
    f"Data source: {data_source_desc} · File last updated (IST): **{file_mtime_txt}**"
)
