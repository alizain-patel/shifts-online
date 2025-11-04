
import os
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"

# ---------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

def parse_datetime_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Build IST-aware 'datetime' with robust precedence:
    1) 'sort_key' (IST local ISO from PS compat script)
    2) legacy 'datetime' (dd/MM/yyyy HH:mm:ss IST)
    3) 'datetime_iso' + timezone=='IST' (localize IST)
    4) 'datetime_iso' (assume UTC -> convert IST)
    5) 'date' + 'time' (localize IST)
    """
    if "sort_key" in df.columns:
        dt = pd.to_datetime(df["sort_key"], errors="coerce")
        df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    elif "datetime" in df.columns:
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
        raise KeyError("Expected 'sort_key' or 'datetime' or 'datetime_iso' or both 'date' and 'time'")

    return df.dropna(subset=["datetime"]).copy()


def apply_friday_window(df: pd.DataFrame, today_only: bool) -> tuple[pd.DataFrame, str]:
    """Apply Friday->Today (or Friday->Monday if today is Monday) in IST.
       If today_only is True, restrict to today IST only.
    """
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4

    if today_only:
        ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
        mask = ist_dates == today_ist
        df2 = df.loc[mask].copy()
        info = f"window: {today_ist.strftime('%d-%m-%Y')} (today)"
        return df2, info

    # Friday anchor
    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end = next_monday if weekday == 0 else today_ist

    ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    df2 = df.loc[mask].copy()
    info = f"window: {last_friday.strftime('%d-%m-%Y')} → {window_end.strftime('%d-%m-%Y')}"
    return df2, info


def latest_per_user(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# ---------------------------------------------------------------
# APP (Final dashboard as earlier)
# ---------------------------------------------------------------
st.set_page_config(page_title="Live User Status Dashboard — IST", layout="wide")

# Show source path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

# --- Keep: Clear cache & reload button ---
if st.button("Clear cache & reload"):
    st.cache_data.clear()

# Load + parse
raw = load_json(JSON_PATH, mtime)

df = parse_datetime_ist(raw)

# Prepare IST display fields
df = df.sort_values("datetime", ascending=False).copy()
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"

status_map = {
    "Punch In": "🟢 active",
    "Break Start": "🟠 on break",
    "Break End": "🟢 active",
    "Punch Out": "🔴 on leave",
    "On Leave": "🔴 on leave",
}
df["status"] = df["event"].map(lambda e: status_map.get(e, "⚪ unknown"))
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# Sidebar controls (as earlier intent)
st.sidebar.header("View Options")
view_mode = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)
apply_window = st.sidebar.checkbox("Apply Friday → Today window (Mon: Fri → Mon)", value=True)
today_only = st.sidebar.checkbox("Today only (IST)", value=False)

# Apply window/today filter
win_info = ""
df_view = df
if apply_window or today_only:
    df_view, win_info = apply_friday_window(df_view, today_only=today_only)

# Apply view mode
if view_mode == "Latest per user":
    df_view = latest_per_user(df_view)

# Title & caption
st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption("Shows latest status per user or all events in **IST (Asia/Kolkata)**. " + win_info)

# Table
st.dataframe(
    df_view[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Footer with last event time (IST)
last_ist = df["datetime_ist"].max()
st.caption(f"Last event (IST): **{last_ist}** · Total rows loaded: {len(df)}")
