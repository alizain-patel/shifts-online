import os
import re
import pandas as pd
import streamlit as st
from pandas.api.types import is_datetime64tz_dtype

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ    = "Asia/Kolkata"  # IST = UTC+05:30

# ---------------------------------------------------------------
# LOAD
# ---------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    """
    Read the JSON file into a pandas DataFrame.
    Cache is keyed by mtime to avoid stale reads.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

# ---------------------------------------------------------------
# DATETIME HELPERS
# ---------------------------------------------------------------
_TS_PATTERN = r"^Timestamp\('(.*?)'\)$"

def _clean_ts_string(s: pd.Series) -> pd.Series:
    """
    Clean strings like: Timestamp('YYYY-MM-DD HH:MM:SS') -> 'YYYY-MM-DD HH:MM:SS'.
    Non-matching values remain unchanged.
    """
    if s.dtype == "O":
        return s.astype(str).str.replace(_TS_PATTERN, r"\1", regex=True)
    return s

def _to_dt_localize_ist(ser: pd.Series) -> pd.Series:
    """
    Parse naive strings and localize to IST.
    If parsed values are already tz-aware, convert to IST.
    """
    ser = _clean_ts_string(ser)
    dt  = pd.to_datetime(ser, errors="coerce")
    return (
        dt.dt.tz_convert(IST_TZ)
        if getattr(dt.dt, "tz", None) is not None
        else dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    )

def _to_dt_utc_to_ist(ser: pd.Series) -> pd.Series:
    """
    Treat strings as UTC and convert to IST.
    """
    ser = _clean_ts_string(ser)
    return pd.to_datetime(ser, errors="coerce", utc=True).dt.tz_convert(IST_TZ)

def build_datetime_rowwise(df: pd.DataFrame) -> pd.Series:
    """
    Build one tz-aware IST datetime **per row** with ISO-first priority:

      1) 'sort_key' (ISO) — preferred (newer rows usually have ISO)
         - If per-row timezone == 'IST' -> localize IST (no shift)
         - Else                         -> assume UTC -> convert to IST
      2) 'datetime_iso' — same rule as sort_key
      3) legacy 'datetime' — intended IST -> localize IST (handles Timestamp('...'))
      4) 'date' + 'time' — localize IST   (handles Timestamp('...'))

    This keeps newer ISO rows (Nov) and still supports older legacy rows (Oct).
    """
    n = len(df)
    tzcol = df["timezone"].astype(str).str.upper() if "timezone" in df.columns else None

    # 1) sort_key
    dt_sort = pd.Series([pd.NaT] * n)
    if "sort_key" in df.columns:
        naive = _clean_ts_string(df["sort_key"])
        base  = pd.to_datetime(naive, errors="coerce")
        if tzcol is not None:
            ist_mask = tzcol.eq("IST") & base.notna()
            utc_mask = (~ist_mask) & base.notna()
            if ist_mask.any(): dt_sort.loc[ist_mask] = _to_dt_localize_ist(naive.loc[ist_mask])
            if utc_mask.any(): dt_sort.loc[utc_mask] = _to_dt_utc_to_ist(naive.loc[utc_mask])
        else:
            dt_sort = _to_dt_utc_to_ist(naive)

    # 2) datetime_iso
    dt_iso = pd.Series([pd.NaT] * n)
    if "datetime_iso" in df.columns:
        naive = _clean_ts_string(df["datetime_iso"])
        base  = pd.to_datetime(naive, errors="coerce")
        if tzcol is not None:
            ist_mask = tzcol.eq("IST") & base.notna()
            utc_mask = (~ist_mask) & base.notna()
            if ist_mask.any(): dt_iso.loc[ist_mask] = _to_dt_localize_ist(naive.loc[ist_mask])
            if utc_mask.any(): dt_iso.loc[utc_mask] = _to_dt_utc_to_ist(naive.loc[utc_mask])
        else:
            dt_iso = _to_dt_utc_to_ist(naive)

    # 3) legacy datetime (fallback)
    dt_legacy = pd.Series([pd.NaT] * n)
    if "datetime" in df.columns:
        dt_legacy = _to_dt_localize_ist(df["datetime"])

    # 4) date + time (fallback)
    dt_dt = pd.Series([pd.NaT] * n)
    if {"date", "time"}.issubset(df.columns):
        combo = _clean_ts_string(df["date"]).astype(str) + " " + _clean_ts_string(df["time"]).astype(str)
        dt_dt = _to_dt_localize_ist(combo)

    # Coalesce in ISO-first order, then legacy, then date+time
    merged = dt_sort.combine_first(dt_iso).combine_first(dt_legacy).combine_first(dt_dt)

    # --- HARDEN: guarantee tz-aware datetime64[ns, tz] to avoid .dt errors ---
    # If dtype not tz-aware, localize to IST as a last resort.
    if not is_datetime64tz_dtype(merged.dtype):
        merged = pd.to_datetime(merged, errors="coerce")
        if is_datetime64tz_dtype(merged.dtype):
            merged = merged.dt.tz_convert(IST_TZ)
        else:
            merged = merged.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")

    return merged

# ---------------------------------------------------------------
# APP
# ---------------------------------------------------------------
st.set_page_config(page_title="Live User Status Dashboard — IST", layout="wide")

# Path + mtime (confirm exact file)
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`")
    st.stop()

# Cache clear button
if st.button("Clear cache & reload"):
    st.cache_data.clear()

# Load raw JSON
raw = load_json(JSON_PATH, mtime)

# Build per-row IST-aware datetime
dt = build_datetime_rowwise(raw)

# Compose DF and drop unparsed rows
df = raw.copy()
df["datetime"] = dt
df = df.dropna(subset=["datetime"]).copy()

# Derive IST display fields
df = df.sort_values("datetime", ascending=False)
df["datetime_ist"] = df["datetime"].dt.tz_convert(IST_TZ)
df["Date"]         = df["datetime_ist"].dt.strftime("%d-%m-%Y")
df["Time"]         = df["datetime_ist"].dt.strftime("%H:%M:%S") + " IST"

# ---------- Per-day counts (IST) BEFORE filters ----------
with st.expander("Per-day counts (IST) BEFORE filters"):
    ser = df["datetime_ist"].dt.strftime("%d-%m-%Y").value_counts().sort_index()
    per_day_df = ser.reset_index()
    per_day_df.columns = ["Date", "Count"]  # unique names (avoid PyArrow duplicate error)
    st.dataframe(per_day_df, use_container_width=True, hide_index=True)

# ---------- Status/Display ----------
status_map = {
    "Punch In":    "🟢 active",
    "Break Start": "🟠 on break",
    "Break End":   "🟢 active",
    "Punch Out":   "🔴 on leave",
    "On Leave":    "🔴 on leave",
}
df["status"]         = df["event"].map(lambda e: status_map.get(e, "⚪ unknown")) if "event" in df.columns else ""
df["Name & Status"]  = df.apply(lambda r: f"{r.get('name','')}  {r.get('status','')}", axis=1)

# ---------- Sidebar controls ----------
st.sidebar.header("View Options")
apply_window = st.sidebar.checkbox("Apply Friday → Today window (Mon: Fri → Mon)", value=True)
view_mode    = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)
prefer_today = st.sidebar.checkbox("Prefer today if present (for Latest per user)", value=True)

# Friday → Today window (Mon: Fri → Mon)
now_ist   = pd.Timestamp.now(tz=IST_TZ)
today_ist = now_ist.floor("D")
weekday   = today_ist.weekday()  # Mon=0, Fri=4

win_info = ""
if apply_window:
    days_back   = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back, unit="D")).floor("D")
    next_monday = (last_friday + pd.to_timedelta(3, unit="D")).floor("D")
    window_end  = next_monday if weekday == 0 else today_ist
    ist_dates   = df["datetime_ist"].dt.floor("D")
    mask        = (ist_dates >= last_friday) & (ist_dates <= window_end)
    df          = df.loc[mask]
    win_info    = f"window: {last_friday.strftime('%d-%m-%Y')} → {window_end.strftime('%d-%m-%Y')}"

# Latest per user (prefer today if present)
if view_mode == "Latest per user" and "user_id" in df.columns:
    if prefer_today:
        df_today = df[df["datetime_ist"].dt.floor("D") == today_ist]
        df_other = df[df["datetime_ist"].dt.floor("D") != today_ist]
        if not df_today.empty:
            latest_today = (
                df_today.sort_values("datetime", ascending=False)
                        .drop_duplicates(subset=["user_id"], keep="first")
            )
            missing_users = set(df["user_id"]) - set(latest_today["user_id"])
            latest_other  = (
                df_other[df_other["user_id"].isin(missing_users)]
                        .sort_values("datetime", ascending=False)
                        .drop_duplicates(subset=["user_id"], keep="first")
            )
            df = pd.concat([latest_today, latest_other], ignore_index=True)
            df = df.sort_values("datetime", ascending=False)
        else:
            df = (
                df.sort_values("datetime", ascending=False)
                  .drop_duplicates(subset=["user_id"], keep="first")
                  .sort_values("datetime", ascending=False)
            )
    else:
        df = (
            df.sort_values("datetime", ascending=False)
              .drop_duplicates(subset=["user_id"], keep="first")
              .sort_values("datetime", ascending=False)
        )

# ---------- UI ----------
st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption("Shows latest status per user or all events in **IST (Asia/Kolkata)**. " + win_info)

st.dataframe(
    df[[c for c in ["Name & Status", "Date", "event", "Time"] if c in df.columns]].rename(columns={"event": "Event"}),
    use_container_width=True,
    hide_index=True,
)

# Footer & diagnostics
last_ist = df["datetime_ist"].max() if len(df) else None
st.caption(f"Last event (IST): {last_ist if last_ist is not None else 'N/A'} · Total rows loaded: {len(df)}")

with st.expander("Diagnostics: raw vs parsed (first 2 rows)"):
    st.json({
        "raw_sample":     raw.head(2).to_dict(orient="records") if len(raw) else [],
        "display_sample": df.head(2).to_dict(orient="records") if len(df) else []
