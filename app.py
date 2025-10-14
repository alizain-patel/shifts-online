import streamlit as st
import pandas as pd
from datetime import datetime

# Simulated user status data
data = [
    {"user_id": "U001", "name": "Akash Gitaye", "event": "Punch In", "datetime": "2025-10-14 08:00:00"},
    {"user_id": "U002", "name": "Priya Sharma", "event": "Break Start", "datetime": "2025-10-14 10:15:00"},
    {"user_id": "U003", "name": "John Doe", "event": "Punch Out", "datetime": "2025-10-14 17:00:00"},
    {"user_id": "U004", "name": "Emily Davis", "event": "Punch In", "datetime": "2025-10-14 09:00:00"},
    {"user_id": "U005", "name": "Raj Patel", "event": "Break End", "datetime": "2025-10-14 11:00:00"},
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
