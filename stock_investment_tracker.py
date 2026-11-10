import streamlit as st
import pandas as pd
import numpy as np
import datetime

# Page Configuration
st.set_page_config(
    page_title="Stock Investment Tracker & Web Hub",
    page_icon="📈",
    layout="wide"
)

# Custom CSS to pin the web hub text box / input area to the bottom of the page
st.markdown("""
    <style>
        /* Pin the bottom container / web hub text area to the bottom of the viewport */
        .fixed-bottom-hub {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            background-color: #0e1117;
            padding: 10px 20px;
            z-index: 99999;
            border-top: 1px solid #303030;
            box-shadow: 0px -2px 10px rgba(0,0,0,0.5);
        }
        /* Add bottom padding to the main block so content doesn't get hidden behind the fixed bar */
        .reportview-container .main .block-container {
            padding-bottom: 100px;
        }
        .main {
            margin-bottom: 80px;
        }
    </style>
""", unsafe_allow_html=True)

# Application Header
st.title("📊 Stock Investment Tracker & Web Hub")
st.markdown("Track your portfolio, analyze stocks, and interact with the integrated web hub below.")

# Sidebar Controls
st.sidebar.header("Portfolio Settings")
portfolio_cash = st.sidebar.number_input("Available Cash ($)", value=5000.0, step=100.0)
risk_tolerance = st.sidebar.selectbox("Risk Tolerance", ["Conservative", "Moderate", "Aggressive"])

# Main Dashboard Tabs
tab1, tab2, tab3 = st.tabs(["Portfolio Overview", "Stock Analyzer", "Market News"])

with tab1:
    st.subheader("Your Holdings")
    # Sample Portfolio Data
    portfolio_data = pd.DataFrame({
        "Ticker": ["AAPL", "TSLA", "MSFT", "GOOGL"],
        "Shares": [10, 5, 8, 4],
        "Avg Buy Price ($)": [175.50, 210.00, 320.00, 140.00],
        "Current Price ($)": [185.00, 205.00, 335.00, 145.00]
    })
    
    portfolio_data["Total Value ($)"] = portfolio_data["Shares"] * portfolio_data["Current Price ($)"]
    portfolio_data["Profit/Loss ($)"] = portfolio_data["Total Value ($)"] - (portfolio_data["Shares"] * portfolio_data["Avg Buy Price ($)"])
    
    st.dataframe(portfolio_data, use_container_width=True)
    
    total_portfolio_value = portfolio_data["Total Value ($)"].sum()
    total_pl = portfolio_data["Profit/Loss ($)"].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Portfolio Value", f"${total_portfolio_value:,.2f}")
    col2.metric("Total Profit / Loss", f"${total_pl:,.2f}", delta=f"${total_pl:,.2f}")
    col3.metric("Available Cash", f"${portfolio_cash:,.2f}")

with tab2:
    st.subheader("Stock Deep Dive")
    selected_stock = st.selectbox("Select Ticker for Analysis", ["AAPL", "TSLA", "MSFT", "GOOGL", "AMZN"])
    st.info(f"Displaying technical indicators and analysis metrics for {selected_stock}...")
    
    # Generate dummy chart data
    chart_data = pd.DataFrame(
        np.random.randn(20, 3) * 5 + 150,
        columns=['Open', 'High', 'Low']
    )
    st.line_chart(chart_data)

with tab3:
    st.subheader("Latest Financial News & Insights")
    st.write("- **Fed Signals Steady Interest Rates**: Markets respond positively to recent economic data reports.")
    st.write("- **Tech Sector Rally**: Artificial Intelligence leaders continue driving quarterly earnings beats.")
    st.write("- **Energy Markets Shift**: Crude inventories report unexpected drawdowns this week.")

# Web Hub Text Box pinned to the bottom of the page
st.markdown('<div class="fixed-bottom-hub">', unsafe_allow_html=True)
with st.container():
    col_input, col_btn = st.columns([6, 1])
    with col_input:
        web_hub_query = st.text_input("Web Hub Input", placeholder="Type a command, query, or prompt for the web hub...", label_visibility="collapsed")
    with col_btn:
        hub_submitted = st.button("Send 🚀", use_container_width=True)
        
    if hub_submitted and web_hub_query:
        st.toast(f"Hub Received: '{web_hub_query}'", icon="💬")
st.markdown('</div>', unsafe_allow_html=True)
