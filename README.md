# Trade Analyser — Commodity & Stock Alert System 📈

[![Live Website](https://img.shields.io/badge/Live%20Portal-Visit%20Website-brightgreen?style=for-the-badge&logo=googlechrome)](https://VenuInturiphoos.github.io/commodity-alerts/)
[![GitHub Actions](https://img.shields.io/badge/Workflow-Automated%20Price%20Alerter-blue?style=for-the-badge&logo=githubactions)](https://github.com/VenuInturiphoos/commodity-alerts/actions)
[![License](https://img.shields.io/badge/License-MIT-purple?style=for-the-badge)](LICENSE)

An automated market intelligence engine and real-time dashboard for **MCX Commodities** and **Nifty 50 Stocks**. It monitors multi-timeframe extremes (Yearly, Monthly, Weekly Lows/Highs, Pivot Points S1/S2/R1/R2, and Bollinger Bands) and dispatches instant alerts via Email and Telegram.

---

## 🌐 Quick Access Links

| Service | Link | Description |
| :--- | :--- | :--- |
| **🚀 Live Portal / Dashboard** | [**https://VenuInturiphoos.github.io/commodity-alerts/**](https://VenuInturiphoos.github.io/commodity-alerts/) | Main dashboard with Live Market, Alert Zone, and Paper Trading |
| **🔥 Alert Zone** | [**https://VenuInturiphoos.github.io/commodity-alerts/#alerts-tab**](https://VenuInturiphoos.github.io/commodity-alerts/) | Real-time breakdown/breakout alerts for assets testing extreme levels |
| **📈 Paper Trading** | [**https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab**](https://VenuInturiphoos.github.io/commodity-alerts/) | Virtual trading simulator for MCX & NSE Futures with margin calculation |
| **🔕 Unsubscribe Portal** | [**https://VenuInturiphoos.github.io/commodity-alerts/unsubscribe.html**](https://VenuInturiphoos.github.io/commodity-alerts/unsubscribe.html) | Manage your email alert subscription preferences |
| **📂 Source Repository** | [**https://github.com/VenuInturiphoos/commodity-alerts**](https://github.com/VenuInturiphoos/commodity-alerts) | GitHub source repository & workflow automation |

---

## ⚡ Core Features

- **Multi-Timeframe Extreme Alerts**:
  - **Yearly Low / High** (1-Year Low/High within 1% threshold)
  - **Monthly Low / High** (1-Month Low/High within 0.5% threshold)
  - **Weekly Low / High** (1-Week Low/High within 0.5% threshold)
  - **3-Month & All-Time Lows/Highs**
  - **Algorithmic S1/S2 Support & R1/R2 Resistance**
- **Commodity 30-Day Average Movement Alerts**: Alerts when an MCX commodity breaks above or below its 30-day average percentage move.
- **Multi-Channel Notifications**:
  - Instant HTML & Plain Text **Email Alerts** via SMTP
  - Real-time **Telegram Alerts** with actionable trade recommendations
- **Live TradingView Charts**: Integrated interactive Lightweight-Charts modals.
- **Paper Trading Engine**: Virtual portfolio tracking for MCX & NSE F&O contracts with 15% standard margin requirements, limit orders, and stop losses.
- **Automated Cron Jobs**: Runs 24/7 every 3 minutes via GitHub Actions.

---

## 📊 Tracked Assets

### 🛢️ MCX Commodities
- **Crude Oil** (`CL=F`)
- **Natural Gas** (`NG=F`)
- **Gold** (`GC=F`)
- **Silver** (`SI=F`)
- **Copper** (`HG=F`)
- **Aluminium** (`ALI=F`)
- **Zinc** (`ZNC=F`)
- **Electricity** (`HP=F` / MCX Base Load)

### 📈 Indian Stocks (Nifty 50)
- RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK, BHARTIARTL, SBIN, LT, ITC, BAJFINANCE, ASIANPAINT, TITAN, ADANIENT, and more.

---

## 🛠️ Architecture & Tech Stack

- **Frontend**: Vanilla HTML5, CSS3 (Glassmorphism, Dark/Light mode), JavaScript ES6+
- **Charts**: Lightweight Charts (v4.1.3) + TradingView Modals
- **Backend**: Python 3.11 (`yfinance`, `ta`, `SmartAPI`, `pandas`, `pytz`)
- **Database**: Supabase (PostgreSQL, Realtime, REST API)
- **CI/CD**: GitHub Actions Scheduled Workflows (`schedule.yml`, `pages.yml`)

---

## 💻 Local Development

1. **Clone the repository:**
   ```bash
   git clone https://github.com/VenuInturiphoos/commodity-alerts.git
   cd commodity-alerts
   ```

2. **Install Python dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Run a manual price check:**
   ```bash
   python main.py
   ```

4. **Launch the Telegram Bot (optional):**
   ```bash
   python telegram_bot.py
   ```

5. **Preview the Frontend locally:**
   ```bash
   python -m http.server 8000 --directory web
   # Open http://localhost:8000 in your browser
   ```
