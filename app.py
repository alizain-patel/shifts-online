
import os
import pandas as pd
import streamlit as st

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"  # IANA timezone for IST

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, file_mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)


def parse_datetime_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize to IST-aware datetimes.
    Precedence:
      1) legacy 'datetime' (dd/MM/yyyy HH:mm:ss IST) -> localize IST
      2) 'datetime_iso' + 'timezone' == 'IST' -> localize IST
      3) 'datetime_iso' (no timezone or unknown) -> assume UTC then convert to IST
      4) 'date' + 'time' -> localize IST
    """
    if "datetime" in df.columns:
        base = df["datetime"].astype(str).str.replace(" IST", "", regex=False)
        dt = pd.to_datetime(base, dayfirst=True, errors="coerce")
        df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    elif "datetime_iso" in df.columns:
        tz_series = df.get("timezone")
        if tz_series is not None and tz_series.astype(str).str.upper().eq("IST").all():
            # The compat PS script writes timezone='IST' and datetime_iso in local IST
            dt = pd.to_datetime(df["datetime_iso"], errors="coerce")
            df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
        else:
            # Fallback: treat ISO as UTC and convert to IST
            dt = pd.to_datetime(df["datetime_iso"], errors="coerce", utc=True)
            df["datetime"] = dt.dt.tz_convert(IST_TZ)
    elif {"date", "time"}.issubset(df.columns):
        dt = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
        df["datetime"] = dt.dt.tz_localize(IST_TZ, nonexistent="shift_forward", ambiguous="NaT")
    else:
        raise KeyError("Expected 'datetime' or 'datetime_iso' or both 'date' and 'time' columns")

    df = df.dropna(subset=["datetime"]).copy()
    return df


def apply_window(df: pd.DataFrame, friday_to_today: bool, latest_day_only: bool):
    """Apply Friday->Today (or Friday->Monday) window and optional latest-day filter."""
    now_ist = pd.Timestamp.now(tz=IST_TZ)
    today_ist = now_ist.floor("D")
    weekday = today_ist.weekday()  # Mon=0, Fri=4

    days_back_to_friday = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back_to_friday, unit="D")).floor("D")
    next_monday = last_friday + pd.to_timedelta(3, unit="D")

    if friday_to_today:
        window_end = next_monday if weekday == 0 else today_ist
        ist_dates = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D")
        mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
        df = df.loc[mask]

    if latest_day_only and not df.empty:
        latest_day = df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D").max()
        df = df[df["datetime"].dt.tz_convert(IST_TZ).dt.floor("D") == latest_day]

    return df, last_friday.date(), (next_monday if weekday == 0 else today_ist).date()


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

# Cache busting
try:
    file_mtime = os.path.getmtime(JSON_PATH)
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`. Set env var `SHIFTS_JSON_PATH` or place the file next to app.py.")
    st.stop()

raw_df = load_json(JSON_PATH, file_mtime)

df = parse_datetime_ist(raw_df)

# Sort and derive display fields in IST
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

# -------------------------------------------------------------------
# CONTROLS
# -------------------------------------------------------------------
st.sidebar.header("View Options")
friday_to_today = st.sidebar.checkbox("Filter to Friday → Today (or Friday → Monday)", value=True)
latest_day_only = st.sidebar.checkbox("Show latest day only", value=False)
view_mode = st.sidebar.radio("Rows to show", ("Latest per user", "All events"), index=0)

# Apply filters
window_info = ""
df, start_d, end_d = apply_window(df, friday_to_today, latest_day_only)
window_info = f" (window: {start_d.strftime('%d-%m-%Y')} → {end_d.strftime('%d-%m-%Y')})" if friday_to_today else ""

# Final selection
df_view = latest_per_user(df) if view_mode == "Latest per user" else df

# -------------------------------------------------------------------
# UI
# -------------------------------------------------------------------
st.title("🟢🔴 Live User Status Dashboard — IST")
st.caption("Times are converted/localized to **IST (Asia/Kolkata)** from source data." + window_info)

cols = ["Name & Status", "Date", "event", "Time"]
ren = {"event": "Event"}

st.dataframe(
    df_view[cols].rename(columns=ren),
    use_container_width=True,
    hide_index=True,
)

# Diagnostics: per-day counts in IST
with st.expander("Diagnostics (per-day counts in IST)"):
    by_day = df["datetime_ist"].dt.strftime("%d-%m-%Y").value_counts().sort_index()
    st.write(by_day)

# Footer: last event time in IST
if "sort_key" in raw_df.columns:
    # Treat sort_key as local IST when timezone=='IST', else as UTC -> IST
    tz_series = raw_df.get("timezone")
    if tz_series is not None and tz_series.astype(str).str.upper().eq("IST").all():
        last_iso = pd.to_datetime(raw_df["sort_key"], errors="coerce").max()
        last_ist = pd.Timestamp(last_iso, tz=IST_TZ) if pd.notna(last_iso) else None
    else:
        last_iso = pd.to_datetime(raw_df["sort_key"], errors="coerce", utc=True).max()
        last_ist = pd.Timestamp(last_iso).tz_convert(IST_TZ) if pd.notna(last_iso) else None
else:
    last_ist = df["datetime_ist"].max()

st.caption(f"Last event (IST): **{last_ist}** · Source: `{os.path.basename(JSON_PATH)}`")
