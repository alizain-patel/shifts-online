import os
import pandas as pd
import streamlit as st

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"   # IANA tz for IST

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

def parse_datetime_ist(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize to one IST-aware 'datetime' column.
    Precedence:
      1) legacy 'datetime' (dd/MM/yyyy HH:mm:ss IST) -> localize IST
      2) 'datetime_iso' + timezone == 'IST' -> localize IST
      3) 'datetime_iso' (no tz or unknown) -> assume UTC then convert to IST
      4) 'date' + 'time' -> localize IST
    """
    if "datetime" in df.columns:
        base = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        dt = pd.to_datetime(base, dayfirst=True, errors="coerce")
        df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    elif "datetime_iso" in df.columns:
        tz_series = df.get("timezone")
        if tz_series is not None and tz_series.astype(str).str.upper().eq("IST").all():
            dt = pd.to_datetime(df["datetime_iso"], errors="coerce")
            df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        else:
            dt = pd.to_datetime(df["datetime_iso"], errors="coerce", utc=True)
            df["datetime"] = dt.dt.tz_convert(IST_TZ)
    elif {"date", "time"}.issubset(df.columns):
        dt = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    else:
        raise KeyError("Expected 'datetime' or 'datetime_iso' or both 'date' and 'time'")

    return df.dropna(subset=["datetime"]).copy()

def latest_per_user(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

def friday_window(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Friday -> Today (or Friday -> Monday if today is Monday) using IST dates."""
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()   # Mon=0, Fri=4
    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end = next_monday if weekday == 0 else today_ist

    ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    df_win = df.loc[mask].copy()
    info = f"window: {last_friday.strftime('%d-%m-%Y')} → {window_end.strftime('%d-%m-%Y')}"
    return df_win, info

# -------------------------------------------------------------------
# UI HEADER + CACHE CLEAR
# -------------------------------------------------------------------
st.set_page_config(page_title="Live User Status Dashboard — IST", layout="wide")
st.title("🟢🔴 Live User Status Dashboard — IST")

# Show path and modified time
try:
    file_mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(file_mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set env var SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

if st.button("Clear cache & reload"):
    st.cache_data.clear()

# -------------------------------------------------------------------
# LOAD + PARSE
# -------------------------------------------------------------------
try:
    raw_df = load_json(JSON_PATH, file_mtime)
except Exception as e:
    st.error(f"Failed to read JSON: {e}")
    st.stop()

try:
    df = parse_datetime_ist(raw_df)
except Exception as e:
    st.error(f"Failed to parse datetime: {e}")
    st.stop()

# Sort and build display in IST
df = df.sort_values("datetime", ascending=False).copy()
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"
df["status"] = df["event"].map({
    "Punch In": "🟢 active",
    "Break Start": "🟠 on break",
    "Break End": "🟢 active",
    "Punch Out": "🔴 on leave",
    "On Leave": "🔴 on leave",
}).fillna("⚪ unknown")
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# -------------------------------------------------------------------
# SIDEBAR CONTROLS
# -------------------------------------------------------------------
st.sidebar.header("View Options")
apply_win = st.sidebar.checkbox("Apply Friday → Today (or Friday → Monday)", value=True)
view_mode = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)
latest_day_only = st.sidebar.checkbox("Show latest day only", value=False)

# Diagnostics: show per-day counts BEFORE filters
with st.expander("Diagnostics: Per-day counts (IST) BEFORE filters"):
    st.write(df["datetime_ist"].dt.strftime("%d-%m-%Y").value_counts().sort_index())

# Apply window if selected
win_info = ""
df_view = df
if apply_win:
    df_view, win_info = friday_window(df_view)

# Latest day only if selected
if latest_day_only and not df_view.empty:
    latest_day = df_view["datetime_ist"].dt.floor("D").max()
    df_view = df_view[df_view["datetime_ist"].dt.floor("D") == latest_day]

# Latest per user vs all events
df_view = latest_per_user(df_view) if view_mode == "Latest per user" else df_view

# -------------------------------------------------------------------
# TABLE
# -------------------------------------------------------------------
st.caption(f"Times are displayed in **IST (Asia/Kolkata)**. {win_info}")
st.dataframe(
    df_view[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Footer: Last event in IST
last_ist = df["datetime_ist"].max()
st.caption(f"Last event (IST): **{last_ist}** · Rows loaded: {len(df)}")
