
import os
import pandas as pd
import streamlit as st

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"  # IANA timezone for IST (UTC+05:30)
SHOW_WINDOW = True        # Friday -> Today (or Friday -> Monday if today is Monday)

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
    Normalize to a single timezone-aware 'datetime' column in IST, supporting:
      - 'datetime_iso' (preferred, produced by latest script in IST)
      - 'datetime' legacy string like 'dd/MM/yyyy HH:mm:ss IST'
      - 'date' + 'time' columns
    We will localize/convert everything to IST to guarantee display in IST.
    """
    if "datetime_iso" in df.columns:
        # Assume ISO strings represent local time (script writes IST). If naive, localize to IST.
        dt = pd.to_datetime(df["datetime_iso"], errors="coerce")
        if dt.dt.tz is None:
            dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        else:
            dt = dt.dt.tz_convert(IST_TZ)
        df["datetime"] = dt
    elif "datetime" in df.columns:
        # Legacy string: strip trailing " IST" and parse day-first, then localize IST
        dt_legacy = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        dt = pd.to_datetime(dt_legacy, dayfirst=True, errors="coerce")
        dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        df["datetime"] = dt
    elif {"date", "time"}.issubset(df.columns):
        # Combine and localize to IST
        dt = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        df["datetime"] = dt
    else:
        raise KeyError("Expected 'datetime_iso' or 'datetime' or both 'date' and 'time' columns")

    # Drop rows that failed to parse
    df = df.dropna(subset=["datetime"]).copy()
    return df


def apply_window(df: pd.DataFrame):
    """Filter rows to Friday->Today (or Friday->Monday if today is Monday) using IST dates."""
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4

    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")

    window_end = next_monday if weekday == 0 else today_ist

    # Compare by date component in IST
    ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    return df.loc[mask], last_friday.date(), window_end.date()


def map_status(event: str) -> str:
    if event == "Punch In":
        return "🟢 active"  # green circle
    if event == "Break Start":
        return "🟠 on break"  # orange circle
    if event == "Break End":
        return "🟢 active"
    if event in ("Punch Out", "On Leave"):
        return "🔴 on leave"  # red circle
    return "⚪ unknown"           # white circle


def latest_per_user(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# -------------------------------------------------------------------
# LOAD & PREPARE
# -------------------------------------------------------------------
st.set_page_config(page_title="User Status Dashboard", layout="wide")

try:
    file_mtime = os.path.getmtime(JSON_PATH)
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set env var `SHIFTS_JSON_PATH` or place the file next to app.py.")
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

# Sort by true datetimes (IST-aware)
df = df.sort_values("datetime", ascending=False).copy()

# Derive display fields explicitly in IST (and append " IST")
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"

# Status + display name
df["status"] = df["event"].astype(str).map(map_status)
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# Window filter (Friday->Today or Friday->Monday)
window_info = ""
if SHOW_WINDOW:
    df, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"

# -------------------------------------------------------------------
# SIDEBAR & VIEW MODE
# -------------------------------------------------------------------
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
st.caption("Shows the latest status per user — **IST (Asia/Kolkata)**." + window_info)

columns_to_show = ["Name & Status", "Date", "event", "Time"]
rename_map = {"event": "Event"}

st.dataframe(
    df_view[columns_to_show].rename(columns=rename_map),
    use_container_width=True,
    hide_index=True,
)

# Footer: last event time in IST
if "sort_key" in raw_df.columns:
    last_iso = pd.to_datetime(raw_df["sort_key"], errors="coerce").max()
    if pd.notna(last_iso) and last_iso.tzinfo is None:
        last_ist = pd.Timestamp(last_iso, tz=IST_TZ)
    else:
        last_ist = pd.Timestamp(last_iso).tz_convert(IST_TZ)
else:
    last_ist = df["datetime_ist"].max()

st.caption(f"Data last event time (IST): **{last_ist}** · Source: `{os.path.basename(JSON_PATH)}`")
