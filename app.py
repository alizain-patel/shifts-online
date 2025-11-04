
import os
import pandas as pd
import streamlit as st

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)


def parse_datetime_ist(df: pd.DataFrame) -> pd.DataFrame:
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

# -------------------------------------------------------------------
# APP
# -------------------------------------------------------------------
st.set_page_config(page_title="User Status Dashboard — IST", layout="wide")

# Path + cache clear
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

if st.button("Clear cache & reload"):
    st.cache_data.clear()

raw = load_json(JSON_PATH, mtime)

df = parse_datetime_ist(raw)
df = df.sort_values("datetime", ascending=False).copy()
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"

# Build status/name
status_map = {
    "Punch In": "🟢 active",
    "Break Start": "🟠 on break",
    "Break End": "🟢 active",
    "Punch Out": "🔴 on leave",
    "On Leave": "🔴 on leave",
}
df["status"] = df["event"].map(lambda e: status_map.get(e, "⚪ unknown"))
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# Diagnostics BEFORE filters
with st.expander("Diagnostics: Per-day counts (IST) BEFORE filters"):
    st.write(df["datetime_ist"].dt.strftime("%d-%m-%Y").value_counts().sort_index())

# Date filter — default to TODAY IST
today_ist = pd.Timestamp.now(tz=IST_TZ).floor("D").date()
available_dates = sorted(df["datetime_ist"].dt.date.unique().tolist())
preselect = today_ist if today_ist in available_dates else (available_dates[-1] if available_dates else None)

selected_date = st.sidebar.selectbox("Select date (IST)", options=available_dates, index=(available_dates.index(preselect) if preselect in available_dates else 0), format_func=lambda d: d.strftime("%d-%m-%Y"))
view_mode = st.sidebar.radio("Rows to show", ("All events", "Latest per user"), index=0)

# Apply date filter
df_day = df[df["datetime_ist"].dt.date == selected_date].copy()

# Latest per user if selected
if view_mode == "Latest per user":
    df_day = (
        df_day.sort_values("datetime", ascending=False)
              .drop_duplicates(subset=["user_id"], keep="first")
              .sort_values("datetime", ascending=False)
    )

st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption(f"Showing **{selected_date.strftime('%d-%m-%Y')}** in IST (Asia/Kolkata). Use the sidebar to choose another date.")

st.dataframe(
    df_day[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Footer
last_ist = df["datetime_ist"].max()
st.caption(f"Last event (IST): **{last_ist}** · Rows loaded: {len(df)}")
