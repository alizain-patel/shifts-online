
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
    # Priority: legacy 'datetime' (IST) -> 'datetime_iso' + timezone -> 'datetime_iso' as UTC -> date+time
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

# ---------------------------------------------------------------
# APP
# ---------------------------------------------------------------
st.set_page_config(page_title="Shifts — Latest Events (IST)", layout="wide")

# Show source path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    if st.button("Clear cache & reload"):
        st.cache_data.clear()
with c2:
    if st.button("Reset session state"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.experimental_rerun()

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

# Always show the newest TOP_N events regardless of day/window — this proves freshness
st.subheader(f"Newest {TOP_N} events (no filters)")
st.dataframe(
    df[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}).head(TOP_N),
    use_container_width=True, hide_index=True
)

# Show per-day counts and allow picking a day afterwards
st.subheader("Per-day counts (IST)")
per_day = df["datetime_ist"].dt.strftime("%d-%m-%Y").value_counts().sort_index()
st.dataframe(per_day.reset_index().rename(columns={"index":"Date","datetime_ist":"count"}), use_container_width=True)

# Day picker (defaults to latest day in data, not session)
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
