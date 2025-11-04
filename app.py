import os
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
# Where to load the JSON from (env var first, then local file).
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")

# Your dashboard window logic:
# - On Monday: show Friday -> Monday
# - Other days: show Friday -> Today
SHOW_WINDOW = True     # turn off to see all events
IST_TZ = "Asia/Kolkata"

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, file_mtime: float) -> pd.DataFrame:
    """Load JSON with cache-busting based on file modify time."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

def parse_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize to a single 'datetime' column, supporting:
      - 'datetime_iso' (preferred)
      - 'datetime' legacy string like 'dd/MM/yyyy HH:mm:ss IST'
      - 'date' + 'time' columns
    """
    if "datetime_iso" in df.columns:
        # ISO strings are unambiguous
        df["datetime"] = pd.to_datetime(df["datetime_iso"], errors="coerce")
    elif "datetime" in df.columns:
        # legacy string: strip trailing " IST" and parse day-first
        dt_legacy = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        df["datetime"] = pd.to_datetime(dt_legacy, dayfirst=True, errors="coerce")
    elif {"date", "time"}.issubset(df.columns):
        df["datetime"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
    else:
        raise KeyError("Expected 'datetime_iso' or 'datetime' or both 'date' and 'time' columns")

    # Drop rows that failed to parse
    df = df.dropna(subset=["datetime"]).copy()
    return df

def apply_window(df: pd.DataFrame) -> pd.DataFrame:
    """Filter rows to Friday->Today (or Friday->Monday if today is Monday)."""
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.normalize()
    weekday = today_ist.weekday()  # Mon=0, Fri=4, Sun=6

    # last Friday (<= today)
    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).normalize()

    # Monday after last Friday
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).normalize()

    # Window end
    if today_ist.weekday() == 0:  # Monday
        window_end = next_monday
    else:
        window_end = today_ist

    # Treat df["datetime"] as local naive; keep date part for comparison
    dates = df["datetime"].dt.date
    mask = (dates >= last_friday.date()) & (dates <= window_end.date())
    return df.loc[mask], last_friday.date(), window_end.date()

def map_status(event: str) -> str:
    """
    Visual status from event.
    Adjust if you want a different mapping for Break End (I set it to online).
    """
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
    """Pick the latest event per user by datetime."""
    # sort descending and keep the first per user_id
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# -------------------------------------------------------------------
# LOAD DATA
# -------------------------------------------------------------------
st.set_page_config(page_title="User Status Dashboard", layout="wide")

# Try to load file mtime for cache-busting; if not exists, show a friendly hint
try:
    file_mtime = os.path.getmtime(JSON_PATH)
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`.\n\n"
             "Set env var `SHIFTS_JSON_PATH` or place `user_status_dashboard.json` next to app.py.")
    st.stop()

try:
    raw_df = load_json(JSON_PATH, file_mtime)
except Exception as e:
    st.error(f"Failed to read JSON: {e}")
    st.stop()

try:
    df = parse_datetime_columns(raw_df)
except Exception as e:
    st.error(f"Failed to parse date/time: {e}")
    st.stop()

# Build display columns safely from real datetime
df = df.sort_values("datetime", ascending=False).copy()
df["Date"] = df["datetime"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime"].dt.strftime("%H:%M:%S")

# Status + display name
df["status"] = df["event"].astype(str).map(map_status)
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# Optional date window (Friday->Today or Friday->Monday)
window_info = ""
if SHOW_WINDOW:
    df, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"

# Sidebar controls
st.sidebar.header("View Options")
view_mode = st.sidebar.radio(
    "Rows to show", 
    options=("Latest per user", "All events"),
    index=0,
    help="Latest per user shows only the most recent event for each user."
)

if view_mode == "Latest per user":
    df_view = latest_per_user(df)
else:
    df_view = df

# -------------------------------------------------------------------
# UI
# -------------------------------------------------------------------
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows the latest status per user. Refresh manually or set auto-refresh." + window_info)

# Choose columns to present
columns_to_show = ["Name & Status", "Date", "event", "Time"]
rename_map = {"event": "Event"}

st.dataframe(
    df_view[columns_to_show].rename(columns=rename_map),
    use_container_width=True,
    hide_index=True
)

# Tiny footer to help diagnose data freshness
if "sort_key" in raw_df.columns:
    last_iso = pd.to_datetime(raw_df["sort_key"]).max()
else:
    last_iso = df["datetime"].max()
st.caption(f"Data last event time (IST): **{last_iso}** · Source: `{os.path.basename(JSON_PATH)}`")
