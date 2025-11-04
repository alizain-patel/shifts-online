
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

def build_datetime_prefer_legacy(df: pd.DataFrame) -> pd.Series:
    """Build tz-aware IST datetimes preferring legacy 'datetime' (dd/MM/yyyy HH:mm:ss IST).
    Fallbacks: datetime_iso (+timezone), datetime_iso (assume UTC), sort_key (assume UTC), date+time.
    """
    localize_ist = lambda s: pd.to_datetime(s, errors="coerce").dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    utc_to_ist   = lambda s: pd.to_datetime(s, errors="coerce", utc=True).dt.tz_convert(IST_TZ)

    tz_series = df.get("timezone")

    if "datetime" in df.columns:
        base = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        return pd.to_datetime(base, dayfirst=True, errors="coerce").dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")

    if "datetime_iso" in df.columns:
        if tz_series is not None and tz_series.astype(str).str.upper().eq("IST").all():
            return localize_ist(df["datetime_iso"])  # treat as local IST
        else:
            return utc_to_ist(df["datetime_iso"])    # treat as UTC

    if "sort_key" in df.columns:
        return utc_to_ist(df["sort_key"])  # conservative fallback

    if {"date", "time"}.issubset(df.columns):
        base = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        return base.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")

    raise KeyError("Expected one of: datetime, datetime_iso, sort_key, or (date+time)")

# ---------------------------------------------------------------
# APP — Today-first dashboard (avoids Friday skew), keeps cache button
# ---------------------------------------------------------------
st.set_page_config(page_title="Live User Status Dashboard — IST", layout="wide")

# Source path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

# Keep: Clear cache & reload
if st.button("Clear cache & reload"):
    st.cache_data.clear()

# Load & parse
raw = load_json(JSON_PATH, mtime)

dt_series = build_datetime_prefer_legacy(raw)

df = raw.copy()
df["datetime"] = dt_series
df = df.dropna(subset=["datetime"]).copy()

# IST display
df = df.sort_values("datetime", ascending=False)
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"] = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"] = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"

# Status + display
status_map = {
    "Punch In": "🟢 active",
    "Break Start": "🟠 on break",
    "Break End": "🟢 active",
    "Punch Out": "🔴 on leave",
    "On Leave": "🔴 on leave",
}
df["status"] = df["event"].map(lambda e: status_map.get(e, "⚪ unknown"))
df["Name & Status"] = df.apply(lambda r: f"{r.get('name','')}  {r['status']}", axis=1)

# --- Sidebar controls ---
st.sidebar.header("View Options")
# Default to TODAY ONLY (this avoids the Friday skew)
today_only = st.sidebar.checkbox("Today only (IST)", value=True)
# Let user optionally flip to the Friday window
apply_friday = st.sidebar.checkbox("Apply Friday → Today window (Mon: Fri → Mon)", value=False)
view_mode = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)

# --- Date logic ---
now_ist = pd.Timestamp.now(tz=IST_TZ)
today_ist = now_ist.floor("D")
weekday = today_ist.weekday()  # Mon=0, Fri=4

if today_only:
    mask = df["datetime_ist"].dt.floor("D") == today_ist
    df = df.loc[mask]
elif apply_friday:
    days_back = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end = next_monday if weekday == 0 else today_ist
    mask = (df["datetime_ist"].dt.floor("D") >= last_friday) & (df["datetime_ist"].dt.floor("D") <= window_end)
    df = df.loc[mask]
else:
    # No extra window, show everything (but sorted newest first)
    pass

# Apply view mode
if view_mode == "Latest per user":
    df = (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# Title & caption
st.title("🟢🔴 Live User Status Dashboard — IST")
cap = "Times are displayed in **IST (Asia/Kolkata)**. "
if today_only:
    cap += f"Showing **today** ({today_ist.strftime('%d-%m-%Y')})."
elif apply_friday:
    cap += f"Friday window applied."
st.caption(cap)

# Table
st.dataframe(
    df[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Footer
st.caption(f"Last event (IST): **{df['datetime_ist'].max() if len(df) else 'N/A'}** · Rows shown: {len(df)}")
