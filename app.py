import streamlit as st
import pandas as pd
from datetime import datetime

# Simulated user status data
# Replace this line:
# DATA_FILE = "user_status_dashboard.json"

# With this:
DATA_FILE = "https://raw.githubusercontent.com/<your-username>/<repo-name>/main/user_status_dashboard.json"
]

# Convert to DataFrame
df = pd.DataFrame(data)
df["datetime"] = pd.to_datetime(df["datetime"])
df["date"] = df["datetime"].dt.strftime("%d-%m-%Y")
df["time"] = df["datetime"].dt.strftime("%H:%M:%S")

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

df["status"] = df["event"].apply(get_status)
df["user_display"] = df.apply(lambda row: f"{row['user_id']} {row['status']}", axis=1)

# Streamlit dashboard
st.set_page_config(page_title="User Status Dashboard", layout="wide")
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows today's latest status per user. Refresh manually or set auto-refresh.")

# Display table
st.dataframe(df[["user_display", "name", "date", "event", "time"]].rename(columns={
    "user_display": "User ID & Status",
    "name": "Name",
    "date": "Date",
    "event": "Event",
    "time": "Time"
}))

