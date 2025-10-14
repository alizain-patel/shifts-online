import streamlit as st
import pandas as pd
from datetime import datetime

# Load data
df = pd.read_json("userstatus.json")
df["datetime"] = pd.to_datetime(df["datetime"])
df["date"] = df["datetime"].dt.strftime("%d-%m-%Y")
df["time"] = df["datetime"].dt.strftime("%H:%M:%S")

# Sort by datetime so latest event is last
df = df.sort_values(["user_id", "datetime"])

# Keep only the latest event for each user
latest_df = df.groupby("user_id", as_index=False).last()

# Status logic
def get_status(event):
    if event == "Punch In" or event == "Break End":
        return "🟢 active"
    elif event == "Break Start":
        return "🔴 on break"
    elif event == "Punch Out":
        return "🔴 on leave"
    else:
        return "⚪ unknown"

latest_df["status"] = latest_df["event"].apply(get_status)
latest_df["user_display"] = latest_df.apply(lambda row: f"{row['user_id']} {row['status']}", axis=1)

# Streamlit dashboard
st.set_page_config(page_title="User Status Dashboard", layout="wide")
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows only the latest status per user. Refresh manually or set auto-refresh.")

# Display table
st.dataframe(
    latest_df[["user_display", "name", "date", "event", "time"]].rename(
        columns={
            "user_display": "User ID & Status",
            "name": "Name",
            "date": "Date",
            "event": "Event",
            "time": "Time"
        }
    )
)
