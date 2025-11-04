
import os
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------
JSON_PATH = os.getenv("SHIFTS_JSON_PATH", "user_status_dashboard.json")
IST_TZ = "Asia/Kolkata"  # IST = UTC+05:30

@st.cache_data(show_spinner=False)
def load_json(path: str, mtime: float) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"JSON not found: {path}")
    return pd.read_json(path)

# ---------------------------------------------------------------
# ROW-WISE DATETIME BUILD (timezone-aware using per-row 'timezone')
# ---------------------------------------------------------------
# For each row, choose the first available field in order and convert to IST:
#   1) legacy 'datetime' (parse day-first, LOCALIZE IST)
#   2) 'sort_key'       (if timezone=='IST' -> LOCALIZE IST; else ASSUME UTC -> CONVERT to IST)
#   3) 'datetime_iso'   (same rule as sort_key)
#   4) 'date'+'time'    (LOCALIZE IST)

def build_datetime_rowwise_istaware(df: pd.DataFrame) -> pd.Series:
    n = len(df)
    tzcol = df['timezone'].astype(str).str.upper() if 'timezone' in df.columns else None

    # 1) legacy 'datetime'
    dt_legacy = pd.Series([pd.NaT]*n)
    if 'datetime' in df.columns:
        base = df['datetime'].astype(str).str.replace(' IST', '', regex=False)
        parsed = pd.to_datetime(base, dayfirst=True, errors='coerce')
        dt_legacy = parsed.dt.tz_localize(IST_TZ, nonexistent='shift_forward', ambiguous='NaT')

    # Helper: localize vs utc-convert for an ISO-like column
    def parse_iso_like(colname: str) -> pd.Series:
        out = pd.Series([pd.NaT]*n)
        if colname not in df.columns:
            return out
        # parse naive ISO
        naive = pd.to_datetime(df[colname], errors='coerce')
        if tzcol is not None:
            ist_mask = tzcol.eq('IST') & naive.notna()
            if ist_mask.any():
                out.loc[ist_mask] = naive.loc[ist_mask].dt.tz_localize(IST_TZ, nonexistent='shift_forward', ambiguous='NaT')
            utc_mask = (~ist_mask) & naive.notna()
            if utc_mask.any():
                out.loc[utc_mask] = pd.to_datetime(df.loc[utc_mask, colname], errors='coerce', utc=True).dt.tz_convert(IST_TZ)
        else:
            mask = naive.notna()
            if mask.any():
                out.loc[mask] = pd.to_datetime(df.loc[mask, colname], errors='coerce', utc=True).dt.tz_convert(IST_TZ)
        return out

    dt_sort = parse_iso_like('sort_key')
    dt_iso  = parse_iso_like('datetime_iso')

    dt_dt = pd.Series([pd.NaT]*n)
    if {'date','time'}.issubset(df.columns):
        combo = pd.to_datetime(df['date'].astype(str)+' '+df['time'].astype(str), errors='coerce')
        dt_dt = combo.dt.tz_localize(IST_TZ, nonexistent='shift_forward', ambiguous='NaT')

    # coalesce in order
    dt = dt_legacy.combine_first(dt_sort).combine_first(dt_iso).combine_first(dt_dt)
    return dt

# ---------------------------------------------------------------
# APP
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

# Build row-wise IST-aware datetime
dt = build_datetime_rowwise_istaware(raw)

# Compose DF
df = raw.copy()
df['datetime'] = dt
# Drop non-parsed rows
df = df.dropna(subset=['datetime']).copy()

# Derive IST display
df = df.sort_values('datetime', ascending=False)
df['datetime_ist'] = df['datetime'].dt.tz_convert(IST_TZ)
df['Date'] = df['datetime_ist'].dt.strftime('%d-%m-%Y')
df['Time'] = df['datetime_ist'].dt.strftime('%H:%M:%S') + ' IST'

# --- FIX: Safe per-day counts (avoid duplicate column names) ---
with st.expander('Per-day counts (IST) BEFORE filters'):
    per_day = df['datetime_ist'].dt.strftime('%d-%m-%Y').value_counts().sort_index()
    per_day_df = per_day.reset_index()
    per_day_df.columns = ['Date', 'Count']  # ensure unique column names
    st.dataframe(per_day_df, use_container_width=True, hide_index=True)

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

# Friday→Today window (Mon: Fri→Mon)
st.sidebar.header('View Options')
apply_window = st.sidebar.checkbox('Apply Friday → Today window (Mon: Fri → Mon)', value=True)
view_mode = st.sidebar.radio('Rows to show', ('Latest per user', 'All events'), index=0)
prefer_today = st.sidebar.checkbox('Prefer today if present (for Latest per user)', value=True)

now_ist = pd.Timestamp.now(tz=IST_TZ)
today_ist = now_ist.floor('D')
weekday = today_ist.weekday()

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
    if prefer_today:
        # Choose latest for today if present; else overall latest in window
        df_today = df[df['datetime_ist'].dt.floor('D') == today_ist]
        df_other = df[df['datetime_ist'].dt.floor('D') != today_ist]
        if not df_today.empty:
            latest_today = (
                df_today.sort_values('datetime', ascending=False)
                        .drop_duplicates(subset=['user_id'], keep='first')
            )
            # For users missing today, include latest from other days
            missing_users = set(df['user_id']) - set(latest_today['user_id'])
            latest_other = (
                df_other[df_other['user_id'].isin(missing_users)]
                    .sort_values('datetime', ascending=False)
                    .drop_duplicates(subset=['user_id'], keep='first')
            )
            df = pd.concat([latest_today, latest_other], ignore_index=True)
            df = df.sort_values('datetime', ascending=False)
        else:
            df = (
                df.sort_values('datetime', ascending=False)
                  .drop_duplicates(subset=['user_id'], keep='first')
                  .sort_values('datetime', ascending=False)
            )
    else:
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

with st.expander('Diagnostics: raw vs parsed (first 2 rows)'):
    st.write({
        'raw_sample': raw.head(2).to_dict(orient='records') if len(raw) else [],
        'display_sample': df.head(2).to_dict(orient='records') if len(df) else []
    })
