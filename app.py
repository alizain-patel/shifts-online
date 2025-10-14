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
    if event in ["Punch In", "Break End"]:
        return "🟢 active"
    elif event == "Break Start":
        return "🔴 on break"
    elif event == "Punch Out":
        return "🔴 on leave"
    else:
        return "⚪ unknown"

latest_df["status"] = latest_df["event"].apply(get_status)
latest_df["name_status"] = latest_df.apply(lambda row: f"{row['name']} {row['status']}", axis=1)

# Display table
# Streamlit dashboard
st.set_page_config(page_title="User Status Dashboard", layout="wide")
st.title("🟢🔴 Live User Status Dashboard")
st.caption("Shows only the latest status per user. Refresh manually or set auto-refresh.")

# Display table
st.dataframe(
    latest_df[["name_status", "date", "event", "time"]].rename(
        columns={
            "name_status": "Name & Status",
            "date": "Date",
            "event": "Event",
            "time": "Time"
        }
    )
)

