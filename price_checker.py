import os
import time
import concurrent.futures
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
        
        # In-memory caches to eliminate redundant passes
        self.cached_rsi = {}
        self.intrinsic_cache = self._load_intrinsic_cache()
        self._last_circuits_data_time = 0
        self._cached_circuits_data = None
        self._last_history_data_time = 0
        
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

    def _load_intrinsic_cache(self):
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'intrinsic_cache.json')
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error reading intrinsic cache: {e}")
        return {}

    def _save_intrinsic_cache(self):
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'intrinsic_cache.json')
        try:
            with open(cache_path, 'w') as f:
                json.dump(self.intrinsic_cache, f, indent=2)
        except Exception as e:
            print(f"Error saving intrinsic cache: {e}")

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

    def get_support_resistance_levels(self, ticker_symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=None, current_price=None, tv_quote=None):
        levels = None
        
        # 1. Try to get Pivot/R/S from live internet MCX scanner quote if available
        if is_commodity and tv_quote and tv_quote.get('prev_close') is not None:
            prev_close = float(tv_quote['prev_close'])
            high = float(tv_quote.get('high') or prev_close)
            low = float(tv_quote.get('low') or prev_close)
            p = (high + low + prev_close) / 3
            levels = {
                "Pivot": p,
                "R1": (p * 2) - low,
                "R2": p + (high - low),
                "S1": (p * 2) - high,
                "S2": p - (high - low),
                "Prev_Close": prev_close
            }
        
        # 2. Try to get Pivot/R/S from AngelOne
        if not levels:
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

        # 3. Fetch yfinance data (period='2y') for multi-timeframe extremes and fallback Pivot/R/S
        ticker_symbol = yf_symbol if yf_symbol else ticker_symbol
        try:
            ticker = yf.Ticker(ticker_symbol)
            try:
                hist = ticker.history(period="2y")
            except Exception:
                hist = pd.DataFrame()
                
            if hist.empty or len(hist) < 2:
                try:
                    hist_fallback = ticker.history(period="5d")
                    if not hist_fallback.empty:
                        hist = hist_fallback
                except Exception:
                    pass
            
            if len(hist) < 1:
                print(f"Not enough historical data from yfinance for {ticker_symbol}.")
                return levels # Return levels if yfinance fails
                
            # If previous steps failed, calculate Pivot/R/S from yfinance
            if not levels:
                idx = -2 if len(hist) >= 2 else -1
                high = hist['High'].iloc[idx] * fallback_multiplier
                low = hist['Low'].iloc[idx] * fallback_multiplier
                close = hist['Close'].iloc[idx] * fallback_multiplier
                
                p = (high + low + close) / 3
                levels = {
                    "Pivot": p,
                    "R1": (p * 2) - low,
                    "R2": p + (high - low),
                    "S1": (p * 2) - high,
                    "S2": p - (high - low),
                    "Prev_Close": close
                }
            elif 'Prev_Close' not in levels:
                idx = -2 if len(hist) >= 2 else -1
                levels['Prev_Close'] = hist['Close'].iloc[idx] * fallback_multiplier
                
            # Record current price directly from latest close
            levels['Current_Price'] = hist['Close'].iloc[-1] * fallback_multiplier
                
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
                
            # Calculate 30-day average up/down % for commodities
            if is_commodity and not hist_1m.empty and len(hist_1m) > 1:
                returns = hist_1m['Close'].pct_change().dropna()
                up_days = returns[returns > 0]
                down_days = returns[returns < 0]
                levels['Avg_Up_Pct'] = up_days.mean() if not up_days.empty else 0
                levels['Avg_Down_Pct'] = down_days.mean() if not down_days.empty else 0
                levels['Prev_Close'] = hist['Close'].iloc[-2] * fallback_multiplier
            else:
                levels['Avg_Up_Pct'] = None
                levels['Avg_Down_Pct'] = None
                levels['Prev_Close'] = hist['Close'].iloc[-2] * fallback_multiplier if len(hist) >= 2 else None

            # Calculate ATR, Bollinger Bands, Volume, RSI, and MACD
            if len(hist) >= 35:
                levels['ATR_14'] = ta.volatility.average_true_range(hist['High'], hist['Low'], hist['Close'], window=14).iloc[-1] * fallback_multiplier
                levels['BB_Upper'] = ta.volatility.bollinger_hband(hist['Close'], window=20, window_dev=2).iloc[-1] * fallback_multiplier
                levels['BB_Lower'] = ta.volatility.bollinger_lband(hist['Close'], window=20, window_dev=2).iloc[-1] * fallback_multiplier
                levels['Vol_20_MA'] = hist['Volume'].rolling(window=20).mean().iloc[-2] # previous day MA
                levels['Vol_Current'] = hist['Volume'].iloc[-1]
                
                # Confluence Indicators
                levels['RSI_14'] = ta.momentum.rsi(hist['Close'], window=14).iloc[-1]
                macd = ta.trend.MACD(hist['Close'])
                levels['MACD_Line'] = macd.macd().iloc[-1]
                levels['MACD_Signal'] = macd.macd_signal().iloc[-1]
            elif len(hist) >= 20:
                levels['ATR_14'] = ta.volatility.average_true_range(hist['High'], hist['Low'], hist['Close'], window=14).iloc[-1] * fallback_multiplier
                levels['BB_Upper'] = ta.volatility.bollinger_hband(hist['Close'], window=20, window_dev=2).iloc[-1] * fallback_multiplier
                levels['BB_Lower'] = ta.volatility.bollinger_lband(hist['Close'], window=20, window_dev=2).iloc[-1] * fallback_multiplier
                levels['Vol_20_MA'] = hist['Volume'].rolling(window=20).mean().iloc[-2] # previous day MA
                levels['Vol_Current'] = hist['Volume'].iloc[-1]
                levels['RSI_14'] = None
                levels['MACD_Line'] = None
                levels['MACD_Signal'] = None
            else:
                levels['ATR_14'] = None
                levels['BB_Upper'] = None
                levels['BB_Lower'] = None
                levels['Vol_20_MA'] = None
                levels['Vol_Current'] = None
                levels['RSI_14'] = None
                levels['MACD_Line'] = None
                levels['MACD_Signal'] = None

            
        except Exception as e:
            print(f"Error fetching historical extremes from yfinance for {ticker_symbol}: {e}")
            
        # Dynamically frame S1/S2 and R1/R2 around the current price
        if not current_price and levels and 'Current_Price' in levels:
            current_price = levels['Current_Price']

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
        if not hasattr(self, 'intrinsic_cache') or not self.intrinsic_cache:
            self.intrinsic_cache = self._load_intrinsic_cache()
            
        now_ts = time.time()
        cached = self.intrinsic_cache.get(yf_symbol)
        # Graham number relies on quarterly earnings/book value; 14-day cache is safe & super fast
        if cached and (now_ts - cached.get('timestamp', 0) < 86400 * 14):
            return cached.get('value')
            
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info
            eps = info.get('trailingEps')
            bv = info.get('bookValue')
            if eps and bv and eps > 0 and bv > 0:
                graham_number = round(math.sqrt(22.5 * eps * bv), 2)
                self.intrinsic_cache[yf_symbol] = {'value': graham_number, 'timestamp': int(now_ts)}
                self._save_intrinsic_cache()
                return graham_number
            else:
                self.intrinsic_cache[yf_symbol] = {'value': None, 'timestamp': int(now_ts)}
                self._save_intrinsic_cache()
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
        
        # Fallback to yfinance for Stocks / Commodities
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1d")
            if len(hist) == 0:
                hist = ticker.history(period="5d")
            if len(hist) > 0:
                price = hist['Close'].iloc[-1]
                return price * fallback_multiplier
            return None
        except Exception as e:
            print(f"Error fetching current price for {ticker_symbol} via yfinance: {e}")
            return None

    @staticmethod
    def get_circuit_stage_rank(event_str):
        """
        Ranks circuit events so alerts only escalate and never downgrade or duplicate.
        Returns (direction, rank):
          rank 1: Warning (Within 1% of initial circuit)
          rank 2: Initial Circuit Hit
          rank 3: Warning Enhanced (Within 1% of enhanced circuit)
          rank 4: Enhanced Circuit Hit
        """
        if not event_str:
            return None, 0
        s = str(event_str).upper()
        direction = "LOWER" if "LOWER" in s else ("UPPER" if "UPPER" in s else None)
        if not direction:
            return None, 0
            
        if "ENHANCED" in s and "HIT" in s:
            return direction, 4
        elif "ENHANCED" in s and ("WARNING" in s or "WITHIN" in s):
            return direction, 3
        elif "HIT" in s:
            return direction, 2
        elif "WARNING" in s or "WITHIN" in s:
            return direction, 1
        return direction, 0

    def fetch_previous_state(self):
        state = {}
        # 1. Load local persistent alerts state if available
        local_state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
        if os.path.exists(local_state_path):
            try:
                with open(local_state_path, 'r') as f:
                    local_data = json.load(f)
                    if isinstance(local_data, dict):
                        state = local_data
            except Exception as e:
                print(f"Error reading local alerts_state.json: {e}")

        # 2. Merge with Supabase market_data
        try:
            headers = {
                'apikey': self.supabase_key,
                'Authorization': f'Bearer {self.supabase_key}'
            }
            response = requests.get(f"{self.supabase_url}?select=symbol,alert_status,last_alert_date,last_alert_msg,signal", headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                for item in data:
                    sym = item.get('symbol')
                    if sym:
                        if sym not in state:
                            state[sym] = item
                        else:
                            # Keep whichever has the most recent alert date or non-empty message
                            supa_date = item.get('last_alert_date')
                            loc_date = state[sym].get('last_alert_date')
                            if supa_date and (not loc_date or supa_date >= loc_date):
                                state[sym].update(item)
                return state
            else:
                print(f"Failed to fetch previous state from Supabase. Status: {response.status_code} {response.text}")
                return state if state else None
        except Exception as e:
            print(f"Error fetching previous state from Supabase: {e}")
            return state if state else None

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
        
        # Calculate Trend Signals (EMA 50 vs EMA 200 vs Current Price)
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

        # STRICT DEDUPLICATION: Maximum once a day per asset
        if last_alert_date == today_ist:
            print(f"Skipping alert for {name} ({symbol}): Already alerted today with '{last_alert_msg}'. (Max 1 alert/day)")
            return alerts, last_alert_msg, last_alert_date, last_alert_msg, current_signal

        yf_symbol_map = {
            "GOLD": "GC=F",
            "SILVER": "SI=F",
            "CRUDEOIL": "CL=F",
            "NATURALGAS": "NG=F",
            "COPPER": "HG=F"
        }
        yf_sym = yf_symbol_map.get(symbol, symbol)
        chart_url = f"https://finance.yahoo.com/quote/{yf_sym}"

        # Volume and Volatility Checks
        vol_ma = levels.get('Vol_20_MA')
        vol_curr = levels.get('Vol_Current')
        is_high_volume = (vol_curr and vol_ma and vol_curr > 1.5 * vol_ma)
        volume_msg = " [High Volume Confirmed]" if is_high_volume else ""

        bb_upper = levels.get('BB_Upper')
        bb_lower = levels.get('BB_Lower')
        atr = levels.get('ATR_14')

        # Note: Non-circuit alerts (High/Low extremes, Bollinger Bands, Moving Averages, 30d moves)
        # are excluded per user preference: Alerts are exclusively sent when a Commodity hits its Upper or Lower Circuit.
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

    def record_alerts_dispatched(self, dispatched_alerts):
        """
        Record alerts in Supabase and local web/alerts_state.json cache ONLY after dispatch has succeeded.
        """
        if not dispatched_alerts:
            return
            
        headers = {
            'apikey': self.supabase_key,
            'Authorization': f'Bearer {self.supabase_key}',
            'Content-Type': 'application/json'
        }
        
        local_state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
        local_state = {}
        if os.path.exists(local_state_path):
            try:
                with open(local_state_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        local_state = data
            except Exception:
                local_state = {}

        for alert in dispatched_alerts:
            symbol = alert.get('symbol')
            event_type = alert.get('event_type')
            today_ist = alert.get('today_ist') or datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
            if not symbol or not event_type:
                continue
                
            payload = {
                'symbol': symbol,
                'last_alert_date': today_ist,
                'last_alert_msg': event_type,
                'alert_status': event_type,
                'last_updated': datetime.utcnow().isoformat()
            }
            local_state[symbol] = payload
            try:
                patch_url = f"{self.supabase_url}?symbol=eq.{symbol}"
                resp = requests.patch(patch_url, headers=headers, json=payload, timeout=10)
                if resp.status_code in [200, 204]:
                    print(f"Recorded alert dispatch for {symbol} ({event_type}) in Supabase.")
                else:
                    resp = requests.post(self.supabase_url, headers=headers, json=[payload], timeout=10)
                    print(f"Upserted alert dispatch for {symbol} in Supabase (status {resp.status_code}).")
            except Exception as e:
                print(f"Error recording alert dispatch for {symbol}: {e}")

        try:
            with open(local_state_path, 'w') as f:
                json.dump(local_state, f, indent=2)
            print(f"Persisted {len(local_state)} alert states to web/alerts_state.json")
        except Exception as e:
            print(f"Error persisting alerts_state.json: {e}")

    def fetch_tradingview_commodity_quotes(self):
        """
        Fetches live MCX futures quotes dynamically from TradingView futures scanner.
        Intelligently resolves active front-month contracts (e.g. November 2026 X2026 vs expired October V2026),
        preventing stale prices when continuous tickers (1!) lag after expiry.
        """
        try:
            url = 'https://scanner.tradingview.com/futures/scan'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            cols = ['close', 'open', 'high', 'low', 'change', 'change_abs', 'volume', 'description', 'expiration', 'name',
                    'RSI', 'ADX', 'ATR', 'average_volume_10d_calc', 'EMA20', 'EMA50']
            payload = {
                'filter': [{'left': 'exchange', 'operation': 'equal', 'right': 'MCX'}],
                'columns': cols
            }
            
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            data_rows = []
            if resp.status_code == 200:
                data_rows = resp.json().get('data', [])
                
            # If exchange filter succeeded, dynamically resolve active front-month contracts
            if data_rows:
                by_symbol = {row['s']: dict(zip(cols, row.get('d', []))) for row in data_rows}
                today_int = int(datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y%m%d'))
                
                quotes = {}
                for sym, conf in self.commodities.items():
                    root = sym
                    default_tick = conf.get('tv_ticker') or f"MCX:{root}1!"
                    
                    # 1. Collect all standard unexpired monthly contracts for this commodity
                    monthly_contracts = []
                    for s, d in by_symbol.items():
                        if not s.startswith(f"MCX:{root}"):
                            continue
                        ticker_part = s.split(':')[-1]
                        suffix = ticker_part[len(root):]
                        # Standard MCX contracts have suffix format: 1 month letter + 4 digit year (e.g. X2026, Z2026)
                        if len(suffix) == 5 and suffix[0] in 'FGHJKMNQUVXZ' and suffix[1:].isdigit():
                            exp = d.get('expiration')
                            if exp and exp >= today_int:
                                monthly_contracts.append({
                                    'ticker': s,
                                    'data': d,
                                    'exp': exp,
                                    'vol': d.get('volume') or 0,
                                    'close': d.get('close')
                                })
                    monthly_contracts.sort(key=lambda x: x['exp'])
                    
                    # Determine active front-month contract:
                    # If nearest contract expires in <= 3 days and 2nd contract has higher liquidity, roll to 2nd contract
                    front_contract = None
                    if monthly_contracts:
                        if len(monthly_contracts) > 1 and (monthly_contracts[0]['exp'] - today_int <= 3) and (monthly_contracts[1]['vol'] > monthly_contracts[0]['vol'] * 1.5):
                            front_contract = monthly_contracts[1]
                        else:
                            front_contract = monthly_contracts[0]
                            
                    cont_data = by_symbol.get(default_tick)
                    chosen_ticker = default_tick
                    chosen_data = cont_data
                    
                    if front_contract:
                        fc_tick = front_contract['ticker']
                        fc_data = front_contract['data']
                        # Check if continuous ticker is missing, has 0 volume, or diverges (>4%) from active front contract
                        if not cont_data or cont_data.get('close') is None:
                            chosen_ticker = fc_tick
                            chosen_data = fc_data
                        else:
                            c_close = cont_data.get('close') or 0
                            f_close = fc_data.get('close') or 0
                            if abs(c_close - f_close) / (f_close or 1) > 0.04 or (cont_data.get('volume') == 0 and fc_data.get('volume') > 0):
                                print(f"Detected continuous ticker lag on {default_tick} (Price: {c_close}). Rolling to active {fc_tick} (Price: {f_close}, Exp: {front_contract['exp']}).")
                                chosen_ticker = fc_tick
                                chosen_data = fc_data
                            else:
                                # They align; prefer explicit active contract ticker so expiry is known
                                chosen_ticker = fc_tick
                                chosen_data = fc_data
                    elif cont_data:
                        chosen_ticker = default_tick
                        chosen_data = cont_data
                        
                    if chosen_data and chosen_data.get('close') is not None:
                        close = float(chosen_data['close'])
                        chg_abs = float(chosen_data.get('change_abs') or 0.0)
                        chg_pct = float(chosen_data.get('change') or 0.0)
                        prev_close = round(close - chg_abs, 2)
                        exp_val = chosen_data.get('expiration')
                        exp_str = f"{str(exp_val)[:4]}-{str(exp_val)[4:6]}-{str(exp_val)[6:8]}" if exp_val else None
                        quotes[sym] = {
                            'symbol': sym,
                            'ticker': chosen_ticker,
                            'name': conf.get('name', sym),
                            'close': close,
                            'open': float(chosen_data.get('open') or close),
                            'high': float(chosen_data.get('high') or close),
                            'low': float(chosen_data.get('low') or close),
                            'change': round(chg_pct, 2),
                            'change_abs': round(chg_abs, 2),
                            'volume': int(chosen_data.get('volume') or 0),
                            'prev_close': prev_close,
                            'description': chosen_data.get('description'),
                            'expiry': exp_str,
                            'contract_desc': chosen_data.get('description'),
                            'rsi_14': float(chosen_data['RSI']) if chosen_data.get('RSI') is not None else None,
                            'adx_14': float(chosen_data['ADX']) if chosen_data.get('ADX') is not None else None,
                            'atr_14': float(chosen_data['ATR']) if chosen_data.get('ATR') is not None else None,
                            'avg_volume_10d': float(chosen_data['average_volume_10d_calc']) if chosen_data.get('average_volume_10d_calc') is not None else None,
                            'ema_20': float(chosen_data['EMA20']) if chosen_data.get('EMA20') is not None else None,
                            'ema_50': float(chosen_data['EMA50']) if chosen_data.get('EMA50') is not None else None
                        }
                return quotes
                
            # Fallback direct ticker query if scan returned empty
            tickers = [data.get('tv_ticker') for sym, data in self.commodities.items() if data.get('tv_ticker')]
            if not tickers:
                return {}
            fallback_payload = {
                'symbols': {'tickers': tickers},
                'columns': ['close', 'open', 'high', 'low', 'change', 'change_abs', 'volume', 'description',
                            'RSI', 'ADX', 'ATR', 'average_volume_10d_calc', 'EMA20', 'EMA50']
            }
            resp_fb = requests.post(url, json=fallback_payload, headers=headers, timeout=10)
            if resp_fb.status_code != 200:
                return {}
            res_data = resp_fb.json().get('data', [])
            quotes = {}
            cols_fb = fallback_payload['columns']
            symbol_to_ticker = {data.get('tv_ticker'): sym for sym, data in self.commodities.items()}
            for row in res_data:
                tick = row.get('s')
                sym = symbol_to_ticker.get(tick)
                if sym:
                    d = dict(zip(cols_fb, row.get('d', [])))
                    close = d.get('close')
                    change_abs = d.get('change_abs') or 0.0
                    change_pct = d.get('change') or 0.0
                    prev_close = round(close - change_abs, 2) if close is not None else None
                    quotes[sym] = {
                        'symbol': sym,
                        'ticker': tick,
                        'name': self.commodities[sym].get('name', sym),
                        'close': close,
                        'open': d.get('open'),
                        'high': d.get('high'),
                        'low': d.get('low'),
                        'change': change_pct,
                        'change_abs': change_abs,
                        'volume': d.get('volume'),
                        'prev_close': prev_close,
                        'description': d.get('description'),
                        'rsi_14': float(d['RSI']) if d.get('RSI') is not None else None,
                        'adx_14': float(d['ADX']) if d.get('ADX') is not None else None,
                        'atr_14': float(d['ATR']) if d.get('ATR') is not None else None,
                        'avg_volume_10d': float(d['average_volume_10d_calc']) if d.get('average_volume_10d_calc') is not None else None,
                        'ema_20': float(d['EMA20']) if d.get('EMA20') is not None else None,
                        'ema_50': float(d['EMA50']) if d.get('EMA50') is not None else None
                    }
            return quotes
        except Exception as e:
            print(f"Error fetching TradingView commodity quotes: {e}")
            return {}

    def _process_single_stock(self, symbol, data, previous_state):
        name = data.get('name', symbol) if isinstance(data, dict) else symbol
        yf_sym = data.get('yf_symbol', symbol) if isinstance(data, dict) else symbol
        
        current_price = None
        if self.angel_active and '.NS' in symbol:
            sec_id = self.get_angel_token(symbol)
            if sec_id:
                current_price = self.get_angel_current_price(sec_id)
                
        levels = self.get_support_resistance_levels(symbol, is_commodity=False, fallback_multiplier=1.0, yf_symbol=yf_sym, current_price=current_price)
        
        if current_price is None and levels and 'Current_Price' in levels:
            current_price = levels['Current_Price']
            
        intrinsic_val = self.get_intrinsic_value(yf_sym)
        
        new_alerts, alert_status, last_alert_date, last_alert_msg, current_signal = self.evaluate_levels(
            name, symbol, levels, current_price, previous_state, is_commodity=False
        )
        
        records = []
        rsi_info = None
        
        if levels and current_price:
            records.append({
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
            records.append({
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
            
            if levels.get('RSI_14') is not None:
                rsi_info = {'symbol': symbol, 'name': name, 'rsi': levels['RSI_14'], 'type': 'stock'}
                
        return records, rsi_info

    def check_alerts(self):
        alerts = []
        
        # Pull previous alert state from Supabase to prevent duplicate emails
        previous_state = self.fetch_previous_state()
        if previous_state is None:
            print("Failed to fetch previous state from Supabase. Aborting this run to prevent spamming duplicate alerts.")
            return alerts
            
        market_data_payload = []
        
        # 1. Fetch live MCX quotes directly from internet scanner for genuine MCX pricing
        tv_quotes = self.fetch_tradingview_commodity_quotes()
        if tv_quotes:
            print(f"Loaded {len(tv_quotes)} live commodity quotes directly from exchange scanner feed.")
        
        # 1. Check Commodities
        for symbol, data in self.commodities.items():
            name = data['name']
            yf_sym = data.get('yf_symbol')
            
            # Check live internet MCX quote first
            tv_quote = tv_quotes.get(symbol)
            current_price = None
            fut_price = None
            
            if tv_quote and tv_quote.get('close') is not None:
                current_price = float(tv_quote['close'])
                fut_price = current_price
                print(f"\nChecking Commodity {name} ({symbol}) via Live Internet MCX Scanner: ₹{current_price} (PrevClose: ₹{tv_quote.get('prev_close')})...")
            
            # DYNAMIC UNIT CONVERSION (Global USD to MCX INR) as fallback
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
                else:
                    conversion = self.usd_inr_rate
            
            if current_price is None:
                print(f"\nChecking Commodity {name} ({symbol}) via MCX API (or yfinance fallback)...")
                current_price = self.get_current_price(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
            
            # Fetch Commodity Future explicitly early from Angel if available
            if self.angel_active:
                sec_id = self.get_angel_mcx_near_month(symbol)
                if sec_id:
                    angel_fut = self.get_angel_current_price(sec_id, exchange_segment="MCX")
                    if angel_fut:
                        fut_price = angel_fut
                        current_price = fut_price
                    
            # Fallback for spot price if yfinance fails
            if current_price is None and fut_price is not None:
                current_price = fut_price
            
            levels = self.get_support_resistance_levels(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym, current_price=current_price, tv_quote=tv_quote)
            
            # Default alert/signal states for commodity
            alert_status = None
            last_alert_date = previous_state.get(symbol, {}).get('last_alert_date')
            last_alert_msg = previous_state.get(symbol, {}).get('last_alert_msg')
            current_signal = 'NEUTRAL'

            # Check Commodity Upper/Lower Circuit Limits (Initial & Enhanced Slabs)
            dpl_initial = data.get('dpl_initial', 0.04)
            dpl_max = data.get('dpl_max', dpl_initial)
            
            prev_close_ref = None
            if tv_quote and tv_quote.get('prev_close') is not None:
                prev_close_ref = float(tv_quote['prev_close'])
            elif levels and levels.get('Prev_Close'):
                prev_close_ref = float(levels['Prev_Close'])
            elif current_price:
                prev_close_ref = float(current_price)
                
            day_high_ref = float(tv_quote.get('high') or current_price) if tv_quote else current_price
            day_low_ref = float(tv_quote.get('low') or current_price) if tv_quote else current_price
            
            if prev_close_ref and current_price and prev_close_ref > 0:
                uc_initial = round(prev_close_ref * (1 + dpl_initial), 2)
                uc_max = round(prev_close_ref * (1 + dpl_max), 2)
                lc_initial = round(prev_close_ref * (1 - dpl_initial), 2)
                lc_max = round(prev_close_ref * (1 - dpl_max), 2)
                
                # Distance to initial circuit limits (in percentage & absolute points)
                dist_to_lc_pct = ((current_price - lc_initial) / current_price) * 100
                dist_to_uc_pct = ((uc_initial - current_price) / current_price) * 100
                day_low_dist_to_lc_pct = ((day_low_ref - lc_initial) / day_low_ref) * 100 if day_low_ref else dist_to_lc_pct
                day_high_dist_to_uc_pct = ((uc_initial - day_high_ref) / day_high_ref) * 100 if day_high_ref else dist_to_uc_pct
                
                is_enhanced_uc = bool(current_price >= uc_max or day_high_ref >= uc_max)
                is_initial_uc = bool(current_price >= uc_initial or day_high_ref >= uc_initial)
                is_enhanced_lc = bool(current_price <= lc_max or day_low_ref <= lc_max)
                is_initial_lc = bool(current_price <= lc_initial or day_low_ref <= lc_initial)
                
                # Check for 1% warning before initial circuit limit is breached (distance <= 1.0%)
                is_warn_1pct_lc = bool(not is_initial_lc and (dist_to_lc_pct <= 1.0 or day_low_dist_to_lc_pct <= 1.0))
                is_warn_1pct_uc = bool(not is_initial_uc and (dist_to_uc_pct <= 1.0 or day_high_dist_to_uc_pct <= 1.0))
                
                # Check for 1% warning before enhanced circuit limit if initial was breached
                dist_to_lc_max_pct = ((current_price - lc_max) / current_price) * 100
                dist_to_uc_max_pct = ((uc_max - current_price) / current_price) * 100
                is_warn_1pct_enhanced_lc = bool(is_initial_lc and not is_enhanced_lc and dpl_max > dpl_initial and dist_to_lc_max_pct <= 1.0)
                is_warn_1pct_enhanced_uc = bool(is_initial_uc and not is_enhanced_uc and dpl_max > dpl_initial and dist_to_uc_max_pct <= 1.0)
                
                event_type = None
                circuit_price = None
                circuit_slab_pct = None
                is_warning = False
                warning_dist_pct = None
                warning_dist_pts = None
                
                if is_enhanced_lc:
                    event_type = f"ENHANCED LOWER CIRCUIT HIT (-{dpl_max*100:.0f}%)"
                    circuit_price = lc_max
                    circuit_slab_pct = dpl_max * 100
                    extreme_price = day_low_ref
                elif is_warn_1pct_enhanced_lc:
                    event_type = f"CIRCUIT WARNING: Within 1% of Enhanced Lower Circuit (-{dpl_max*100:.0f}%)"
                    circuit_price = lc_max
                    circuit_slab_pct = dpl_max * 100
                    extreme_price = day_low_ref
                    is_warning = True
                    warning_dist_pct = dist_to_lc_max_pct
                    warning_dist_pts = current_price - lc_max
                elif is_initial_lc:
                    event_type = f"LOWER CIRCUIT HIT (-{dpl_initial*100:.0f}%)"
                    circuit_price = lc_initial
                    circuit_slab_pct = dpl_initial * 100
                    extreme_price = day_low_ref
                elif is_warn_1pct_lc:
                    event_type = f"CIRCUIT WARNING: Within 1% of Lower Circuit (-{dpl_initial*100:.0f}%)"
                    circuit_price = lc_initial
                    circuit_slab_pct = dpl_initial * 100
                    extreme_price = day_low_ref
                    is_warning = True
                    warning_dist_pct = min(dist_to_lc_pct, day_low_dist_to_lc_pct)
                    warning_dist_pts = min(current_price - lc_initial, day_low_ref - lc_initial)
                elif is_enhanced_uc:
                    event_type = f"ENHANCED UPPER CIRCUIT HIT (+{dpl_max*100:.0f}%)"
                    circuit_price = uc_max
                    circuit_slab_pct = dpl_max * 100
                    extreme_price = day_high_ref
                elif is_warn_1pct_enhanced_uc:
                    event_type = f"CIRCUIT WARNING: Within 1% of Enhanced Upper Circuit (+{dpl_max*100:.0f}%)"
                    circuit_price = uc_max
                    circuit_slab_pct = dpl_max * 100
                    extreme_price = day_high_ref
                    is_warning = True
                    warning_dist_pct = dist_to_uc_max_pct
                    warning_dist_pts = uc_max - current_price
                elif is_initial_uc:
                    event_type = f"UPPER CIRCUIT HIT (+{dpl_initial*100:.0f}%)"
                    circuit_price = uc_initial
                    circuit_slab_pct = dpl_initial * 100
                    extreme_price = day_high_ref
                elif is_warn_1pct_uc:
                    event_type = f"CIRCUIT WARNING: Within 1% of Upper Circuit (+{dpl_initial*100:.0f}%)"
                    circuit_price = uc_initial
                    circuit_slab_pct = dpl_initial * 100
                    extreme_price = day_high_ref
                    is_warning = True
                    warning_dist_pct = min(dist_to_uc_pct, day_high_dist_to_uc_pct)
                    warning_dist_pts = min(uc_initial - current_price, uc_initial - day_high_ref)
                    
                if event_type:
                    move_from_anchor = ((current_price - prev_close_ref) / prev_close_ref) * 100
                    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
                    today_ist = now_dt.strftime('%Y-%m-%d')
                    today_time_ist = now_dt.strftime('%d %b %Y, %I:%M %p IST')
                    
                    last_c_date = previous_state.get(symbol, {}).get('last_alert_date')
                    last_c_msg = previous_state.get(symbol, {}).get('last_alert_msg') or ""
                    
                    curr_dir, curr_rank = self.get_circuit_stage_rank(event_type)
                    last_dir, last_rank = self.get_circuit_stage_rank(last_c_msg)
                    
                    is_duplicate = False
                    if last_c_date == today_ist:
                        # Directional stage check: if alerted in the same direction today,
                        # only escalate (e.g. Warning -> Hit). Never downgrade (e.g. Hit -> Warning)
                        # and never re-send warnings for the same limit on the same day.
                        if curr_dir and last_dir == curr_dir:
                            if last_rank >= curr_rank:
                                is_duplicate = True
                        elif last_c_msg == event_type:
                            is_duplicate = True
                            
                    if is_duplicate:
                        print(f"Skipping duplicate circuit alert for {name} ({symbol}): already alerted for {last_c_msg} (stage {last_rank}) today.")
                    else:
                        if is_warning:
                            target_direction = "LOWER" if "Lower" in event_type else "UPPER"
                            alert_subj = f"⚠️ CIRCUIT WARNING: {name} is within 1% of {target_direction} CIRCUIT (₹{circuit_price:.2f})!"
                            alert_body = (
                                f"⚠️ MCX CIRCUIT EARLY WARNING (< 1% TO CIRCUIT LIMIT)\n\n"
                                f"Commodity: {name} ({symbol})\n"
                                f"Alert: {event_type}\n"
                                f"Event Date & Time: {today_time_ist}\n"
                                f"Live Price: ₹{current_price:.2f}\n"
                                f"Day Extreme: ₹{extreme_price:.2f}\n"
                                f"Circuit Limit Price: ₹{circuit_price:.2f} ({circuit_slab_pct:.1f}% DPL Limit)\n"
                                f"Distance to Circuit: {max(0.0, warning_dist_pct):.2f}% (₹{max(0.0, warning_dist_pts):.2f} pts away)\n"
                                f"Previous Close (Anchor): ₹{prev_close_ref:.2f}\n"
                                f"Day Move: {move_from_anchor:+.2f}%\n\n"
                                f"Trading Notice: Asset is within 1% of daily circuit band. High probability of cooling-off period or limit freeze.\n\n"
                                f"Live Dashboard: https://VenuInturiphoos.github.io/commodity-alerts/index.html#circuits"
                            )
                        else:
                            alert_subj = f"⚡ MCX CIRCUIT HIT: {name} reached {event_type} (₹{circuit_price:.2f})!"
                            alert_body = (
                                f"⚡ MCX DAILY CIRCUIT LIMIT (DPL) HIT!\n\n"
                                f"Commodity: {name} ({symbol})\n"
                                f"Event: {event_type}\n"
                                f"Event Date & Time: {today_time_ist}\n"
                                f"Live Price: ₹{current_price:.2f}\n"
                                f"Day Extreme: ₹{extreme_price:.2f}\n"
                                f"Circuit Limit Price: ₹{circuit_price:.2f} ({circuit_slab_pct:.1f}% DPL Limit)\n"
                                f"Previous Close (Anchor): ₹{prev_close_ref:.2f}\n"
                                f"Day Move: {move_from_anchor:+.2f}%\n\n"
                                f"Exchange Status: Trading reached daily permissible limit.\n"
                                f"Historical Pattern: Check 30-day mean reversion bounce/pullback rates.\n\n"
                                f"Live Dashboard: https://VenuInturiphoos.github.io/commodity-alerts/index.html#circuits"
                            )
                        alerts.append({
                            'symbol': symbol,
                            'name': name,
                            'event_type': event_type,
                            'today_ist': today_ist,
                            'subject': alert_subj,
                            'body': alert_body
                        })
                        alert_status = event_type
                        last_alert_date = today_ist
                        last_alert_msg = event_type
                        print(f"🚨 [{'CIRCUIT WARNING' if is_warning else 'CIRCUIT HIT'} ALERT GENERATED] {alert_subj}")
            
            if levels and current_price:
                if levels.get('RSI_14') is not None:
                    self.cached_rsi[symbol] = {'name': name, 'rsi': levels['RSI_14'], 'type': 'commodity'}

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
            
        # 2. Check Stocks in Parallel (8 worker threads)
        print("\nChecking Stocks in parallel (8 worker threads)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(self._process_single_stock, symbol, data, previous_state)
                for symbol, data in self.stocks.items()
            ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    records, rsi_info = future.result()
                    if records:
                        market_data_payload.extend(records)
                    if rsi_info:
                        self.cached_rsi[rsi_info['symbol']] = rsi_info
                except Exception as e:
                    print(f"Error processing stock: {e}")

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

        # Generate the dedicated Daily Circuit Limits JSON
        try:
            self.generate_circuits_data(tv_quotes)
        except Exception as e:
            print(f"Error generating circuits data in check_alerts: {e}")

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
                                  
                    # 3. Update profile balance (F&O Margin Debit / Release + Realized P&L)
                    prof_resp = requests.get(f"https://cohupetijvykzmeliubg.supabase.co/rest/v1/profiles?id=eq.{user_id}&select=balance", headers=headers)
                    if prof_resp.status_code == 200 and prof_resp.json():
                        current_bal = float(prof_resp.json()[0]['balance'])
                        
                        margin_rates = {
                            'CRUDEOIL': 0.20, 'NATURALGAS': 0.20, 'SILVER': 0.12,
                            'COPPER': 0.15, 'GOLD': 0.10, 'NIFTY': 0.10, 'BANKNIFTY': 0.10
                        }
                        base_sym = symbol.replace('-FUT', '').replace('.NS', '')
                        m_rate = margin_rates.get(base_sym, margin_rates.get(symbol, 0.15))
                        
                        # Fetch prior transactions (excluding the one just inserted) to compute existing position
                        user_tx_resp = requests.get(f"https://cohupetijvykzmeliubg.supabase.co/rest/v1/transactions?user_id=eq.{user_id}&symbol=eq.{symbol}&select=*&order=timestamp.asc", headers=headers)
                        cur_shares = 0
                        avg_p = 0.0
                        if user_tx_resp.status_code == 200:
                            all_txs = user_tx_resp.json()[:-1] # exclude newly added tx
                            for tx in all_txs:
                                t_qty = tx['quantity']
                                t_pr = tx['price']
                                if tx['transaction_type'] == 'BUY':
                                    if cur_shares >= 0:
                                        avg_p = ((cur_shares * avg_p) + (t_qty * t_pr)) / (cur_shares + t_qty) if (cur_shares + t_qty) > 0 else 0
                                        cur_shares += t_qty
                                    else:
                                        cur_shares += t_qty
                                else:
                                    if cur_shares <= 0:
                                        avg_p = ((abs(cur_shares) * avg_p) + (t_qty * t_pr)) / (abs(cur_shares) + t_qty) if (abs(cur_shares) + t_qty) > 0 else 0
                                        cur_shares -= t_qty
                                    else:
                                        cur_shares -= t_qty

                        delta = 0.0
                        if tx_type == 'BUY':
                            if cur_shares >= 0:
                                delta = -(qty * limit_price * m_rate)
                            else:
                                short_sh = abs(cur_shares)
                                cl_qty = min(qty, short_sh)
                                op_qty = max(0, qty - short_sh)
                                delta += (cl_qty * avg_p * m_rate) + (cl_qty * (avg_p - limit_price))
                                if op_qty > 0:
                                    delta -= (op_qty * limit_price * m_rate)
                        else:
                            if cur_shares <= 0:
                                delta = -(qty * limit_price * m_rate)
                            else:
                                long_sh = cur_shares
                                cl_qty = min(qty, long_sh)
                                op_qty = max(0, qty - long_sh)
                                delta += (cl_qty * avg_p * m_rate) + (cl_qty * (limit_price - avg_p))
                                if op_qty > 0:
                                    delta -= (op_qty * limit_price * m_rate)

                        new_bal = max(0.0, current_bal + delta)
                        requests.patch(f"https://cohupetijvykzmeliubg.supabase.co/rest/v1/profiles?id=eq.{user_id}", 
                                       headers=headers, json={"balance": new_bal})
                                       
        except Exception as e:
            print(f"Error processing limit orders: {e}")
    def generate_near_alerts_json(self):
        near_buy = []
        near_sell = []
        
        # Fast path: use cached RSI computed during check_alerts (0ms)
        if getattr(self, 'cached_rsi', None):
            for symbol, info in self.cached_rsi.items():
                rsi = info.get('rsi')
                if rsi is not None:
                    if rsi < 45:
                        near_buy.append({'symbol': symbol, 'name': info['name'], 'rsi': round(rsi, 2)})
                    elif rsi > 55:
                        near_sell.append({'symbol': symbol, 'name': info['name'], 'rsi': round(rsi, 2)})
        else:
            # Fallback if called standalone without check_alerts
            for symbol, data in self.commodities.items():
                name = data['name']
                levels = self.get_support_resistance_levels(symbol, is_commodity=True, yf_symbol=data.get('yf_symbol'))
                if levels and levels.get('RSI_14'):
                    rsi = levels['RSI_14']
                    if rsi < 45:
                        near_buy.append({'symbol': symbol, 'name': name, 'rsi': round(rsi, 2)})
                    elif rsi > 55:
                        near_sell.append({'symbol': symbol, 'name': name, 'rsi': round(rsi, 2)})

            for symbol, data in self.stocks.items():
                stock_name = data.get('name', symbol) if isinstance(data, dict) else data
                levels = self.get_support_resistance_levels(symbol, current_price=100)
                if levels and levels.get('RSI_14'):
                    rsi = levels['RSI_14']
                    if rsi < 45:
                        near_buy.append({'symbol': symbol, 'name': stock_name, 'rsi': round(rsi, 2)})
                    elif rsi > 55:
                        near_sell.append({'symbol': symbol, 'name': stock_name, 'rsi': round(rsi, 2)})
                    
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'near_alerts.json')
        with open(output_path, 'w') as f:
            json.dump({'near_buy': near_buy, 'near_sell': near_sell}, f)
        print(f"Exported {len(near_buy)} near buys and {len(near_sell)} near sells to web/near_alerts.json")

    def generate_circuits_data(self, tv_quotes=None):
        """Generates comprehensive Daily Upper and Lower Circuit Limits for commodities and writes to web/circuits_data.json."""
        now_ts = time.time()
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_data.json')
        if (now_ts - getattr(self, '_last_circuits_data_time', 0) < 60) and getattr(self, '_cached_circuits_data', None) and os.path.exists(output_path):
            print("Circuits data freshly generated (<60s ago). Skipping redundant pass.")
            return self._cached_circuits_data

        if tv_quotes is None:
            tv_quotes = self.fetch_tradingview_commodity_quotes()
            
        circuits = []
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M:%S %p IST')
        
        # Parent mapping for Mini / Micro commodities to guarantee identical price and limit sync
        PARENT_MAP = {
            'CRUDEOILM': 'CRUDEOIL',
            'NATURALGASM': 'NATURALGAS',
            'SILVERMIC': 'SILVER',
            'GOLDM': 'GOLD',
            'ZINCMINI': 'ZINC',
            'ALUMINI': 'ALUMINIUM'
        }
        
        for symbol, data in self.commodities.items():
            name = data.get('name', symbol)
            dpl_initial = data.get('dpl_initial', 0.04)
            dpl_max = data.get('dpl_max', 0.06)
            category = data.get('category', 'Commodities')
            unit = data.get('unit', '')
            ticker = data.get('tv_ticker', '')
            
            quote = tv_quotes.get(symbol) if tv_quotes else None
            # If direct TV quote for mini/micro not available, seamlessly inherit parent quote
            if (not quote or quote.get('close') is None) and symbol in PARENT_MAP and tv_quotes:
                parent_quote = tv_quotes.get(PARENT_MAP[symbol])
                if parent_quote and parent_quote.get('close') is not None:
                    quote = parent_quote.copy()
                    quote['ticker'] = data.get('tv_ticker') or quote.get('ticker')
                    quote['contract_desc'] = f"{name} Futures"

            expiry = None
            contract_desc = None
            if quote and quote.get('close') is not None:
                ticker = quote.get('ticker') or ticker
                expiry = quote.get('expiry')
                contract_desc = quote.get('contract_desc')
                close = float(quote['close'])
                prev_close = float(quote['prev_close']) if quote.get('prev_close') is not None else close
                open_price = float(quote.get('open') or close)
                day_high = float(quote.get('high') or close)
                day_low = float(quote.get('low') or close)
                change_pct = float(quote.get('change') or 0.0)
                change_abs = float(quote.get('change_abs') or 0.0)
                volume = int(quote.get('volume') or 0)
            else:
                # Fallback calculation if TV quote is unavailable
                yf_sym = data.get('yf_symbol')
                mcx_multiplier = data.get('mcx_multiplier', 1.0)
                conversion = self.usd_inr_rate * mcx_multiplier
                close = self.get_current_price(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
                if not close and symbol in PARENT_MAP:
                    close = self.get_current_price(PARENT_MAP[symbol], is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
                if not close:
                    continue
                close = round(close, 2)
                prev_close = close
                open_price = close
                day_high = close
                day_low = close
                change_pct = 0.0
                change_abs = 0.0
                volume = 0
                
            lc_init = round(prev_close * (1 - dpl_initial), 2)
            uc_init = round(prev_close * (1 + dpl_initial), 2)
            lc_max = round(prev_close * (1 - dpl_max), 2)
            uc_max = round(prev_close * (1 + dpl_max), 2)
            
            dist_to_uc_pct = round(((uc_init - close) / close) * 100, 2)
            dist_to_lc_pct = round(((close - lc_init) / close) * 100, 2)
            dist_to_uc_pts = round(uc_init - close, 2)
            dist_to_lc_pts = round(close - lc_init, 2)
            
            total_band = uc_init - lc_init
            band_pos = round(max(0, min(100, ((close - lc_init) / total_band) * 100)), 1) if total_band > 0 else 50.0
            
            if close >= uc_init:
                status = "LOCKED UPPER"
                status_color = "#ef4444"
                status_text = "Upper Circuit Locked 🚨"
            elif close <= lc_init:
                status = "LOCKED LOWER"
                status_color = "#ef4444"
                status_text = "Lower Circuit Locked 🚨"
            elif day_high >= uc_init:
                status = "HIT UPPER"
                status_color = "#ef4444"
                status_text = "Upper Circuit Hit & Reverted 🚨"
            elif day_low <= lc_init:
                status = "HIT LOWER"
                status_color = "#ef4444"
                status_text = "Lower Circuit Hit & Reverted 🚨"
            elif dist_to_uc_pct <= 1.0 or (day_high > 0 and ((uc_init - day_high) / day_high * 100 <= 1.0)):
                status = "NEAR UPPER"
                status_color = "#f59e0b"
                status_text = "Near Upper Band ⚠️"
            elif dist_to_lc_pct <= 1.0 or (day_low > 0 and ((day_low - lc_init) / day_low * 100 <= 1.0)):
                status = "NEAR LOWER"
                status_color = "#f59e0b"
                status_text = "Near Lower Band ⚠️"
            else:
                status = "NORMAL"
                status_color = "#10b981"
                status_text = "Normal Trading 🟢"
                
            circuits.append({
                "symbol": symbol,
                "name": name,
                "category": category,
                "unit": unit,
                "ticker": ticker,
                "tv_chart_symbol": f"MCX:{symbol}1!",
                "expiry": expiry,
                "contract_desc": contract_desc,
                "current_price": close,
                "prev_close": prev_close,
                "open_price": open_price,
                "day_high": day_high,
                "day_low": day_low,
                "change_pct": round(change_pct, 2),
                "change_abs": round(change_abs, 2),
                "volume": volume,
                "dpl_initial_pct": round(dpl_initial * 100, 1),
                "dpl_max_pct": round(dpl_max * 100, 1),
                "lower_circuit": lc_init,
                "upper_circuit": uc_init,
                "lower_circuit_max": lc_max,
                "upper_circuit_max": uc_max,
                "dist_to_uc_pct": dist_to_uc_pct,
                "dist_to_lc_pct": dist_to_lc_pct,
                "dist_to_uc_pts": dist_to_uc_pts,
                "dist_to_lc_pts": dist_to_lc_pts,
                "band_position_pct": band_pos,
                "status": status,
                "status_color": status_color,
                "status_text": status_text,
                "rsi_14": quote.get('rsi_14') if quote else None,
                "adx_14": quote.get('adx_14') if quote else None,
                "atr_14": quote.get('atr_14') if quote else None,
                "avg_volume_10d": quote.get('avg_volume_10d') if quote else None,
                "ema_20": quote.get('ema_20') if quote else None,
                "ema_50": quote.get('ema_50') if quote else None
            })
            
        out_data = {
            "last_updated": now_ist,
            "source": "Direct Exchange Feed (MCX / TradingView Internet)",
            "count": len(circuits),
            "commodities": circuits
        }
        
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_data.json')
        with open(output_path, 'w') as f:
            json.dump(out_data, f, indent=2)
            
        print(f"Exported {len(circuits)} commodity circuit limits to web/circuits_data.json at {now_ist}")
        self._last_circuits_data_time = now_ts
        self._cached_circuits_data = out_data

        # Automatically archive daily authentic MCX prices to build persistent 100% genuine MCX history
        try:
            today_key = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
            archive_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'mcx_daily_history.json')
            mcx_hist = {}
            if os.path.exists(archive_path):
                try:
                    with open(archive_path, 'r') as hf:
                        mcx_hist = json.load(hf)
                except Exception:
                    mcx_hist = {}
            
            day_data = mcx_hist.get(today_key, {})
            for c in circuits:
                sym = c['symbol']
                day_data[sym] = {
                    'prev_close': c.get('prev_close'),
                    'open': c.get('open_price'),
                    'high': c.get('day_high'),
                    'low': c.get('day_low'),
                    'close': c.get('current_price'),
                    'timestamp': now_ts
                }
            mcx_hist[today_key] = day_data
            with open(archive_path, 'w') as hf:
                json.dump(mcx_hist, hf, indent=2)
        except Exception as eh:
            print(f"Daily MCX archive notice: {eh}")
        
        try:
            self.generate_circuits_history_30d(tv_quotes)
        except Exception as e:
            print(f"Error generating 30d circuit history: {e}")
            
        return out_data

    def generate_circuits_history_30d(self, tv_quotes=None, force=False):
        """Identifies all circuit limit and near-circuit events across the past 30 days and computes reversal outcomes in actual Indian MCX Rupee prices."""
        try:
            now_ts = time.time()
            out_30d_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_history_30d.json')
            if not force and (now_ts - getattr(self, '_last_history_data_time', 0) < 60) and os.path.exists(out_30d_path):
                print("Circuits 30d history freshly generated (<60s ago). Skipping redundant pass.")
                return

            if not self.commodities:
                try:
                    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
                    if os.path.exists(cfg_path):
                        with open(cfg_path, 'r') as cf:
                            cfg = json.load(cf)
                            self.commodities = cfg.get('commodities', {})
                except Exception:
                    pass

            if tv_quotes is None:
                tv_quotes = self.fetch_tradingview_commodity_quotes()
                
            commodities_map = {
                'CRUDEOIL': {'name': 'Crude Oil', 'symbol': 'CL=F', 'slab': 0.04, 'unit': '1 bbl', 'category': 'Energy'},
                'NATURALGAS': {'name': 'Natural Gas', 'symbol': 'NG=F', 'slab': 0.06, 'unit': '1 mmBtu', 'category': 'Energy'},
                'GOLD': {'name': 'Gold', 'symbol': 'GC=F', 'slab': 0.03, 'unit': '10 g', 'category': 'Bullion'},
                'SILVER': {'name': 'Silver', 'symbol': 'SI=F', 'slab': 0.04, 'unit': '1 kg', 'category': 'Bullion'},
                'COPPER': {'name': 'Copper', 'symbol': 'HG=F', 'slab': 0.04, 'unit': '1 kg', 'category': 'Base Metals'},
                'ZINC': {'name': 'Zinc', 'symbol': 'ZNC=F', 'slab': 0.04, 'unit': '1 kg', 'category': 'Base Metals'},
                'ALUMINIUM': {'name': 'Aluminium', 'symbol': 'ALI=F', 'slab': 0.04, 'unit': '1 kg', 'category': 'Base Metals'},
            }
            
            events = []
            commodity_counts = {}
            cutoff_60d = datetime.now() - timedelta(days=62)
            cutoff_30d = datetime.now() - timedelta(days=32)
            today_ist_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
            
            def _fetch_commodity_history(sym, conf):
                sym_events = []
                try:
                    ticker = yf.Ticker(conf['symbol'])
                    df = ticker.history(period="75d", interval="1d")
                    if df.empty or len(df) < 5:
                        return sym_events, 0, conf['name']
                        
                    df_hourly = None
                    try:
                        df_h = ticker.history(period="60d", interval="1h")
                        if not df_h.empty:
                            df_h.index = df_h.index.tz_convert(pytz.timezone('Asia/Kolkata'))
                            df_hourly = df_h
                    except Exception as eh:
                        print(f"Hourly fetch notice for {sym}: {eh}")
                        
                    df['PrevClose'] = df['Close'].shift(1)
                    df['NextClose'] = df['Close'].shift(-1)
                    next_dates = [df.index[i + 1].strftime('%d %b %Y') if i + 1 < len(df) else None for i in range(len(df))]
                    next_days = [df.index[i + 1].strftime('%a') if i + 1 < len(df) else None for i in range(len(df))]
                    df['NextDate'] = next_dates
                    df['NextDay'] = next_days
                    
                    df = df.dropna(subset=['PrevClose'])
                    df = df[df.index >= pd.Timestamp(cutoff_60d, tz=df.index.tz)]
                    
                    slab = conf['slab']
                    near_threshold = slab * 0.80

                    # Dynamic MCX Indian Rupee reference quote & conversion multiplier
                    quote = tv_quotes.get(sym) if tv_quotes else None
                    mcx_ref_price = 0.0
                    if quote and quote.get('prev_close') and float(quote['prev_close']) > 0:
                        mcx_ref_price = float(quote['prev_close'])
                    elif quote and quote.get('close') and float(quote['close']) > 0:
                        mcx_ref_price = float(quote['close'])

                    if mcx_ref_price <= 0:
                        circuits_data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_data.json')
                        if os.path.exists(circuits_data_path):
                            try:
                                with open(circuits_data_path, 'r') as cdf:
                                    cdata = json.load(cdf)
                                    for item in cdata.get('commodities', []):
                                        if item.get('symbol') == sym:
                                            mcx_ref_price = float(item.get('prev_close') or item.get('current_price') or 0.0)
                                            break
                            except Exception:
                                pass

                    DEFAULT_MCX_REF = {
                        'CRUDEOIL': 9798.0,
                        'NATURALGAS': 293.1,
                        'GOLD': 143705.0,
                        'SILVER': 194325.0,
                        'COPPER': 1406.6,
                        'ZINC': 410.2,
                        'ALUMINIUM': 329.9
                    }
                    if mcx_ref_price <= 0:
                        mcx_ref_price = DEFAULT_MCX_REF.get(sym, 1.0)

                    last_yahoo_close = float(df['Close'].iloc[-1]) if not df.empty and df['Close'].iloc[-1] > 0 else 1.0
                    multiplier = (mcx_ref_price / last_yahoo_close) if (mcx_ref_price > 0 and last_yahoo_close > 0) else 1.0

                    # Persistent MCX daily archive: use 100% genuine MCX exchange prices for any recorded dates
                    mcx_archive_data = {}
                    archive_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'mcx_daily_history.json')
                    if os.path.exists(archive_path):
                        try:
                            with open(archive_path, 'r') as hf:
                                mcx_archive_data = json.load(hf)
                        except Exception:
                            pass
                    
                    for idx, row in df.iterrows():
                        date_key = idx.strftime('%Y-%m-%d')
                        is_today = (date_key == today_ist_str)
                        
                        # For the current in-progress day, validate strictly against live MCX exchange quote
                        if is_today:
                            if quote and quote.get('prev_close') and quote.get('low') and quote.get('high'):
                                mcx_pc = float(quote['prev_close'])
                                mcx_low = float(quote['low'])
                                mcx_high = float(quote['high'])
                                mcx_lc = round(mcx_pc * (1 - slab), 2)
                                mcx_uc = round(mcx_pc * (1 + slab), 2)
                                
                                mcx_uc_hit = bool(mcx_high >= mcx_uc or quote['close'] >= mcx_uc)
                                mcx_lc_hit = bool(mcx_low <= mcx_lc or quote['close'] <= mcx_lc)
                                mcx_near_uc = bool((mcx_high - mcx_pc) / mcx_pc >= near_threshold)
                                mcx_near_lc = bool((mcx_low - mcx_pc) / mcx_pc <= -near_threshold)
                                
                                if not mcx_uc_hit and not mcx_lc_hit and not mcx_near_uc and not mcx_near_lc:
                                    # MCX is trading normally within range today. Skip synthetic Yahoo candle.
                                    continue
                        date_str = idx.strftime('%d %b %Y')
                        day_name = idx.strftime('%a')
                        is_within_30d = bool(idx >= pd.Timestamp(cutoff_30d, tz=df.index.tz))
                        
                        # Use genuine MCX exchange prices: Live Feed (today) > Daily Archive > Dynamic MCX scaling
                        archived_mcx = mcx_archive_data.get(date_key, {}).get(sym)
                        if is_today and quote and quote.get('prev_close') and float(quote['prev_close']) > 0:
                            p_close = round(float(quote['prev_close']), 2)
                            high = round(float(quote.get('high', quote['prev_close'])), 2)
                            low = round(float(quote.get('low', quote['prev_close'])), 2)
                            close = round(float(quote.get('close', quote['prev_close'])), 2)
                        elif archived_mcx and archived_mcx.get('prev_close') and archived_mcx.get('close'):
                            p_close = round(float(archived_mcx['prev_close']), 2)
                            high = round(float(archived_mcx.get('high', archived_mcx['prev_close'])), 2)
                            low = round(float(archived_mcx.get('low', archived_mcx['prev_close'])), 2)
                            close = round(float(archived_mcx.get('close', archived_mcx['prev_close'])), 2)
                        else:
                            p_close = round(float(row['PrevClose']) * multiplier, 2)
                            high = round(float(row['High']) * multiplier, 2)
                            low = round(float(row['Low']) * multiplier, 2)
                            close = round(float(row['Close']) * multiplier, 2)
                        
                        high_move = (high - p_close) / p_close
                        low_move = (low - p_close) / p_close
                        close_move = (close - p_close) / p_close
                        
                        uc_limit = round(p_close * (1 + slab), 2)
                        lc_limit = round(p_close * (1 - slab), 2)
                        
                        # Find exact time of high / low in IST from hourly candles
                        high_time = "11:30 PM"
                        low_time = "11:30 PM"
                        high_timestamp = int(idx.timestamp())
                        low_timestamp = int(idx.timestamp())
                        
                        if df_hourly is not None and not df_hourly.empty:
                            day_h = df_hourly[df_hourly.index.strftime('%Y-%m-%d') == date_key]
                            if not day_h.empty:
                                try:
                                    h_idx = day_h['High'].idxmax()
                                    high_time = h_idx.strftime('%I:%M %p')
                                    high_timestamp = int(h_idx.timestamp())
                                except Exception:
                                    pass
                                try:
                                    l_idx = day_h['Low'].idxmin()
                                    low_time = l_idx.strftime('%I:%M %p')
                                    low_timestamp = int(l_idx.timestamp())
                                except Exception:
                                    pass
                        
                        has_next = pd.notna(row['NextClose']) and not is_today
                        next_close = round(float(row['NextClose']) * multiplier, 2) if has_next else None
                        next_date = str(row['NextDate']) if (has_next and pd.notna(row['NextDate']) and row['NextDate'] is not None and str(row['NextDate']).lower() != 'nan') else ("Session in Progress" if is_today else "--")
                        next_day = str(row['NextDay']) if (has_next and pd.notna(row['NextDay']) and row['NextDay'] is not None and str(row['NextDay']).lower() != 'nan') else "--"
                        next_move = round(((next_close - close) / close) * 100, 2) if has_next else None
                        
                        # Upper Extreme
                        if high_move >= near_threshold:
                            is_circuit_hit = bool(high_move >= slab)
                            pullback = float((high - close) / high * 100)
                            
                            # A true reversal requires that by day's end, the price reversed back inside the circuit (close < uc_limit)
                            # and stayed positive in reverse (pullback >= 0.50%).
                            # If it continued to move / close beyond the circuit limit, it broke the circuit and extended.
                            is_extended = bool(close > uc_limit)
                            if is_extended:
                                reversal_confirmed = False
                                ext_pct = round(((close - uc_limit) / p_close) * 100, 2)
                                event_type = "UPPER CIRCUIT BROKEN" if close > uc_limit else "UPPER CIRCUIT HIT"
                                badge_status = "EXTENDED"
                                rating = "Broken Circuit & Extended 🔴"
                                reaction_move_pct = ext_pct
                                reaction_text = f"+{ext_pct:.2f}% Beyond Limit (Extended)"
                            elif pullback >= 0.50 and close < uc_limit:
                                reversal_confirmed = True
                                event_type = "UPPER CIRCUIT HIT" if is_circuit_hit else "NEAR UPPER BAND"
                                badge_status = "REVERSED"
                                rating = "Strong Reversal 🟢" if pullback >= 2.0 else "Moderate Reversal 🟡"
                                reaction_move_pct = round(float(-pullback), 2)
                                reaction_text = f"-{pullback:.2f}% Pullback"
                            else:
                                reversal_confirmed = False
                                event_type = "UPPER CIRCUIT HIT" if is_circuit_hit else "NEAR UPPER BAND"
                                badge_status = "LOCKED"
                                rating = "Locked / Closed High 🔴"
                                reaction_move_pct = round(float(-pullback), 2)
                                reaction_text = f"Locked at Limit (-{pullback:.2f}%)"
                                
                            if has_next:
                                next_day_reversed = bool(next_close < close)
                                if next_move <= -1.5:
                                    next_day_rating = f"{next_move:.2f}% Pullback 🟢"
                                    next_day_badge = "Next-Day Pullback 🟢"
                                elif next_move < 0:
                                    next_day_rating = f"{next_move:.2f}% Pullback 🟢"
                                    next_day_badge = "Next-Day Pullback 🟢"
                                elif next_move == 0:
                                    next_day_rating = "0.00% Flat ⚪"
                                    next_day_badge = "Next-Day Flat ⚪"
                                else:
                                    next_day_rating = f"+{next_move:.2f}% Extension 🔴"
                                    next_day_badge = "Next-Day Extension 🔴"
                            else:
                                next_day_reversed = None
                                next_day_rating = "Session in progress ⏳"
                                next_day_badge = "Pending ⏳"
                                
                            sym_events.append({
                                'date': date_str,
                                'day': day_name,
                                'is_within_30d': is_within_30d,
                                'time': high_time,
                                'time_ist': f"{high_time} IST",
                                'datetime_str': f"{date_str}, {high_time}",
                                'timestamp': high_timestamp,
                                'symbol': sym,
                                'name': conf['name'],
                                'category': conf['category'],
                                'unit': conf['unit'],
                                'direction': 'UPPER',
                                'event_type': event_type,
                                'is_circuit_hit': is_circuit_hit,
                                'is_extended': is_extended,
                                'slab_pct': round(float(slab * 100), 1),
                                'prev_close': round(float(p_close), 2),
                                'circuit_limit': float(uc_limit),
                                'extreme_hit': round(float(high), 2),
                                'extreme_move_pct': round(float(high_move * 100), 2),
                                'close_price': round(float(close), 2),
                                'close_move_pct': round(float(close_move * 100), 2),
                                'reaction_move_pct': reaction_move_pct,
                                'reaction_text': reaction_text,
                                'reversal_confirmed': reversal_confirmed,
                                'badge_status': badge_status,
                                'rating': rating,
                                # Next-day metrics
                                'next_date': next_date,
                                'next_day': next_day,
                                'next_close': next_close,
                                'next_day_move_pct': next_move,
                                'next_day_reversed': next_day_reversed,
                                'next_day_badge': next_day_badge,
                                'next_day_rating': next_day_rating
                            })
                            
                        # Lower Extreme
                        if low_move <= -near_threshold:
                            is_circuit_hit = bool(low_move <= -slab)
                            bounce = float((close - low) / low * 100)
                            
                            # A true reversal requires that by day's end, the price reversed back inside the circuit (close > lc_limit)
                            # and stayed positive in reverse (bounce >= 0.50%).
                            # If it continued to move / close beyond the circuit floor, it broke the circuit and extended.
                            is_extended = bool(close < lc_limit)
                            if is_extended:
                                reversal_confirmed = False
                                ext_pct = round(((lc_limit - close) / p_close) * 100, 2)
                                event_type = "LOWER CIRCUIT BROKEN" if close < lc_limit else "LOWER CIRCUIT HIT"
                                badge_status = "EXTENDED"
                                rating = "Broken Circuit & Extended 🔴"
                                reaction_move_pct = -ext_pct
                                reaction_text = f"-{ext_pct:.2f}% Beyond Floor (Extended)"
                            elif bounce >= 0.50 and close > lc_limit:
                                reversal_confirmed = True
                                event_type = "LOWER CIRCUIT HIT" if is_circuit_hit else "NEAR LOWER BAND"
                                badge_status = "REVERSED"
                                rating = "Strong Reversal 🟢" if bounce >= 2.0 else "Moderate Reversal 🟡"
                                reaction_move_pct = round(float(bounce), 2)
                                reaction_text = f"+{bounce:.2f}% Bounce"
                            else:
                                reversal_confirmed = False
                                event_type = "LOWER CIRCUIT HIT" if is_circuit_hit else "NEAR LOWER BAND"
                                badge_status = "LOCKED"
                                rating = "Locked / Closed Floor 🔴"
                                reaction_move_pct = round(float(bounce), 2)
                                reaction_text = f"Locked at Floor (+{bounce:.2f}%)"
                                
                            if has_next:
                                next_day_reversed = bool(next_close > close)
                                if next_move >= 1.5:
                                    next_day_rating = f"+{next_move:.2f}% Bounce 🟢"
                                    next_day_badge = "Next-Day Bounce 🟢"
                                elif next_move > 0:
                                    next_day_rating = f"+{next_move:.2f}% Bounce 🟢"
                                    next_day_badge = "Next-Day Bounce 🟢"
                                elif next_move == 0:
                                    next_day_rating = "0.00% Flat ⚪"
                                    next_day_badge = "Next-Day Flat ⚪"
                                else:
                                    next_day_rating = f"{next_move:.2f}% Extension 🔴"
                                    next_day_badge = "Next-Day Extension 🔴"
                            else:
                                next_day_reversed = None
                                next_day_rating = "Session in progress ⏳"
                                next_day_badge = "Pending ⏳"
                                
                            sym_events.append({
                                'date': date_str,
                                'day': day_name,
                                'is_within_30d': is_within_30d,
                                'time': low_time,
                                'time_ist': f"{low_time} IST",
                                'datetime_str': f"{date_str}, {low_time}",
                                'timestamp': low_timestamp,
                                'symbol': sym,
                                'name': conf['name'],
                                'category': conf['category'],
                                'unit': conf['unit'],
                                'direction': 'LOWER',
                                'event_type': event_type,
                                'is_circuit_hit': is_circuit_hit,
                                'is_extended': is_extended,
                                'slab_pct': round(float(slab * 100), 1),
                                'prev_close': round(float(p_close), 2),
                                'circuit_limit': float(lc_limit),
                                'extreme_hit': round(float(low), 2),
                                'extreme_move_pct': round(float(low_move * 100), 2),
                                'close_price': round(float(close), 2),
                                'close_move_pct': round(float(close_move * 100), 2),
                                'reaction_move_pct': reaction_move_pct,
                                'reaction_text': reaction_text,
                                'reversal_confirmed': reversal_confirmed,
                                'badge_status': badge_status,
                                'rating': rating,
                                # Next-day metrics
                                'next_date': next_date,
                                'next_day': next_day,
                                'next_close': next_close,
                                'next_day_move_pct': next_move,
                                'next_day_reversed': next_day_reversed,
                                'next_day_badge': next_day_badge,
                                'next_day_rating': next_day_rating
                            })
                except Exception as e:
                    print(f"Error fetching history for {sym}: {e}")
                return sym_events, len(sym_events), conf['name']

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(_fetch_commodity_history, sym, conf)
                    for sym, conf in commodities_map.items()
                ]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        sym_evs, ev_count, c_name = future.result()
                        events.extend(sym_evs)
                        commodity_counts[c_name] = ev_count
                    except Exception as e:
                        print(f"Error processing commodity history: {e}")
                    
            events.sort(key=lambda x: x['timestamp'], reverse=True)
            
            def compute_summary_stats(subset):
                if not subset:
                    return {
                        'total_events': 0, 'reversals_count': 0, 'locked_count': 0, 'extended_count': 0,
                        'reversal_win_rate_pct': 0.0, 'avg_reaction_pct': 0.0,
                        'next_day_completed_events': 0, 'next_day_reversals_count': 0,
                        'next_day_win_rate_pct': 0.0, 'avg_next_day_move_pct': 0.0,
                        'most_active_commodity': '--'
                    }
                t = len(subset)
                r = sum(1 for e in subset if e['reversal_confirmed'])
                ext = sum(1 for e in subset if e.get('is_extended'))
                l = t - r
                w = round(r / t * 100, 1) if t > 0 else 0.0
                avg_r = round(sum(abs(e['reaction_move_pct']) for e in subset) / t, 2) if t > 0 else 0.0
                next_c = [e for e in subset if e.get('next_day_reversed') is not None]
                next_r = sum(1 for e in next_c if e['next_day_reversed'])
                next_w = round(next_r / len(next_c) * 100, 1) if next_c else 0.0
                avg_nm = round(sum(abs(e['next_day_move_pct']) for e in next_c) / len(next_c), 2) if next_c else 0.0
                c_counts = {}
                for e in subset:
                    c_counts[e['name']] = c_counts.get(e['name'], 0) + 1
                most = max(c_counts.items(), key=lambda x: x[1]) if c_counts else ("--", 0)
                return {
                    'total_events': t,
                    'reversals_count': r,
                    'locked_count': l,
                    'extended_count': ext,
                    'reversal_win_rate_pct': w,
                    'avg_reaction_pct': avg_r,
                    'next_day_completed_events': len(next_c),
                    'next_day_reversals_count': next_r,
                    'next_day_win_rate_pct': next_w,
                    'avg_next_day_move_pct': avg_nm,
                    'most_active_commodity': f"{most[0]} ({most[1]} events)"
                }

            events_30d = [e for e in events if e.get('is_within_30d')]
            summary_30d = compute_summary_stats(events_30d)
            summary_60d = compute_summary_stats(events)
            now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M:%S %p IST')
            
            def _clean_nan(v):
                if isinstance(v, float):
                    if math.isnan(v) or math.isinf(v):
                        return None
                    return v
                elif isinstance(v, dict):
                    return {k: _clean_nan(val) for k, val in v.items()}
                elif isinstance(v, list):
                    return [_clean_nan(val) for val in v]
                elif isinstance(v, str) and v.lower() == 'nan':
                    return None
                return v

            data_payload = _clean_nan({
                'last_updated': now_ist,
                'period_days': 60,
                'summary': summary_30d, # default summary
                'summary_30d': summary_30d,
                'summary_60d': summary_60d,
                'events_30d_count': len(events_30d),
                'events_60d_count': len(events),
                'events': events
            })
            
            out_30d_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_history_30d.json')
            with open(out_30d_path, 'w') as f:
                json.dump(data_payload, f, indent=2, allow_nan=False)
                
            out_60d_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_history_60d.json')
            with open(out_60d_path, 'w') as f:
                json.dump(data_payload, f, indent=2, allow_nan=False)
                
            print(f"Exported {len(events)} events (60D) & {len(events_30d)} events (30D) to web/circuits_history_30d.json and circuits_history_60d.json")
            self._last_history_data_time = now_ts
            return data_payload
        except Exception as e:
            print(f"Error in generate_circuits_history_30d: {e}")
            return None

