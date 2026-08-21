import os
import yfinance as yf
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz
import json
import math
from dhanhq import dhanhq, DhanContext

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
        self.dhan_active = False
        self.dhan = None
        self.dhan_master = None
        
        if self.dhan_client_id and self.dhan_access_token:
            try:
                ctx = DhanContext(self.dhan_client_id, self.dhan_access_token)
                self.dhan = dhanhq(ctx)
                self.dhan_active = True
                print("DhanHQ API Initialized Successfully.")
                self.load_dhan_master()
            except Exception as e:
                print(f"Failed to initialize DhanHQ: {e}")
                self.dhan_active = False

    def load_dhan_master(self):
        try:
            print("Downloading DhanHQ Instrument Master...")
            # Using usecols drastically reduces memory and parsing time for the 50MB CSV
            cols_to_use = [
                'SEM_EXM_EXCH_ID',
                'SEM_INSTRUMENT_NAME',
                'SM_SYMBOL_NAME',
                'SEM_EXPIRY_DATE',
                'SEM_SMST_SECURITY_ID',
                'SEM_CUSTOM_SYMBOL'
            ]
            self.dhan_master = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', usecols=cols_to_use, low_memory=False)
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
            return str(instrument['SEM_SMST_SECURITY_ID'].values[0])
        return None

    def get_dhan_mcx_near_month(self, base_symbol):
        if not self.dhan_active or self.dhan_master is None:
            print(f"Skipping Dhan MCX for {base_symbol}: dhan_active={self.dhan_active}, dhan_master is loaded={self.dhan_master is not None}")
            return None
            
        try:
            mcx = self.dhan_master[
                (self.dhan_master['SEM_EXM_EXCH_ID'] == 'MCX') & 
                (self.dhan_master['SEM_INSTRUMENT_NAME'] == 'FUTCOM') &
                (self.dhan_master['SM_SYMBOL_NAME'] == base_symbol)
            ].copy()
            
            if mcx.empty:
                print(f"No active MCX futures found in Dhan master for {base_symbol}!")
                return None
            
            now = datetime.now()
            mcx['EXP_DATE'] = pd.to_datetime(mcx['SEM_EXPIRY_DATE'])
            mcx = mcx[mcx['EXP_DATE'] >= now].sort_values('EXP_DATE')
            
            if not mcx.empty:
                return str(mcx.iloc[0]['SEM_SMST_SECURITY_ID'])
            return None
        except Exception as e:
            print(f"Error fetching MCX near month for {base_symbol}: {e}")
            return None

    def get_dhan_levels(self, security_id, exchange_segment="NSE_EQ", instrument_type="EQUITY"):
        try:
            # Get historical daily data to find yesterday's OHLC
            today = datetime.now()
            # Look back up to 10 days to ensure we get the last trading day
            from_date = (today - timedelta(days=10)).strftime('%Y-%m-%d')
            to_date = today.strftime('%Y-%m-%d')
            
            data = self.dhan.historical_daily_data(
                security_id=security_id,
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                expiry_code=0,
                from_date=from_date,
                to_date=to_date
            )
            
            if not data or data.get('status') == 'failure' or not data.get('data'):
                print(f"Dhan API historical_daily_data failed for {security_id}. Response: {data}")
                return None
                
            if data and data.get('data'):
                if len(data['data']['close']) >= 2:
                    # Get the previous day's data for pivot points
                    high = data['data']['high'][-2]
                    low = data['data']['low'][-2]
                    close = data['data']['close'][-2]
                    
                    p = (high + low + close) / 3
                    r1 = (p * 2) - low
                    r2 = p + (high - low)
                    s1 = (p * 2) - high
                    s2 = p - (high - low)
                    
                    return {
                        "Pivot": p,
                        "R1": r1,
                        "R2": r2,
                        "S1": s1,
                        "S2": s2
                    }
                else:
                    print(f"Dhan API returned successful historical data, but it has less than 2 candles! Length: {len(data['data']['close'])}")
                    return None
            return None
        except Exception as e:
            print(f"Error fetching historical data from Dhan for {security_id}: {e}")
            return None

    def get_dhan_current_price(self, security_id, exchange_segment="NSE_EQ", instrument_type="EQUITY"):
        try:
            now = datetime.now()
            from_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
            to_date = now.strftime("%Y-%m-%d")
            
            # Intraday minute data
            data = self.dhan.intraday_minute_data(
                security_id=security_id,
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=to_date
            )
            
            if not data or data.get('status') == 'failure' or not data.get('data'):
                print(f"Dhan API intraday_minute_data failed for {security_id}. Response: {data}")
                
            if data and data.get('data') and len(data['data']['close']) > 0:
                # Return the most recent close price
                return data['data']['close'][-1]
        except Exception as e:
            print(f"Error fetching live price from Dhan for {security_id}: {e}")
        return None

    def get_support_resistance_levels(self, ticker_symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=None):
        levels = None
        
        # 1. Try to get Pivot/R/S from DhanHQ
        if is_commodity:
            print(f"Attempting DhanHQ for commodity {ticker_symbol}...")
            if self.dhan_active:
                sec_id = self.get_dhan_mcx_near_month(ticker_symbol)
                if sec_id:
                    print(f"Found SecID for {ticker_symbol}: {sec_id}. Fetching levels...")
                    levels = self.get_dhan_levels(sec_id, exchange_segment="MCX_COMM", instrument_type="FUTCOM")
        else:
            print(f"Attempting DhanHQ for stock {ticker_symbol}...")
            if self.dhan_active and '.NS' in ticker_symbol:
                sec_id = self.get_dhan_security_id(ticker_symbol)
                if sec_id:
                    print(f"Found SecID for {ticker_symbol}: {sec_id}. Fetching levels...")
                    levels = self.get_dhan_levels(sec_id)

        # 2. Fetch all-time yfinance data for multi-timeframe extremes and fallback Pivot/R/S
        ticker_symbol = yf_symbol if yf_symbol else ticker_symbol
        try:
            print(f"Fetching yfinance period='max' for extremes for {ticker_symbol}...")
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="max")
            
            if len(hist) < 2:
                print(f"Not enough historical data from yfinance for {ticker_symbol}.")
                return levels # Return dhan levels if yfinance fails
                
            # If DhanHQ failed, calculate Pivot/R/S from yfinance
            if not levels:
                print(f"Using yfinance for Pivot/R/S fallback for {ticker_symbol}...")
                high = hist['High'].iloc[-2] * fallback_multiplier
                low = hist['Low'].iloc[-2] * fallback_multiplier
                close = hist['Close'].iloc[-2] * fallback_multiplier
                
                p = (high + low + close) / 3
                levels = {
                    "Pivot": p,
                    "R1": (p * 2) - low,
                    "R2": p + (high - low),
                    "S1": (p * 2) - high,
                    "S2": p - (high - low)
                }
                
            # Now calculate the multi-timeframe extremes
            now = datetime.now(pytz.timezone('UTC'))
            if hist.index.tzinfo is None:
                hist.index = hist.index.tz_localize('UTC')
            
            # Slices
            hist_1m = hist[hist.index >= (now - timedelta(days=30))]
            hist_2m = hist[hist.index >= (now - timedelta(days=60))]
            hist_3m = hist[hist.index >= (now - timedelta(days=90))]
            hist_1y = hist[hist.index >= (now - timedelta(days=365))]
            
            levels['MonthlyHigh'] = hist_1m['High'].max() * fallback_multiplier if not hist_1m.empty else None
            levels['MonthlyLow'] = hist_1m['Low'].min() * fallback_multiplier if not hist_1m.empty else None
            
            levels['TwoMonthHigh'] = hist_2m['High'].max() * fallback_multiplier if not hist_2m.empty else None
            levels['TwoMonthLow'] = hist_2m['Low'].min() * fallback_multiplier if not hist_2m.empty else None
            
            levels['ThreeMonthHigh'] = hist_3m['High'].max() * fallback_multiplier if not hist_3m.empty else None
            levels['ThreeMonthLow'] = hist_3m['Low'].min() * fallback_multiplier if not hist_3m.empty else None
            
            levels['OneYearHigh'] = hist_1y['High'].max() * fallback_multiplier if not hist_1y.empty else None
            levels['OneYearLow'] = hist_1y['Low'].min() * fallback_multiplier if not hist_1y.empty else None
            
            levels['AllTimeHigh'] = hist['High'].max() * fallback_multiplier
            levels['AllTimeLow'] = hist['Low'].min() * fallback_multiplier
            
        except Exception as e:
            print(f"Error fetching historical extremes from yfinance for {ticker_symbol}: {e}")
            
        return levels

    def get_intrinsic_value(self, yf_symbol):
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info
            eps = info.get('trailingEps')
            bv = info.get('bookValue')
            if eps and bv and eps > 0 and bv > 0:
                graham_number = math.sqrt(22.5 * eps * bv)
                return round(graham_number, 2)
        except Exception as e:
            print(f"Error calculating intrinsic value for {yf_symbol}: {e}")
        return None

    def get_current_price(self, ticker_symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=None):
        # Handle Commodities via DhanHQ MCX
        if is_commodity:
            if self.dhan_active:
                sec_id = self.get_dhan_mcx_near_month(ticker_symbol)
                if sec_id:
                    price = self.get_dhan_current_price(sec_id, exchange_segment="MCX_COMM", instrument_type="FUTCOM")
                    if price:
                        return price
            # Fallback to yfinance if Dhan fails or MCX is inactive
            ticker_symbol = yf_symbol
            
        # Handle Stocks via DhanHQ NSE
        elif self.dhan_active and '.NS' in ticker_symbol:
            sec_id = self.get_dhan_security_id(ticker_symbol)
            if sec_id:
                price = self.get_dhan_current_price(sec_id)
                if price:
                    return price
        
        # Fallback to yfinance for Stocks
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                price = hist['Close'].iloc[-1]
                return price * fallback_multiplier
            return None
        except Exception as e:
            print(f"Error fetching current price for {ticker_symbol} via yfinance: {e}")
            return None

    def fetch_previous_state(self):
        try:
            headers = {
                'apikey': self.supabase_key,
                'Authorization': f'Bearer {self.supabase_key}'
            }
            response = requests.get(f"{self.supabase_url}?select=symbol,alert_status,last_alert_date,last_alert_msg", headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                return {item['symbol']: item for item in data}
        except Exception as e:
            print(f"Error fetching previous state: {e}")
        return {}

    def evaluate_levels(self, name, symbol, levels, current_price, previous_state):
        alerts = []
        alert_status = None
        
        today_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
        last_alert_date = previous_state.get(symbol, {}).get('last_alert_date')
        last_alert_msg = previous_state.get(symbol, {}).get('last_alert_msg')
        
        if levels is None or current_price is None:
            print(f"Could not fetch data for {name}. Skipping.")
            return alerts, alert_status, last_alert_date, last_alert_msg
            
        print(f"Current Price: ₹{current_price:.2f}")
        print(f"Levels -> R2: ₹{levels['R2']:.2f}, R1: ₹{levels['R1']:.2f}, P: ₹{levels['Pivot']:.2f}, S1: ₹{levels['S1']:.2f}, S2: ₹{levels['S2']:.2f}")
        
        # Resistance and Support alerts have been removed per user request.

        # Multi-timeframe extremes evaluation (Highest priority first)
        high_alerts_config = [
            ('AllTimeHigh', 'All-Time High', 0.01),
            ('OneYearHigh', '1-Year High', 0.01),
            ('ThreeMonthHigh', '3-Month High', 0.01),
            ('TwoMonthHigh', '2-Month High', 0.01),
            ('MonthlyHigh', '1-Month High', 0.005)
        ]
        
        for key, name_str, threshold in high_alerts_config:
            level = levels.get(key)
            if level and abs(current_price - level) / level <= threshold:
                alert_msg = f"testing {name_str}"
                if not (last_alert_date == today_ist and last_alert_msg == alert_msg):
                    alerts.append({
                        'subject': f"🚀 Market Breakout: {name} {alert_msg}!",
                        'body': f"{name} ({symbol}) is {alert_msg} of ₹{level:.2f}.\n\nCurrent price: ₹{current_price:.2f}."
                    })
                    last_alert_date = today_ist
                    last_alert_msg = alert_msg
                alert_status = alert_msg
                break # Only alert the highest timeframe reached
                
        low_alerts_config = [
            ('AllTimeLow', 'All-Time Low', 0.01),
            ('OneYearLow', '1-Year Low', 0.01),
            ('ThreeMonthLow', '3-Month Low', 0.01),
            ('TwoMonthLow', '2-Month Low', 0.01),
            ('MonthlyLow', '1-Month Low', 0.005)
        ]
        
        for key, name_str, threshold in low_alerts_config:
            level = levels.get(key)
            if level and abs(current_price - level) / level <= threshold:
                alert_msg = f"testing {name_str}"
                if not (last_alert_date == today_ist and last_alert_msg == alert_msg):
                    alerts.append({
                        'subject': f"📉 Market Breakdown: {name} {alert_msg}!",
                        'body': f"{name} ({symbol}) is {alert_msg} of ₹{level:.2f}.\n\nCurrent price: ₹{current_price:.2f}."
                    })
                    last_alert_date = today_ist
                    last_alert_msg = alert_msg
                alert_status = alert_msg
                break # Only alert the highest timeframe reached

        return alerts, alert_status, last_alert_date, last_alert_msg

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
        
        print("Fetching previous alert states from Supabase...")
        previous_state = self.fetch_previous_state()
        
        # 1. Check Commodities
        for symbol, data in self.commodities.items():
            name = data['name']
            yf_sym = data.get('yf_symbol')
            
            # DYNAMIC UNIT CONVERSION (Global USD to MCX INR)
            # Gold: $ per Troy Oz -> ₹ per 10 grams (1 Troy Oz = 31.1035 grams)
            if symbol == "GOLD":
                conversion = self.usd_inr_rate * (10 / 31.1035)
            # Silver: $ per Troy Oz -> ₹ per 1 kg (1000 grams)
            elif symbol == "SILVER":
                conversion = self.usd_inr_rate * (1000 / 31.1035)
            # Copper: $ per lb -> ₹ per 1 kg (1 kg = 2.20462 lbs)
            elif symbol == "COPPER":
                conversion = self.usd_inr_rate * 2.20462
            # Aluminum: $ per metric ton -> ₹ per 1 kg (1 MT = 1000 kg)
            elif symbol == "ALUMINIUM":
                conversion = self.usd_inr_rate / 1000
            # Crude Oil (per barrel) & Natural Gas (per mmBtu) are 1:1 units
            else:
                conversion = self.usd_inr_rate
                
            # Add an approximate 12% premium for Indian Gold/Silver Customs Duty & GST
            if symbol in ["GOLD", "SILVER"]:
                conversion *= 1.12
            
            print(f"\nChecking Commodity {name} ({symbol}) via MCX API (or yfinance fallback)...")
            levels = self.get_support_resistance_levels(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
            current_price = self.get_current_price(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
            
            new_alerts, alert_status, last_alert_date, last_alert_msg = self.evaluate_levels(name, symbol, levels, current_price, previous_state)
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
                    "alert_status": alert_status,
                    "last_alert_date": last_alert_date,
                    "last_alert_msg": last_alert_msg,
                    "intrinsic_value": None,
                    "last_updated": datetime.utcnow().isoformat()
                })
            
        # 2. Check Stocks
        for symbol, data in self.stocks.items():
            name = data['name']
            
            print(f"\nChecking Stock {name} ({symbol})...")
            yf_sym = data.get('yf_symbol', symbol)
            levels = self.get_support_resistance_levels(symbol, is_commodity=False, yf_symbol=yf_sym)
            current_price = self.get_current_price(symbol, is_commodity=False, yf_symbol=yf_sym)
            intrinsic_val = self.get_intrinsic_value(yf_sym)
            
            new_alerts, alert_status, last_alert_date, last_alert_msg = self.evaluate_levels(name, symbol, levels, current_price, previous_state)
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
                    "alert_status": alert_status,
                    "last_alert_date": last_alert_date,
                    "last_alert_msg": last_alert_msg,
                    "intrinsic_value": intrinsic_val,
                    "last_updated": datetime.utcnow().isoformat()
                })

        if market_data_payload:
            self.sync_to_supabase(market_data_payload)

        return alerts
