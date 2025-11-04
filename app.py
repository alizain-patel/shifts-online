
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
    """
    Build tz-aware IST datetimes, **preferring legacy 'datetime'** (dd/MM/yyyy HH:mm:ss IST)
    to avoid any ambiguity in ISO/sort_key fields that may be UTC.

    Precedence:
      1) legacy 'datetime'  (parse day-first, localize IST)
      2) 'datetime_iso' + timezone=='IST'  (localize IST)
      3) 'datetime_iso' (assume UTC -> convert IST)
      4) 'sort_key' (assume UTC -> convert IST)  # moved to last to be safest
      5) 'date' + 'time' (localize IST)
    """
    # Helpers
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
        # being conservative: treat sort_key as UTC then convert to IST
        return utc_to_ist(df["sort_key"])  # if it's already IST, the UTC assumption will show a +5:30 shift; prefer datetime field when available

    if {"date", "time"}.issubset(df.columns):
        base = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        return base.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")

    raise KeyError("Expected one of: datetime, datetime_iso, sort_key, or (date+time)")

# ---------------------------------------------------------------
# APP
# ---------------------------------------------------------------
st.set_page_config(page_title="Live User Status Dashboard — IST", layout="wide")

# Source path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

# Cache clear button (keep)
if st.button("Clear cache & reload"):
    st.cache_data.clear()

# Load
raw = load_json(JSON_PATH, mtime)

# Build datetime preferring legacy string
try:
    dt_series = build_datetime_prefer_legacy(raw)
except Exception as e:
    st.error(f"Failed to build datetime: {e}")
    st.stop()

df = raw.copy()
df["datetime"] = dt_series
df = df.dropna(subset=["datetime"]).copy()

# IST display fields
df = df.sort_values("datetime", ascending=False)
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

# Friday->Today (Mon: Fri->Mon) window as earlier
now_ist = pd.Timestamp.now(tz=IST_TZ)
today_ist = now_ist.floor("D")
weekday = today_ist.weekday()
days_back = (weekday - 4) % 7
last_friday = (today_ist - pd.to_timedelta(days_back, unit="D")).floor("D")
next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
window_end = next_monday if weekday == 0 else today_ist
mask = (df["datetime_ist"].dt.floor("D") >= last_friday) & (df["datetime_ist"].dt.floor("D") <= window_end)
df = df.loc[mask]

# Latest per user by default (as earlier)
df_latest = (
    df.sort_values("datetime", ascending=False)
      .drop_duplicates(subset=["user_id"], keep="first")
      .sort_values("datetime", ascending=False)
)

# Title/caption
st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption(f"Times are displayed in **IST (Asia/Kolkata)** · window: {last_friday.strftime('%d-%m-%Y')} → {window_end.strftime('%d-%m-%Y')}")

# Table
st.dataframe(
    df_latest[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Small diagnostics to confirm fields used
with st.expander("Diagnostics: fields used"):
    # Show which column was used as the source
    used = "datetime" if "datetime" in raw.columns else ("datetime_iso" if "datetime_iso" in raw.columns else ("sort_key" if "sort_key" in raw.columns else "date+time"))
    st.write({
        "source_used": used,
        "first_raw": raw.head(1).to_dict(orient="records")[0] if len(raw) else {},
        "first_display": df_latest.head(1).to_dict(orient="records")[0] if len(df_latest) else {},
    })

st.caption(f"Last event (IST): **{df['datetime_ist'].max()}** · Total rows: {len(df)}")
