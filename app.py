
import os
import pandas as pd
import streamlit as st

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"  # IANA timezone
SHOW_WINDOW = True        # Friday -> Today (or Friday -> Monday on Mondays)

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, file_mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)


def parse_datetime_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Force all datetimes to IST.
    - If legacy 'datetime' exists (dd/MM/yyyy HH:mm:ss IST), parse day-first and localize to IST.
    - Else if 'datetime_iso' exists, **assume source is UTC** and convert to IST.
      (This handles cases where ISO strings were produced in UTC without offset.)
    - Else if 'date'+'time' exist, parse and localize to IST.
    """
    if "datetime" in df.columns:
        # Legacy format: treat as IST local time
        base = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        dt = pd.to_datetime(base, dayfirst=True, errors="coerce")
        dt = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        df["datetime"] = dt
    elif "datetime_iso" in df.columns:
        # Force ISO to be interpreted as UTC, then convert to IST
        # (covers cases where ISO lacks timezone information)
        dt = pd.to_datetime(df["datetime_iso"], errors="coerce", utc=True)
        dt = dt.dt.tz_convert(IST_TZ)
        df["datetime"] = dt
    elif {"date", "time"}.issubset(df.columns):
        base = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        base = base.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        df["datetime"] = base
    else:
        raise KeyError("Expected 'datetime' or 'datetime_iso' or both 'date' and 'time' columns")

    df = df.dropna(subset=["datetime"]).copy()
    return df


def apply_window(df: pd.DataFrame):
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


def latest_per_user(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# -------------------------------------------------------------------
# LOAD & PREPARE
# -------------------------------------------------------------------
st.set_page_config(page_title="User Status Dashboard (IST)", layout="wide")

try:
    file_mtime = os.path.getmtime(JSON_PATH)
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set env var `SHIFTS_JSON_PATH` or place the file next to app.py.")
    st.stop()

raw_df = load_json(JSON_PATH, file_mtime)

df = parse_datetime_ist(raw_df)

# Sort and derive display fields
df = df.sort_values("datetime", ascending=False).copy()
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"

# Status + name
status_map = {
    "Punch In": "🟢 active",
    "Break Start": "🟠 on break",
    "Break End": "🟢 active",
    "Punch Out": "🔴 on leave",
    "On Leave": "🔴 on leave",
}
df["status"] = df["event"].map(lambda e: status_map.get(e, "⚪ unknown"))
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# Window filter
window_info = ""
if SHOW_WINDOW:
    df, start_d, end_d = apply_window(df)
    window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})"

# View mode
st.sidebar.header("View Options")
view_mode = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)

df_view = latest_per_user(df) if view_mode == "Latest per user" else df

# UI
st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption("Times are forcibly converted to **IST (Asia/Kolkata)** from source data." + window_info)

cols = ["Name & Status", "Date", "event", "Time"]
ren = {"event": "Event"}

st.dataframe(
    df_view[cols].rename(columns=ren),
    use_container_width=True,
    hide_index=True,
)

# Footer: last event time in IST (debugging)
if "sort_key" in raw_df.columns:
    # sort_key is assumed local; interpret as UTC and convert to IST for safety
    last_iso = pd.to_datetime(raw_df["sort_key"], errors="coerce", utc=True).max()
    last_ist = pd.Timestamp(last_iso).tz_convert(IST_TZ) if pd.notna(last_iso) else None
else:
    last_ist = df["datetime_ist"].max()

st.caption(f"Last event (IST): **{last_ist}** · Source: `{os.path.basename(JSON_PATH)}`")
