
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


def build_datetime(df: pd.DataFrame, iso_policy: str) -> pd.Series:
    """
    Build a single tz-aware 'datetime' Series in IST using a selectable policy for ISO/sort_key:
      - iso_policy == 'auto':
          * if timezone=='IST' -> localize IST
          * else -> assume UTC then convert to IST
      - iso_policy == 'force_utc':
          * assume ISO/sort_key are UTC -> convert to IST
      - iso_policy == 'already_ist':
          * localize ISO/sort_key to IST (no UTC shift)
    Precedence of source columns: sort_key > datetime (legacy) > datetime_iso > date+time
    """
    # Helper lambdas
    localize_ist = lambda s: pd.to_datetime(s, errors="coerce").dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    utc_to_ist   = lambda s: pd.to_datetime(s, errors="coerce", utc=True).dt.tz_convert(IST_TZ)

    tz_series = df.get("timezone")

    if "sort_key" in df.columns:
        if iso_policy == 'force_utc':
            return utc_to_ist(df["sort_key"])  # treat as UTC then -> IST
        elif iso_policy == 'already_ist':
            return localize_ist(df["sort_key"])  # treat as local IST
        else:  # auto
            if tz_series is not None and tz_series.astype(str).str.upper().eq("IST").all():
                return localize_ist(df["sort_key"])  # local IST
            else:
                return utc_to_ist(df["sort_key"])    # assume UTC

    if "datetime" in df.columns:
        base = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        return pd.to_datetime(base, dayfirst=True, errors="coerce").dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")

    if "datetime_iso" in df.columns:
        if iso_policy == 'force_utc':
            return utc_to_ist(df["datetime_iso"])  # assume UTC
        elif iso_policy == 'already_ist':
            return localize_ist(df["datetime_iso"])  # local IST
        else:  # auto
            if tz_series is not None and tz_series.astype(str).str.upper().eq("IST").all():
                return localize_ist(df["datetime_iso"])  # local IST
            else:
                return utc_to_ist(df["datetime_iso"])    # assume UTC

    if {"date", "time"}.issubset(df.columns):
        base = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        return base.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")

    raise KeyError("Expected one of: sort_key, datetime, datetime_iso, or (date+time)")


def latest_per_user(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("datetime", ascending=False)
          .drop_duplicates(subset=["user_id"], keep="first")
          .sort_values("datetime", ascending=False)
    )

# ---------------------------------------------------------------
# APP (Final with timezone interpretation toggle)
# ---------------------------------------------------------------
st.set_page_config(page_title="Live User Status Dashboard — IST", layout="wide")

# Show source path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set SHIFTS_JSON_PATH or place the file next to app.py.")
    st.stop()

# Cache clear button
if st.button("Clear cache & reload"):
    st.cache_data.clear()

# Load
raw = load_json(JSON_PATH, mtime)

# Sidebar controls
st.sidebar.header("View Options")
iso_policy = st.sidebar.radio(
    "Interpret ISO/sort_key timestamps as…",
    options=("Auto (use timezone column)", "Force UTC → IST", "Treat as already IST"),
    index=0,
)
policy_key = {"Auto (use timezone column)": "auto", "Force UTC → IST": "force_utc", "Treat as already IST": "already_ist"}[iso_policy]

view_mode = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)
apply_friday = st.sidebar.checkbox("Apply Friday → Today window (Mon: Fri → Mon)", value=True)

# Build datetime with selected policy
try:
    dt_series = build_datetime(raw, policy_key)
except Exception as e:
    st.error(f"Failed to build datetime: {e}")
    st.stop()

# Compose working DF
df = raw.copy()
df["datetime"] = dt_series
# Drop parsing failures
df = df.dropna(subset=["datetime"]).copy()

# IST display
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

# Apply Friday window in IST if chosen
win_info = ""
if apply_friday:
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4
    days_back = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end = next_monday if weekday == 0 else today_ist
    ist_dates = df["datetime_ist"].dt.floor("D")
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    df = df.loc[mask]
    win_info = f"window: {last_friday.strftime('%d-%m-%Y')} → {window_end.strftime('%d-%m-%Y')}"

# Apply view mode
if view_mode == "Latest per user":
    df = latest_per_user(df)

# UI
st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption("Times below are rendered in **IST (Asia/Kolkata)**. " + win_info)

st.dataframe(
    df[["Name & Status", "Date", "event", "Time"]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Diagnostics (first row)
with st.expander("Diagnostics: sample conversion"):
    sample = raw.head(1).to_dict(orient="records")[0] if len(raw) else {}
    ist_min = df["datetime_ist"].min() if len(df) else None
    ist_max = df["datetime_ist"].max() if len(df) else None
    st.write({
        "iso_policy": iso_policy,
        "first_raw_row": sample,
        "first_display_row": df.head(1).to_dict(orient="records")[0] if len(df) else {},
        "ist_range": [str(ist_min), str(ist_max)]
    })

st.caption(f"Rows after filters: {len(df)}")
