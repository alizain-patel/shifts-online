
import os
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------
# CONFIG (locked to IST view)
# ---------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"  # IST = UTC+05:30

# ---------------------------------------------------------------
# LOAD
# ---------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

# ---------------------------------------------------------------
# DATETIME BUILD (LOCKED RULE)
# ---------------------------------------------------------------
# Rule:
# 1) Use legacy 'datetime' (dd/MM/yyyy HH:mm:ss IST) if present => localize IST (no shift)
# 2) Otherwise, treat ISO fields ('sort_key' or 'datetime_iso') as **UTC** and convert to IST
# 3) Otherwise, if 'date'+'time' exist => localize IST
# This avoids UTC-labelled-as-IST errors.

def build_datetime_locked(df: pd.DataFrame) -> pd.Series:
    def localize_ist_series(s: pd.Series) -> pd.Series:
        dt = pd.to_datetime(s, errors='coerce')
        return dt.dt.tz_localize(IST_TZ, nonexistent='shift_forward', ambiguous='NaT')

    def utc_to_ist_series(s: pd.Series) -> pd.Series:
        dt = pd.to_datetime(s, errors='coerce', utc=True)
        return dt.dt.tz_convert(IST_TZ)

    if 'datetime' in df.columns:
        # Legacy display string already intended as IST
        base = df['datetime'].astype(str).str.replace(' IST', '', regex=False)
        dt = pd.to_datetime(base, dayfirst=True, errors='coerce')
        return dt.dt.tz_localize(IST_TZ, nonexistent='shift_forward', ambiguous='NaT')

    if 'sort_key' in df.columns:
        # Assume sort_key is ISO in UTC and convert to IST
        return utc_to_ist_series(df['sort_key'])

    if 'datetime_iso' in df.columns:
        # Assume datetime_iso is ISO in UTC and convert to IST
        return utc_to_ist_series(df['datetime_iso'])

    if {'date', 'time'}.issubset(df.columns):
        base = pd.to_datetime(df['date'].astype(str) + ' ' + df['time'].astype(str), errors='coerce')
        return base.dt.tz_localize(IST_TZ, nonexistent='shift_forward', ambiguous='NaT')

    raise KeyError("Expected one of: 'datetime', 'sort_key', 'datetime_iso', or both 'date' and 'time'.")

# ---------------------------------------------------------------
# APP (final)
# ---------------------------------------------------------------
st.set_page_config(page_title='Live User Status Dashboard — IST', layout='wide')

# Path + mtime
try:
    mtime = os.path.getmtime(JSON_PATH)
    st.caption(f"Reading JSON from: `{JSON_PATH}` (mtime: {pd.to_datetime(mtime, unit='s')})")
except FileNotFoundError:
    st.error(f"JSON file not found: `{JSON_PATH}`")
    st.stop()

# Keep: Clear cache & reload
if st.button('Clear cache & reload'):
    st.cache_data.clear()

# Load raw
raw = load_json(JSON_PATH, mtime)

# Build locked datetime
try:
    dt = build_datetime_locked(raw)
except Exception as e:
    st.error(f"Failed to build datetime: {e}")
    st.stop()

# Compose DF
df = raw.copy()
df['datetime'] = dt
# Drop non-parsed
df = df.dropna(subset=['datetime']).copy()

# Derive IST display
df = df.sort_values('datetime', ascending=False)
df['datetime_ist'] = df['datetime'].dt.tz_convert(IST_TZ)
df['Date'] = df['datetime_ist'].dt.strftime('%d-%m-%Y')
df['Time'] = df['datetime_ist'].dt.strftime('%H:%M:%S') + ' IST'

# Status label
status_map = {
    'Punch In': '🟢 active',
    'Break Start': '🟠 on break',
    'Break End': '🟢 active',
    'Punch Out': '🔴 on leave',
    'On Leave': '🔴 on leave',
}
df['status'] = df['event'].map(lambda e: status_map.get(e, '⚪ unknown')) if 'event' in df.columns else ''
df['Name & Status'] = df.apply(lambda r: f"{r.get('name','')}  {r.get('status','')}", axis=1)

# Friday→Today window (Mon: Fri→Mon), with ability to show all
st.sidebar.header('View Options')
apply_window = st.sidebar.checkbox('Apply Friday → Today window (Mon: Fri → Mon)', value=True)
view_mode = st.sidebar.radio('Rows to show', ('Latest per user', 'All events'), index=0)

now_ist = pd.Timestamp.now(tz=IST_TZ)
today_ist = now_ist.floor('D')
weekday = today_ist.weekday()   # Mon=0, Fri=4

win_info = ''
if apply_window:
    days_back = (weekday - 4) % 7
    last_friday = (today_ist - pd.to_timedelta(days_back, unit='D')).floor('D')
    next_monday = (last_friday + pd.to_timedelta(3, unit='D')).floor('D')
    window_end = next_monday if weekday == 0 else today_ist
    ist_dates = df['datetime_ist'].dt.floor('D')
    mask = (ist_dates >= last_friday) & (ist_dates <= window_end)
    df = df.loc[mask]
    win_info = f"window: {last_friday.strftime('%d-%m-%Y')} → {window_end.strftime('%d-%m-%Y')}"

# Latest per user
if view_mode == 'Latest per user' and 'user_id' in df.columns:
    df = (
        df.sort_values('datetime', ascending=False)
          .drop_duplicates(subset=['user_id'], keep='first')
          .sort_values('datetime', ascending=False)
    )

# UI
st.title('🟢🔴 Live User Status Dashboard — IST')
st.caption('Shows latest status per user or all events in **IST (Asia/Kolkata)**. ' + win_info)

st.dataframe(
    df[[c for c in ['Name & Status','Date','event','Time'] if c in df.columns]].rename(columns={'event':'Event'}),
    use_container_width=True,
    hide_index=True,
)

# Footer & tiny diagnostics
last_ist = df['datetime_ist'].max() if len(df) else None
st.caption(f"Last event (IST): {last_ist if last_ist is not None else 'N/A'} · Total rows loaded: {len(df)}")

with st.expander('Diagnostics: raw vs parsed (first row)'):
    st.write({
        'first_raw': raw.head(1).to_dict(orient='records')[0] if len(raw) else {},
        'first_display': df.head(1).to_dict(orient='records')[0] if len(df) else {}
    })
