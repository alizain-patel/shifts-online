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
