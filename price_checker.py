import os
import yfinance as yf
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz
import json
import math
import numpy as np
from scipy.signal import argrelextrema
import ta
from SmartApi import SmartConnect
import pyotp
import urllib.request
import json as json_lib

class PriceChecker:
    def __init__(self, config):
        self.commodities = config.get('commodities', {})
        self.stocks = config.get('stocks', {})
        self.derivatives = config.get('derivatives', {})
        self.usd_inr_rate = self.get_usd_inr_rate()
        
        # Supabase config
        self.supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/market_data'
        self.supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'

        # AngelOne config
        self.angel_api_key = os.environ.get('ANGEL_API_KEY')
        self.angel_client_id = os.environ.get('ANGEL_CLIENT_ID')
        self.angel_password = os.environ.get('ANGEL_PASSWORD')
        self.angel_totp_secret = os.environ.get('ANGEL_TOTP_SECRET')
        self.angel_active = False
        self.angel = None
        self.angel_master = None
        
        if self.angel_api_key and self.angel_client_id and self.angel_password and self.angel_totp_secret:
            try:
                self.angel = SmartConnect(api_key=self.angel_api_key)
                totp = pyotp.TOTP(self.angel_totp_secret).now()
                data = self.angel.generateSession(self.angel_client_id, self.angel_password, totp)
                if data['status']:
                    self.angel_active = True
                    print("AngelOne API Initialized Successfully.")
                    self.load_angel_master()
                else:
                    print(f"Failed to initialize AngelOne: {data['message']}")
                    self.angel_active = False
            except Exception as e:
                print(f"Failed to initialize AngelOne Exception: {e}")
                self.angel_active = False

    def load_angel_master(self):
        try:
            print("Downloading AngelOne Instrument Master...")
            # We fetch the JSON from AngelOne
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            response = urllib.request.urlopen(url)
            data = json_lib.loads(response.read())
            self.angel_master = pd.DataFrame(data)
            # Optimize memory and lookups
            self.angel_master['expiry'] = pd.to_datetime(self.angel_master['expiry'], format="%d%b%Y", errors='coerce')
            print("AngelOne Instrument Master loaded.")
        except Exception as e:
            print(f"Error loading AngelOne Master JSON: {e}")
            self.angel_active = False

    def get_angel_token(self, symbol, exchange="NSE"):
        if not self.angel_active or self.angel_master is None:
            return None
        
        clean_symbol = symbol.replace('.NS', '').replace('.BO', '')
        
        instrument = self.angel_master[
            (self.angel_master['exch_seg'] == exchange) & 
            ((self.angel_master['symbol'] == clean_symbol + '-EQ') | (self.angel_master['symbol'] == clean_symbol))
        ]
        
        if not instrument.empty:
            return str(instrument['token'].values[0])
        return None

    def get_angel_mcx_near_month(self, base_symbol):
        if not self.angel_active or self.angel_master is None:
            print(f"Skipping Angel MCX for {base_symbol}: active={self.angel_active}")
            return None
            
        try:
            mcx = self.angel_master[
                (self.angel_master['exch_seg'] == 'MCX') & 
                (self.angel_master['name'] == base_symbol) &
                (self.angel_master['instrumenttype'].isin(['FUTCOM', 'FUTENR', 'FUTBULL', 'FUTIDX', 'FUTMCX']))
            ].copy()
            
            if mcx.empty:
                print(f"No active MCX futures found in Angel master for {base_symbol}!")
                return None
            
            now = datetime.now()
            mcx = mcx[mcx['expiry'] >= now].sort_values('expiry')
            
            if not mcx.empty:
                return str(mcx.iloc[0]['token'])
            return None
        except Exception as e:
            print(f"Error fetching MCX near month for {base_symbol}: {e}")
            return None

    def get_usd_inr_rate(self):
        try:
            ticker = yf.Ticker('INR=X')
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                rate = hist['Close'].iloc[-1]
                return rate
        except Exception as e:
            print(f"Failed to fetch USD/INR rate: {e}")
        
        print("Using fallback USD/INR exchange rate of 83.50")
        return 83.50

    def get_angel_derivatives(self, base_symbol, spot_price):
        if not self.angel_active or self.angel_master is None:
            return []
            
        derivatives_to_track = []
        now = datetime.now()
        
        # 1. Near-Month Future
        futures = self.angel_master[
            (self.angel_master['exch_seg'] == 'NFO') & 
            (self.angel_master['name'] == base_symbol) &
            (self.angel_master['instrumenttype'].isin(['FUTIDX', 'FUTSTK']))
        ].copy()
        
        if not futures.empty:
            futures = futures[futures['expiry'] >= now].sort_values('expiry')
            if not futures.empty:
                near_fut = futures.iloc[0]
                derivatives_to_track.append({
                    'symbol': near_fut['symbol'],
                    'security_id': str(near_fut['token']),
                    'type': 'Future',
                    'exchange_segment': 'NFO',
                    'instrument_type': 'FUTIDX'
                })
            
        # 2. Near-Month ATM Options (CE & PE)
        options = self.angel_master[
            (self.angel_master['exch_seg'] == 'NFO') & 
            (self.angel_master['name'] == base_symbol) &
            (self.angel_master['instrumenttype'].isin(['OPTIDX', 'OPTSTK']))
        ].copy()
        
        if not options.empty and spot_price:
            options = options[options['expiry'] >= now].sort_values('expiry')
            if not options.empty:
                nearest_expiry = options.iloc[0]['expiry']
                near_options = options[options['expiry'] == nearest_expiry].copy()
                
                near_options['strike_val'] = pd.to_numeric(near_options['strike'], errors='coerce') / 100
                near_options['STRIKE_DIFF'] = abs(near_options['strike_val'] - spot_price)
                
                atm_strike = near_options.loc[near_options['STRIKE_DIFF'].idxmin()]['strike_val']
                
                atm_options = near_options[near_options['strike_val'] == atm_strike]
                for _, opt in atm_options.iterrows():
                    opt_type = 'CE' if opt['symbol'].endswith('CE') else 'PE'
                    derivatives_to_track.append({
                        'symbol': opt['symbol'],
                        'security_id': str(opt['token']),
                        'type': f'Option ({opt_type})',
                        'exchange_segment': 'NFO',
                        'instrument_type': 'OPTIDX',
                        'strike': float(atm_strike)
                    })
                    
        return derivatives_to_track

    def get_angel_levels(self, token, exchange_segment="NSE"):
        try:
            # For levels we need historic data for the previous day
            now = datetime.now()
            past = now - timedelta(days=5)
            
            historicParam = {
                "exchange": exchange_segment,
                "symboltoken": str(token),
                "interval": "ONE_DAY",
                "fromdate": past.strftime("%Y-%m-%d %H:%M"),
                "todate": now.strftime("%Y-%m-%d %H:%M")
            }
            
            data = self.angel.getCandleData(historicParam)
            
            if data and data.get('status') and data.get('data'):
                candles = data['data']
                if len(candles) >= 1:
                    last_candle = candles[-1]
                    if len(candles) >= 2 and now.hour < 9 or (now.hour == 9 and now.minute < 15):
                        last_candle = candles[-2]
                        
                    high = last_candle[2]
                    low = last_candle[3]
                    close = last_candle[4]
                    
                    pivot = (high + low + close) / 3
                    r1 = (2 * pivot) - low
                    s1 = (2 * pivot) - high
                    r2 = pivot + (high - low)
                    s2 = pivot - (high - low)
                    
                    return {
                        'R2': round(r2, 2),
                        'R1': round(r1, 2),
                        'Pivot': round(pivot, 2),
                        'S1': round(s1, 2),
                        'S2': round(s2, 2)
                    }
            return None
        except Exception as e:
            print(f"Error fetching historical data from AngelOne for {token}: {e}")
            return None

    def get_angel_current_price(self, token, exchange_segment="NSE"):
        try:
            symbol = "DUMMY"
            if self.angel_master is not None:
                instrument = self.angel_master[self.angel_master['token'] == str(token)]
                if not instrument.empty:
                    symbol = instrument['symbol'].values[0]
                    
            data = self.angel.ltpData(exchange_segment, symbol, str(token))
            if data and data.get('status') and data.get('data'):
                return data['data']['ltp']
            return None
        except Exception as e:
            print(f"Error fetching live price from AngelOne for {token}: {e}")
            return None

    def get_angel_oi(self, token, exchange_segment="NFO"):
        try:
            if not hasattr(self.angel, 'marketData'):
                return 0 # Fallback if library version doesn't support marketData
            params = {
                "mode": "FULL",
                "exchangeTokens": {
                    exchange_segment: [str(token)]
                }
            }
            data = self.angel.marketData("FULL", {exchange_segment: [str(token)]})
            if data and data.get('status') and data.get('data'):
                fetched = data['data'].get('fetched', [])
                if fetched:
                    return fetched[0].get('opnInterest', 0)
            return 0
        except Exception as e:
            print(f"Error fetching OI from AngelOne for {token}: {e}")
            return 0

    def get_support_resistance_levels(self, ticker_symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=None, current_price=None):
        levels = None
        
        # 1. Try to get Pivot/R/S from AngelOne
        if is_commodity:
            print(f"Attempting AngelOne for commodity {ticker_symbol}...")
            if self.angel_active:
                sec_id = self.get_angel_mcx_near_month(ticker_symbol)
                if sec_id:
                    print(f"Found SecID for {ticker_symbol}: {sec_id}. Fetching levels...")
                    levels = self.get_angel_levels(sec_id, exchange_segment="MCX", )
        else:
            print(f"Attempting AngelOne for stock {ticker_symbol}...")
            if self.angel_active and '.NS' in ticker_symbol:
                sec_id = self.get_angel_token(ticker_symbol)
                if sec_id:
                    print(f"Found SecID for {ticker_symbol}: {sec_id}. Fetching levels...")
                    levels = self.get_angel_levels(sec_id)

        # 2. Fetch all-time yfinance data for multi-timeframe extremes and fallback Pivot/R/S
        ticker_symbol = yf_symbol if yf_symbol else ticker_symbol
        try:
            print(f"Fetching yfinance period='max' for extremes for {ticker_symbol}...")
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="max")
            
            if len(hist) < 2:
                print(f"Not enough historical data from yfinance for {ticker_symbol}.")
                return levels # Return dhan levels if yfinance fails
                
            # If AngelOne failed, calculate Pivot/R/S from yfinance
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
            hist_1w = hist[hist.index >= (now - timedelta(days=7))]
            hist_1m = hist[hist.index >= (now - timedelta(days=30))]
            hist_2m = hist[hist.index >= (now - timedelta(days=60))]
            hist_3m = hist[hist.index >= (now - timedelta(days=90))]
            hist_1y = hist[hist.index >= (now - timedelta(days=365))]
            
            levels['WeeklyHigh'] = hist_1w['High'].max() * fallback_multiplier if not hist_1w.empty else None
            levels['WeeklyLow'] = hist_1w['Low'].min() * fallback_multiplier if not hist_1w.empty else None
            
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
            
            # Calculate EMAs for Trend Signals
            if len(hist) >= 200:
                levels['EMA_50'] = hist['Close'].ewm(span=50, adjust=False).mean().iloc[-1] * fallback_multiplier
                levels['EMA_200'] = hist['Close'].ewm(span=200, adjust=False).mean().iloc[-1] * fallback_multiplier
            else:
                levels['EMA_50'] = None
                levels['EMA_200'] = None

            # Calculate ATR, Bollinger Bands, and Volume Confirmation
            if len(hist) >= 20:
                levels['ATR_14'] = ta.volatility.average_true_range(hist['High'], hist['Low'], hist['Close'], window=14).iloc[-1] * fallback_multiplier
                levels['BB_Upper'] = ta.volatility.bollinger_hband(hist['Close'], window=20, window_dev=2).iloc[-1] * fallback_multiplier
                levels['BB_Lower'] = ta.volatility.bollinger_lband(hist['Close'], window=20, window_dev=2).iloc[-1] * fallback_multiplier
                levels['Vol_20_MA'] = hist['Volume'].rolling(window=20).mean().iloc[-2] # previous day MA
                levels['Vol_Current'] = hist['Volume'].iloc[-1]
            else:
                levels['ATR_14'] = None
                levels['BB_Upper'] = None
                levels['BB_Lower'] = None
                levels['Vol_20_MA'] = None
                levels['Vol_Current'] = None

            
        except Exception as e:
            print(f"Error fetching historical extremes from yfinance for {ticker_symbol}: {e}")
            
        # Dynamically frame S1/S2 and R1/R2 around the current price
        if current_price and levels:
            pivot_levels = sorted([
                v for k, v in levels.items()
                if k in ['S2', 'S1', 'Pivot', 'R1', 'R2'] and v is not None
            ])
            if pivot_levels:
                below = [v for v in pivot_levels if v < current_price]
                above = [v for v in pivot_levels if v > current_price]
                
                if below:
                    levels['S1'] = below[-1]
                    levels['S2'] = below[-2] if len(below) > 1 else below[-1]
                else:
                    levels['S1'] = pivot_levels[0]
                    levels['S2'] = pivot_levels[0]
                    
                if above:
                    levels['R1'] = above[0]
                    levels['R2'] = above[1] if len(above) > 1 else above[0]
                else:
                    levels['R1'] = pivot_levels[-1]
                    levels['R2'] = pivot_levels[-1]
            
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
        # We now use yfinance for the base spot price so spot and futures are separated
        if is_commodity:
            ticker_symbol = yf_symbol
            
        # Handle Stocks via AngelOne NSE
        elif self.angel_active and '.NS' in ticker_symbol:
            sec_id = self.get_angel_token(ticker_symbol)
            if sec_id:
                price = self.get_angel_current_price(sec_id)
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
            response = requests.get(f"{self.supabase_url}?select=symbol,alert_status,last_alert_date,last_alert_msg,signal", headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                return {item['symbol']: item for item in data}
            else:
                print(f"Failed to fetch previous state. Status: {response.status_code} {response.text}")
                return None
        except Exception as e:
            print(f"Error fetching previous state: {e}")
            return None

    def evaluate_levels(self, name, symbol, levels, current_price, previous_state, is_commodity=False):
        alerts = []
        alert_status = None
        
        today_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
        last_alert_date = previous_state.get(symbol, {}).get('last_alert_date')
        last_alert_msg = previous_state.get(symbol, {}).get('last_alert_msg')
        previous_signal = previous_state.get(symbol, {}).get('signal', 'NEUTRAL')
        
        if levels is None or current_price is None:
            print(f"Could not fetch data for {name}. Skipping.")
            return alerts, alert_status, last_alert_date, last_alert_msg, previous_signal
            
        print(f"Current Price: ₹{current_price:.2f}")
        print(f"Levels -> R2: ₹{levels.get('R2', 0):.2f}, R1: ₹{levels.get('R1', 0):.2f}, P: ₹{levels.get('Pivot', 0):.2f}, S1: ₹{levels.get('S1', 0):.2f}, S2: ₹{levels.get('S2', 0):.2f}")
        
        # Check Trend Signals (EMA 50 vs EMA 200 vs Current Price)
        current_signal = "NEUTRAL"
        ema_50 = levels.get('EMA_50')
        ema_200 = levels.get('EMA_200')
        
        if ema_50 and ema_200:
            if ema_50 > ema_200:
                if current_price > ema_50:
                    current_signal = "STRONG BUY"
                else:
                    current_signal = "BUY (PULLBACK)"
            elif ema_50 < ema_200:
                if current_price < ema_50:
                    current_signal = "STRONG SELL"
                else:
                    current_signal = "SELL (RALLY)"
                
        # Only send an email alert if the signal changes to a STRONG BUY/SELL
        if current_signal != previous_signal and current_signal in ["STRONG BUY", "STRONG SELL"]:
            yf_symbol_map = {
                "GOLD": "GC=F", "SILVER": "SI=F", "CRUDEOIL": "CL=F", 
                "NATURALGAS": "NG=F", "COPPER": "HG=F", "ALUMINIUM": "ALI=F"
            }
            yf_sym = yf_symbol_map.get(symbol, symbol)
            chart_url = f"https://finance.yahoo.com/quote/{yf_sym}"
            alerts.append({
                'subject': f"🔔 Trading Signal: {name} is now a {current_signal}!",
                'body': f"{name} ({symbol}) has crossed its Moving Averages and generated a {current_signal} signal.\n\nEMA 50: ₹{ema_50:.2f}\nEMA 200: ₹{ema_200:.2f}\nCurrent Price: ₹{current_price:.2f}\n\nView Chart: {chart_url}"
            })
            
        # Resistance and Support alerts have been removed per user request.

        # Volume and Volatility Checks
        vol_ma = levels.get('Vol_20_MA')
        vol_curr = levels.get('Vol_Current')
        is_high_volume = (vol_curr and vol_ma and vol_curr > 1.5 * vol_ma)
        volume_msg = " [High Volume Confirmed]" if is_high_volume else ""

        bb_upper = levels.get('BB_Upper')
        bb_lower = levels.get('BB_Lower')
        atr = levels.get('ATR_14')

        # Multi-timeframe extremes evaluation (Highest priority first)
        if is_commodity:
            high_alerts_config = [
                ('Strong_R2', 'Strong Algorithmic Resistance (R2)', 0.01),
                ('Strong_R1', 'Strong Algorithmic Resistance (R1)', 0.005),
                ('ThreeMonthHigh', '3-Month High', 0.01),
                ('MonthlyHigh', '1-Month High', 0.005),
                ('WeeklyHigh', '1-Week High', 0.005)
            ]
        else:
            high_alerts_config = [
                ('Strong_R2', 'Strong Algorithmic Resistance (R2)', 0.01),
                ('Strong_R1', 'Strong Algorithmic Resistance (R1)', 0.005),
                ('ThreeMonthHigh', '3-Month High', 0.01)
            ]
            
        yf_symbol_map = {
            "GOLD": "GC=F",
            "SILVER": "SI=F",
            "CRUDEOIL": "CL=F",
            "NATURALGAS": "NG=F",
            "COPPER": "HG=F",
            "ALUMINIUM": "ALI=F"
        }
        yf_sym = yf_symbol_map.get(symbol, symbol)
        chart_url = f"https://finance.yahoo.com/quote/{yf_sym}"

        # Check Bollinger Band Breakout
        if bb_upper and current_price > bb_upper and is_high_volume:
            if last_alert_date != today_ist:
                alerts.append({
                    'subject': f"🔥 Volatility Breakout: {name} pierced Upper Bollinger Band!",
                    'body': f"{name} ({symbol}) has broken above its Upper Bollinger Band (₹{bb_upper:.2f}) with high volume.\nATR is ₹{atr:.2f}.\n\nCurrent price: ₹{current_price:.2f}.\n\nView Chart: {chart_url}"
                })
                last_alert_date = today_ist
                last_alert_msg = "Upper BB Breakout"
            alert_status = "Upper BB Breakout"

        for key, name_str, threshold in high_alerts_config:
            level = levels.get(key)
            if level and abs(level) > 0 and abs(current_price - level) / abs(level) <= threshold:
                alert_msg = f"testing {name_str}"
                # Require volume confirmation for breakouts (except R1/R2 which are static pivots)
                if ("High" in key) and not is_high_volume:
                    continue

                if last_alert_date != today_ist:
                    action_text = "Action Required: This asset is testing a major resistance level. If you are holding long positions, consider booking partial profits. If it breaks and sustains above this level, it could signal a strong bullish continuation."
                    alerts.append({
                        'subject': f"🚀 Market Breakout: {name} {alert_msg}!{volume_msg}",
                        'body': f"{name} ({symbol}) is {alert_msg} of ₹{level:.2f}.{volume_msg}\n\nCurrent price: ₹{current_price:.2f}.\n\n{action_text}\n\nView Chart: {chart_url}"
                    })
                    last_alert_date = today_ist
                    last_alert_msg = alert_msg
                
                alert_status = alert_msg
                break # Only alert the highest timeframe reached
                
        # Check Lower Bollinger Band Breakdown
        if bb_lower and current_price < bb_lower and is_high_volume:
            if last_alert_date != today_ist:
                action_text = "Action Required: The price has aggressively pierced the Lower Bollinger Band on high volume. This often indicates extreme oversold conditions. Look for a potential mean-reversion bounce, but avoid catching a falling knife until price stabilizes."
                alerts.append({
                    'subject': f"⚠️ Volatility Breakdown: {name} pierced Lower Bollinger Band!",
                    'body': f"{name} ({symbol}) has broken below its Lower Bollinger Band (₹{bb_lower:.2f}) with high volume.\nATR is ₹{atr:.2f}.\n\nCurrent price: ₹{current_price:.2f}.\n\n{action_text}\n\nView Chart: {chart_url}"
                })
                last_alert_date = today_ist
                last_alert_msg = "Lower BB Breakout"
            alert_status = "Lower BB Breakout"
            
        if is_commodity:
            low_alerts_config = [
                ('Strong_S2', 'Strong Algorithmic Support (S2)', 0.01),
                ('Strong_S1', 'Strong Algorithmic Support (S1)', 0.005),
                ('ThreeMonthLow', '3-Month Low', 0.01),
                ('MonthlyLow', '1-Month Low', 0.005),
                ('WeeklyLow', '1-Week Low', 0.005)
            ]
        else:
            low_alerts_config = [
                ('Strong_S2', 'Strong Algorithmic Support (S2)', 0.01),
                ('Strong_S1', 'Strong Algorithmic Support (S1)', 0.005),
                ('ThreeMonthLow', '3-Month Low', 0.01)
            ]
        
        for key, name_str, threshold in low_alerts_config:
            level = levels.get(key)
            if level and abs(level) > 0 and abs(current_price - level) / abs(level) <= threshold:
                alert_msg = f"testing {name_str}"
                
                if ("Low" in key) and not is_high_volume:
                    continue
                    
                if last_alert_date != today_ist:
                    action_text = "Action Required: This asset is testing a major support level. This is a potential buying opportunity if the price respects the support and bounces. Watch for reversal candlestick patterns before entering a new long position."
                    alerts.append({
                        'subject': f"📉 Market Breakdown: {name} {alert_msg}!{volume_msg}",
                        'body': f"{name} ({symbol}) is {alert_msg} of ₹{level:.2f}.{volume_msg}\n\nCurrent price: ₹{current_price:.2f}.\n\n{action_text}\n\nView Chart: {chart_url}"
                    })
                    last_alert_date = today_ist
                    last_alert_msg = alert_msg
                
                alert_status = alert_msg
                break # Only alert the highest timeframe reached

        return alerts, alert_status, last_alert_date, last_alert_msg, current_signal

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
        
        # Pull previous alert state from Supabase to prevent duplicate emails
        previous_state = self.fetch_previous_state()
        if previous_state is None:
            print("Failed to fetch previous state from Supabase. Aborting this run to prevent spamming duplicate alerts.")
            return alerts
            
        market_data_payload = []
        
        # 1. Check Commodities
        for symbol, data in self.commodities.items():
            name = data['name']
            yf_sym = data.get('yf_symbol')
            
            # DYNAMIC UNIT CONVERSION (Global USD to MCX INR)
            mcx_multiplier = data.get('mcx_multiplier')
            
            if mcx_multiplier is not None:
                conversion = self.usd_inr_rate * mcx_multiplier
            else:
                # Fallback logic if multiplier isn't defined in config
                if symbol == "GOLD":
                    conversion = self.usd_inr_rate * (10 / 31.1035) * 1.12
                elif symbol == "SILVER":
                    conversion = self.usd_inr_rate * (1000 / 31.1035) * 1.12
                elif symbol == "COPPER":
                    conversion = self.usd_inr_rate * 2.20462
                elif symbol == "ALUMINIUM":
                    conversion = self.usd_inr_rate / 1000
                else:
                    conversion = self.usd_inr_rate
            
            print(f"\nChecking Commodity {name} ({symbol}) via MCX API (or yfinance fallback)...")
            current_price = self.get_current_price(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
            
            # Fetch Commodity Future explicitly early
            fut_price = None
            if self.angel_active:
                sec_id = self.get_angel_mcx_near_month(symbol)
                if sec_id:
                    fut_price = self.get_angel_current_price(sec_id, exchange_segment="MCX", )
                    
            # Fallback for spot price if yfinance fails
            if current_price is None and fut_price is not None:
                current_price = fut_price
            
            levels = self.get_support_resistance_levels(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym, current_price=current_price)
            
            new_alerts, alert_status, last_alert_date, last_alert_msg, current_signal = self.evaluate_levels(name, symbol, levels, current_price, previous_state, is_commodity=True)
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
                    "signal": current_signal,
                    "intrinsic_value": None,
                    "last_updated": datetime.utcnow().isoformat()
                })
                        
                # Fallback to internet (yf converted price) if AngelOne is down
                if not fut_price:
                    fut_price = current_price
                    
                if fut_price:
                    market_data_payload.append({
                        "symbol": f"{symbol}-FUT",
                        "name": f"{name} Future",
                        "asset_type": "Commodity Future",
                        "current_price": round(fut_price, 2),
                        "r1": None, "r2": None, "s1": None, "s2": None, "pivot": None,
                        "alert_status": None,
                        "last_alert_date": None,
                        "last_alert_msg": None,
                        "signal": None,
                        "intrinsic_value": None,
                        "last_updated": datetime.utcnow().isoformat()
                    })
            
        # 2. Check Stocks
        for symbol, data in self.stocks.items():
            name = data['name']
            
            print(f"\nChecking Stock {name} ({symbol})...")
            yf_sym = data.get('yf_symbol', symbol)
            current_price = self.get_current_price(symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=yf_sym)
            levels = self.get_support_resistance_levels(symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=yf_sym, current_price=current_price)
            intrinsic_val = self.get_intrinsic_value(yf_sym)
            
            new_alerts, alert_status, last_alert_date, last_alert_msg, current_signal = self.evaluate_levels(name, symbol, levels, current_price, previous_state, is_commodity=False)
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
                    "signal": current_signal,
                    "intrinsic_value": intrinsic_val,
                    "last_updated": datetime.utcnow().isoformat()
                })
                
                # Append Stock Future internet fallback
                market_data_payload.append({
                    "symbol": f"{symbol}-FUT",
                    "name": f"{name} Future",
                    "asset_type": "Stock Future",
                    "current_price": round(current_price, 2),
                    "r1": None, "r2": None, "s1": None, "s2": None, "pivot": None,
                    "alert_status": None,
                    "last_alert_date": None,
                    "last_alert_msg": None,
                    "signal": None,
                    "intrinsic_value": None,
                    "last_updated": datetime.utcnow().isoformat()
                })

        # 3. Check Derivatives (Futures & Options)
        if hasattr(self, 'derivatives'):
            for base_symbol, config_data in self.derivatives.items():
                print(f"\nChecking Derivatives for {base_symbol}...")
                # Get spot price for ATM option calculation
                yf_spot_sym = '^NSEI' if base_symbol == 'NIFTY' else '^NSEBANK' if base_symbol == 'BANKNIFTY' else None
                spot_price = None
                if yf_spot_sym:
                    try:
                        ticker = yf.Ticker(yf_spot_sym)
                        hist = ticker.history(period="1d")
                        if not hist.empty:
                            spot_price = hist['Close'].iloc[-1]
                    except Exception as e:
                        print(f"Error fetching spot price for {base_symbol}: {e}")
                
                if self.angel_active:
                    derivs = self.get_angel_derivatives(base_symbol, spot_price)
                    total_ce_oi = 0
                    total_pe_oi = 0
                    derivs_with_oi = []
                    
                    for deriv in derivs:
                        symbol = deriv['symbol']
                        print(f"Fetching price for {symbol}...")
                        current_price = self.get_angel_current_price(deriv['security_id'], exchange_segment=deriv['exchange_segment'], )
                        
                        oi = 0
                        if 'Option' in deriv['type']:
                            oi = self.get_angel_oi(deriv['security_id'], exchange_segment=deriv['exchange_segment'])
                            if 'CE' in deriv['type']:
                                total_ce_oi += oi
                            elif 'PE' in deriv['type']:
                                total_pe_oi += oi
                                
                        if current_price:
                            derivs_with_oi.append({
                                'deriv': deriv,
                                'current_price': current_price,
                                'oi': oi
                            })
                            
                    pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else None
                    
                    for item in derivs_with_oi:
                        deriv = item['deriv']
                        market_data_payload.append({
                            "symbol": deriv['symbol'],
                            "name": deriv['symbol'],
                            "asset_type": deriv['type'],
                            "current_price": round(item['current_price'], 2),
                            "r1": None, "r2": None, "s1": None, "s2": None, "pivot": None,
                            "alert_status": None,
                            "last_alert_date": None,
                            "last_alert_msg": None,
                            "signal": None,
                            "intrinsic_value": None,
                            "open_interest": item['oi'],
                            "pcr": round(pcr, 2) if pcr else None,
                            "last_updated": datetime.utcnow().isoformat()
                        })
                else:
                    # Internet fallback for NIFTY/BANKNIFTY futures using spot price
                    if spot_price and config_data.get('track_futures'):
                        market_data_payload.append({
                            "symbol": f"{base_symbol}-FUT",
                            "name": f"{config_data.get('name', base_symbol)} Future",
                            "asset_type": "Future",
                            "current_price": round(spot_price, 2),
                            "r1": None, "r2": None, "s1": None, "s2": None, "pivot": None,
                            "alert_status": None,
                            "last_alert_date": None,
                            "last_alert_msg": None,
                            "signal": None,
                            "intrinsic_value": None,
                            "last_updated": datetime.utcnow().isoformat()
                        })

        if market_data_payload:
            self.sync_to_supabase(market_data_payload)
            self.process_limit_orders(market_data_payload)

        return alerts
        
    def process_limit_orders(self, market_data_payload):
        print("Checking pending limit orders...")
        try:
            # 1. Fetch pending orders
            url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/pending_orders?status=in.(PENDING,PENDING_STOP)&select=*'
            headers = {
                'apikey': self.supabase_key,
                'Authorization': f'Bearer {self.supabase_key}',
                'Content-Type': 'application/json'
            }
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                print(f"Failed to fetch pending orders: {resp.text}")
                return
                
            orders = resp.json()
            if not orders:
                print("No pending orders found.")
                return
                
            # Create a lookup dictionary for fast price checking
            prices = {item['symbol']: item['current_price'] for item in market_data_payload}
            
            for order in orders:
                symbol = order['symbol']
                limit_price = float(order['limit_price'])
                qty = order['quantity']
                user_id = order['user_id']
                tx_type = order['transaction_type']
                status = order['status']
                
                if symbol not in prices:
                    continue
                    
                current_price = prices[symbol]
                
                should_execute = False
                
                if status == 'PENDING': # Target Limit
                    if tx_type == 'BUY' and current_price <= limit_price:
                        should_execute = True
                    elif tx_type == 'SELL' and current_price >= limit_price:
                        should_execute = True
                elif status == 'PENDING_STOP': # Stop Loss
                    if tx_type == 'BUY' and current_price >= limit_price:
                        should_execute = True
                    elif tx_type == 'SELL' and current_price <= limit_price:
                        should_execute = True
                    
                if should_execute:
                    print(f"Executing {status} {tx_type} for {symbol} at {limit_price} (Current: {current_price})")
                    
                    # 1. Update order status to EXECUTED
                    requests.patch(f"https://cohupetijvykzmeliubg.supabase.co/rest/v1/pending_orders?id=eq.{order['id']}", 
                                   headers=headers, json={"status": "EXECUTED"})
                    
                    # 2. Insert into transactions
                    tx_payload = {
                        "user_id": user_id,
                        "symbol": symbol,
                        "transaction_type": tx_type,
                        "quantity": qty,
                        "price": limit_price
                    }
                    requests.post("https://cohupetijvykzmeliubg.supabase.co/rest/v1/transactions", 
                                  headers=headers, json=tx_payload)
                                  
                    # 3. Update profile balance
                    prof_resp = requests.get(f"https://cohupetijvykzmeliubg.supabase.co/rest/v1/profiles?id=eq.{user_id}&select=balance", headers=headers)
                    if prof_resp.status_code == 200 and prof_resp.json():
                        current_bal = float(prof_resp.json()[0]['balance'])
                        total_val = qty * limit_price
                        new_bal = current_bal - total_val if tx_type == 'BUY' else current_bal + total_val
                        requests.patch(f"https://cohupetijvykzmeliubg.supabase.co/rest/v1/profiles?id=eq.{user_id}", 
                                       headers=headers, json={"balance": new_bal})
                                       
        except Exception as e:
            print(f"Error processing limit orders: {e}")
