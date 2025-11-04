
import os
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"
TOP_N = 100

# ---------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

def parse_datetime_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Build IST-aware 'datetime' column with robust precedence.
    1) 'sort_key' (preferred; IST local ISO from PS script)
    2) legacy 'datetime' (dd/MM/yyyy HH:mm:ss IST)
    3) 'datetime_iso' + timezone=='IST' (localize IST)
    4) 'datetime_iso' (assume UTC -> convert IST)
    5) 'date' + 'time' (localize IST)
    """
    if "sort_key" in df.columns:
        dt = pd.to_datetime(df["sort_key"], errors="coerce")
        # sort_key is IST local without tz, so localize
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

# ---------------------------------------------------------------
# APP
# ---------------------------------------------------------------
st.set_page_config(page_title="Shifts — Latest Events (IST)", layout="wide")

# Path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

cols = st.columns(3)
with cols[0]:
    if st.button("Clear cache & reload"):
        st.cache_data.clear()
with cols[1]:
    if st.button("Reset session state"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.experimental_rerun()
with cols[2]:
    st.write(" ")

raw = load_json(JSON_PATH, mtime)

df = parse_datetime_ist(raw)

# Build IST fields
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

st.title("🟢🔴 Latest Events — IST")

# NEWEST N EVENTS (no filters)
st.subheader(f"Newest {TOP_N} events (no filters)")
st.dataframe(
    df[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}).head(TOP_N),
    use_container_width=True, hide_index=True
)

# PER-DAY COUNTS with safe naming to avoid duplicate column error
st.subheader("Per-day counts (IST)")
per_day = df["datetime_ist"].dt.strftime("%d-%m-%Y").value_counts().sort_index()
per_day.index.name = "Date"   # ensure index has a distinct name
per_day.name = "count"        # ensure series column has a distinct name
per_day_df = per_day.reset_index()
st.dataframe(per_day_df, use_container_width=True, hide_index=True)

# Day picker (defaults to latest day)
latest_day = df["datetime_ist"].dt.floor("D").max().date()
all_days = sorted(df["datetime_ist"].dt.date.unique().tolist())
sel_idx = all_days.index(latest_day) if latest_day in all_days else len(all_days)-1
picked = st.selectbox("Pick a day (IST)", options=all_days, index=sel_idx, format_func=lambda d: d.strftime("%d-%m-%Y"))

st.subheader(f"Events on {picked.strftime('%d-%m-%Y')} (IST)")
day_df = df[df["datetime_ist"].dt.date == picked]
st.dataframe(
    day_df[["Name & Status", "Date", "event", "Time"]].rename(columns={"event":"Event"}),
    use_container_width=True, hide_index=True
)

st.caption(f"Data min/max (IST): {df['datetime_ist'].min()}  →  {df['datetime_ist'].max()}  · Total rows: {len(df)}")
