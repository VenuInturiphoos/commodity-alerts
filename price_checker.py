import os
import yfinance as yf
import requests
import pandas as pd
from datetime import datetime, timedelta

class PriceChecker:
    def __init__(self, config):
        self.config = config
        self.commodities = config.get('commodities', {})
        self.stocks = config.get('stocks', {})
        self.usd_inr_rate = self.get_usd_inr_rate()
        
        # Supabase config
        self.supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/market_data'
        self.supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'

        # Dhan config
        self.dhan_client_id = os.environ.get('DHAN_CLIENT_ID')
        self.dhan_access_token = os.environ.get('DHAN_ACCESS_TOKEN')
        self.dhan_active = bool(self.dhan_client_id and self.dhan_access_token)
        self.dhan = None
        self.dhan_master = None
        
        if self.dhan_active:
            try:
                from dhanhq import dhanhq
                self.dhan = dhanhq(self.dhan_client_id, self.dhan_access_token)
                print("DhanHQ API authenticated successfully.")
                self.load_dhan_master()
            except Exception as e:
                print(f"Failed to initialize DhanHQ: {e}")
                self.dhan_active = False

    def load_dhan_master(self):
        try:
            print("Downloading DhanHQ Instrument Master...")
            # We use the compact version to save memory/time
            self.dhan_master = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv')
            print("DhanHQ Instrument Master loaded.")
        except Exception as e:
            print(f"Error loading Dhan Master CSV: {e}")
            self.dhan_active = False

    def get_usd_inr_rate(self):
        try:
            ticker = yf.Ticker('INR=X')
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                rate = hist['Close'].iloc[-1]
                print(f"Current USD/INR Exchange Rate: ₹{rate:.2f}")
                return rate
        except Exception as e:
            print(f"Error fetching USD/INR rate: {e}")
        print("Using fallback USD/INR exchange rate of 83.50")
        return 83.50

    def get_dhan_security_id(self, symbol, exchange="NSE"):
        if not self.dhan_active or self.dhan_master is None:
            return None
            
        clean_symbol = symbol.replace('.NS', '').replace('.BO', '')
        
        # Try to find the exact symbol in the master CSV
        instrument = self.dhan_master[
            (self.dhan_master['SEM_EXM_EXCH_ID'] == exchange) & 
            (self.dhan_master['SEM_CUSTOM_SYMBOL'] == clean_symbol)
        ]
        
        if not instrument.empty:
            return str(instrument['SEM_SM_SECURITY_ID'].values[0])
        return None

    def get_dhan_levels(self, security_id):
        try:
            # Get historical daily data to find yesterday's OHLC
            today = datetime.now()
            # Look back up to 10 days to ensure we get the last trading day
            from_date = (today - timedelta(days=10)).strftime('%Y-%m-%d')
            to_date = today.strftime('%Y-%m-%d')
            
            data = self.dhan.historical_daily_data(
                security_id=security_id,
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                expiry_code=0,
                from_date=from_date,
                to_date=to_date
            )
            
            if data and data.get('data') and len(data['data']['close']) >= 2:
                # Get the previous day's data
                high = data['data']['high'][-2]
                low = data['data']['low'][-2]
                close = data['data']['close'][-2]
                
                p = (high + low + close) / 3
                r1 = (p * 2) - low
                r2 = p + (high - low)
                s1 = (p * 2) - high
                s2 = p - (high - low)
                
                return {
                    "R2": r2,
                    "R1": r1,
                    "Pivot": p,
                    "S1": s1,
                    "S2": s2
                }
        except Exception as e:
            print(f"Error fetching historical data from Dhan for {security_id}: {e}")
        return None

    def get_dhan_current_price(self, security_id):
        try:
            # Intraday minute data
            data = self.dhan.intraday_minute_data(
                security_id=security_id,
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY"
            )
            if data and data.get('data') and len(data['data']['close']) > 0:
                # Return the most recent close price
                return data['data']['close'][-1]
        except Exception as e:
            print(f"Error fetching live price from Dhan for {security_id}: {e}")
        return None

    def get_support_resistance_levels(self, ticker_symbol, conversion_rate=1.0):
        # Try DhanHQ first if it's an Indian stock
        if self.dhan_active and '.NS' in ticker_symbol:
            sec_id = self.get_dhan_security_id(ticker_symbol)
            if sec_id:
                levels = self.get_dhan_levels(sec_id)
                if levels:
                    return levels
        
        # Fallback to yfinance
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="5d")
            
            if len(hist) < 2:
                return None
                
            prev_day = hist.iloc[-2]
            
            high = prev_day['High'] * conversion_rate
            low = prev_day['Low'] * conversion_rate
            close = prev_day['Close'] * conversion_rate
            
            p = (high + low + close) / 3
            r1 = (p * 2) - low
            r2 = p + (high - low)
            s1 = (p * 2) - high
            s2 = p - (high - low)
            
            return {
                "R2": r2,
                "R1": r1,
                "Pivot": p,
                "S1": s1,
                "S2": s2
            }
        except Exception as e:
            print(f"Error calculating levels for {ticker_symbol} via yfinance: {e}")
            return None

    def get_current_price(self, ticker_symbol, conversion_rate=1.0):
        # Try DhanHQ first
        if self.dhan_active and '.NS' in ticker_symbol:
            sec_id = self.get_dhan_security_id(ticker_symbol)
            if sec_id:
                price = self.get_dhan_current_price(sec_id)
                if price:
                    return price
        
        # Fallback to yfinance
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                price = hist['Close'].iloc[-1]
                return price * conversion_rate
            return None
        except Exception as e:
            print(f"Error fetching current price for {ticker_symbol} via yfinance: {e}")
            return None

    def evaluate_levels(self, name, symbol, levels, current_price):
        alerts = []
        alert_status = None
        
        if levels is None or current_price is None:
            print(f"Could not fetch data for {name}. Skipping.")
            return alerts, alert_status
            
        print(f"Current Price: ₹{current_price:.2f}")
        print(f"Levels -> R2: ₹{levels['R2']:.2f}, R1: ₹{levels['R1']:.2f}, P: ₹{levels['Pivot']:.2f}, S1: ₹{levels['S1']:.2f}, S2: ₹{levels['S2']:.2f}")
        
        threshold_pct = 0.002 
        
        for level_name in ['R1', 'R2']:
            level_price = levels[level_name]
            if abs(current_price - level_price) / level_price <= threshold_pct:
                alert_msg = f"testing Resistance {level_name}"
                alerts.append(f"ALERT: {name} ({symbol}) is {alert_msg} at ₹{level_price:.2f}. Current price: ₹{current_price:.2f}.")
                alert_status = alert_msg
                
        for level_name in ['S1', 'S2']:
            level_price = levels[level_name]
            if abs(current_price - level_price) / level_price <= threshold_pct:
                alert_msg = f"testing Support {level_name}"
                alerts.append(f"ALERT: {name} ({symbol}) is {alert_msg} at ₹{level_price:.2f}. Current price: ₹{current_price:.2f}.")
                alert_status = alert_msg

        return alerts, alert_status

    def sync_to_supabase(self, payload):
        try:
            headers = {
                'apikey': self.supabase_key,
                'Authorization': f'Bearer {self.supabase_key}',
                'Content-Type': 'application/json',
                'Prefer': 'resolution=merge-duplicates'
            }
            response = requests.post(self.supabase_url, headers=headers, json=payload, timeout=15)
            if response.status_code in [200, 201]:
                print(f"Successfully synced {len(payload)} market data records to Supabase.")
            else:
                print(f"Failed to sync to Supabase. Status: {response.status_code}, {response.text}")
        except Exception as e:
            print(f"Error syncing to Supabase: {e}")

    def check_alerts(self):
        alerts = []
        market_data_payload = []
        
        # 1. Check Commodities
        for symbol, data in self.commodities.items():
            name = data['name']
            mcx_multiplier = data.get('mcx_multiplier', 1.0)
            conversion = self.usd_inr_rate * mcx_multiplier
            
            print(f"\nChecking Commodity {name} ({symbol}) in INR...")
            levels = self.get_support_resistance_levels(symbol, conversion)
            current_price = self.get_current_price(symbol, conversion)
            
            new_alerts, alert_status = self.evaluate_levels(name, symbol, levels, current_price)
            alerts.extend(new_alerts)
            
            if levels and current_price:
                market_data_payload.append({
                    "symbol": symbol,
                    "name": name,
                    "asset_type": "Commodity",
                    "current_price": round(current_price, 2),
                    "r1": round(levels['R1'], 2),
                    "r2": round(levels['R2'], 2),
                    "s1": round(levels['S1'], 2),
                    "s2": round(levels['S2'], 2),
                    "pivot": round(levels['Pivot'], 2),
                    "alert_status": alert_status
                })
            
        # 2. Check Stocks
        for symbol, data in self.stocks.items():
            name = data['name']
            
            print(f"\nChecking Stock {name} ({symbol})...")
            levels = self.get_support_resistance_levels(symbol, 1.0)
            current_price = self.get_current_price(symbol, 1.0)
            
            new_alerts, alert_status = self.evaluate_levels(name, symbol, levels, current_price)
            alerts.extend(new_alerts)
            
            if levels and current_price:
                market_data_payload.append({
                    "symbol": symbol,
                    "name": name,
                    "asset_type": "Stock",
                    "current_price": round(current_price, 2),
                    "r1": round(levels['R1'], 2),
                    "r2": round(levels['R2'], 2),
                    "s1": round(levels['S1'], 2),
                    "s2": round(levels['S2'], 2),
                    "pivot": round(levels['Pivot'], 2),
                    "alert_status": alert_status
                })

        if market_data_payload:
            self.sync_to_supabase(market_data_payload)

        return alerts
