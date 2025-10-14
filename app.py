import streamlit as st
import pandas as pd
from datetime import datetime

# === Load live JSON from GitHub ===
# Replace with your actual GitHub username and repo name
DATA_FILE = "https://github.com/alizain-patel/shifts-online/blob/main/user_status_dashboard.json"

# Load the data
try:
    df = pd.read_json(DATA_FILE)
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# Convert datetime column to datetime type
df["datetime"] = pd.to_datetime(df["datetime"])
df["date"] = df["datetime"].dt.strftime("%d-%m-%Y")
df["time"] = df["datetime"].dt.strftime("%H:%M:%S")

# Filter for today's entries
today = datetime.now().date()
df_today = df[df["datetime"].dt.date == today]

# Determine status and color
def get_status(event):
    if event == "Punch In":
        return "🟢 online"
    elif event in ["Break Start", "Break End"]:
        return "🔴 on break"
    elif event == "Punch Out":
        return "🔴 on leave"
    else:
        return "⚪ unknown"

df_today["status"] = df_today["event"].apply(get_status)
df_today["user_display"] = df_today.apply(lambda row: f"{row['user_id']} {row['status']}", axis=1)

# Keep only the latest event per user
df_latest = df_today.sort_values("datetime").groupby("user_id", as_index=False).last()

# Streamlit dashboard
st.set_page_config(page_title="User Status Dashboard", layout="wide")
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Displays the latest status per user for today. Refresh manually or set auto-refresh.")

# Display table
st.dataframe(df_latest[["user_display", "name", "date", "event", "time"]].rename(columns={
    "user_display": "User ID & Status",
    "name": "Name",
    "date": "Date",
    "event": "Event",
    "time": "Time"
}))
