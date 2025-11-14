import os
import pandas as pd
import streamlit as st

# ---------------------------------------------
# CONFIG
# ---------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"  # IANA timezone for IST (UTC+05:30)
SHOW_WINDOW = True       # Friday -> Today (or Friday -> Monday if today is Monday)

# ---------------------------------------------
# HELPERS
# ---------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, file_mtime: float) -> pd.DataFrame:
    """Load JSON with cache-busting based on file modify time."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

def parse_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize to a single timezone-aware 'datetime' column in IST.
    Prefer 'datetime_iso' (with offset or Z). Fall back to legacy only if needed.
    """
    if "datetime_iso" in df.columns:
        # Force tz-aware parsing (works for '+05:30' and 'Z'), then convert to IST.
        dt = pd.to_datetime(df["datetime_iso"], errors="coerce", utc=True)
        dt = dt.dt.tz_convert(IST_TZ)
        df["datetime"] = dt
    elif "datetime" in df.columns:
        # Legacy text like "dd/MM/yyyy HH:mm:ss IST" (avoid if ISO exists)
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

# ---------------------------------------------
# PAGE LAYOUT & CACHE CONTROL
# ---------------------------------------------
st.set_page_config(page_title="User Status Dashboard", layout="wide")
st.caption(f"Reading JSON from: `{JSON_PATH}`")

# Sidebar: manual reload to ensure no stale cache is used
if st.sidebar.button("Reload data"):
    st.cache_data.clear()
    st.experimental_rerun()

# ---------------------------------------------
# LOAD
# ---------------------------------------------
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

# ---------------------------------------------
# TRANSFORM
# ---------------------------------------------
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

# DEBUG: quickly verify parsed range is IST
st.caption(f"DEBUG · rows={len(df)} · earliest={df['datetime_ist'].min()} · latest={df['datetime_ist'].max()}")

# ---------------------------------------------
# PROBE: raw vs parsed vs displayed (find mismatches instantly)
# ---------------------------------------------
if "datetime_iso" in raw_df.columns:
    expected_from_iso = pd.to_datetime(raw_df["datetime_iso"], errors="coerce", utc=True).dt.tz_convert(IST_TZ)
    # Align order with df after parsing/sort
    tmp = df[["name","event","datetime_ist","Date","Time"]].copy()
    tmp["datetime_iso"] = raw_df.loc[tmp.index, "datetime_iso"]
    tmp["expected_from_iso_IST"] = expected_from_iso.loc[tmp.index]
    # show delta (seconds) between what we display and what parsing ISO->IST yields
    tmp["Δ_seconds"] = (tmp["datetime_ist"] - tmp["expected_from_iso_IST"]).dt.total_seconds()
    st.subheader("Probe: raw vs parsed vs displayed (first 50 rows)")
    st.dataframe(tmp.head(50), use_container_width=True)

# ---------------------------------------------
# STATUS + NAME
# ---------------------------------------------
today_ist_date = pd.Timestamp.now(tz=IST_TZ).floor("D").date()

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
        if "left for the day" in note or (dt_date is not None and dt_date == today_ist_date):
            return "🟡 left for the day"
        else:
            return "🔴 on leave"

    if evt == "On Leave":
        return "🔴 on leave"
    return "⚪ unknown"

df["status"] = df.apply(map_display_status, axis=1)
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')} {r['status']}", axis=1)

# Work mode (In Office / Work from home / Unknown)
if "is_at_approved_location" not in df.columns:
    df["is_at_approved_location"] = None

def map_work_mode(val):
    if pd.isna(val) or val is None:
        return "Unknown"
    return "In Office" if bool(val) else "Work from home"

df["Work mode"] = df["is_at_approved_location"].apply(map_work_mode)

# ---------------------------------------------
# WINDOW FILTER
# ---------------------------------------------
window_info = ""
if SHOW_WINDOW:
    df, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"
    st.caption(f"DEBUG · rows in window: {len(df)}")

# ---------------------------------------------
# SIDEBAR & VIEW MODE
# ---------------------------------------------
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

# ---------------------------------------------
# UI
# ---------------------------------------------
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

st.caption(f"Data last event time (IST): **{last_ist}** · Source: `{os.path.basename(JSON_PATH)}`")
