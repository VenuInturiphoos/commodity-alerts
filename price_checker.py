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

        # 3. Fetch all-time yfinance data for multi-timeframe extremes and fallback Pivot/R/S
        ticker_symbol = yf_symbol if yf_symbol else ticker_symbol
        try:
            print(f"Fetching yfinance period='max' for extremes for {ticker_symbol}...")
            ticker = yf.Ticker(ticker_symbol)
            try:
                hist = ticker.history(period="max")
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
                print(f"Using yfinance for Pivot/R/S fallback for {ticker_symbol}...")
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
            "COPPER": "HG=F",
            "ALUMINIUM": "ALI=F",
            "ZINC": "ZNC=F",
            "ELECTRICITY": "HP=F"
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

    def fetch_tradingview_commodity_quotes(self):
        """Fetches live MCX continuous futures quotes directly from TradingView futures scanner."""
        try:
            tickers = []
            symbol_to_ticker = {}
            for sym, data in self.commodities.items():
                tv_tick = data.get('tv_ticker')
                if tv_tick:
                    tickers.append(tv_tick)
                    symbol_to_ticker[tv_tick] = sym
            
            if not tickers:
                return {}
                
            url = 'https://scanner.tradingview.com/futures/scan'
            payload = {
                'symbols': {'tickers': tickers},
                'columns': ['close', 'open', 'high', 'low', 'change', 'change_abs', 'volume', 'description']
            }
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code != 200:
                print(f"TradingView scanner returned status {resp.status_code}")
                return {}
                
            res_data = resp.json().get('data', [])
            quotes = {}
            cols = payload['columns']
            for row in res_data:
                tick = row.get('s')
                sym = symbol_to_ticker.get(tick)
                if sym:
                    d = dict(zip(cols, row.get('d', [])))
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
                        'description': d.get('description')
                    }
            return quotes
        except Exception as e:
            print(f"Error fetching TradingView commodity quotes: {e}")
            return {}

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
                elif symbol == "ALUMINIUM":
                    conversion = self.usd_inr_rate / 1000
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
            
            # Check Commodity Upper/Lower Circuit Limits
            dpl_initial = data.get('dpl_initial', 0.04)
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
                uc_limit = round(prev_close_ref * (1 + dpl_initial), 2)
                lc_limit = round(prev_close_ref * (1 - dpl_initial), 2)
                
                is_uc_hit = bool(current_price >= uc_limit or day_high_ref >= uc_limit)
                is_lc_hit = bool(current_price <= lc_limit or day_low_ref <= lc_limit)
                
                if is_uc_hit or is_lc_hit:
                    event_type = "UPPER CIRCUIT HIT" if is_uc_hit else "LOWER CIRCUIT HIT"
                    circuit_price = uc_limit if is_uc_hit else lc_limit
                    extreme_price = day_high_ref if is_uc_hit else day_low_ref
                    move_from_anchor = ((current_price - prev_close_ref) / prev_close_ref) * 100
                    today_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
                    
                    last_c_date = previous_state.get(symbol, {}).get('last_alert_date')
                    last_c_msg = previous_state.get(symbol, {}).get('last_alert_msg')
                    
                    if last_c_date == today_ist and last_c_msg == event_type:
                        print(f"Skipping duplicate circuit alert for {name} ({symbol}): already alerted today.")
                    else:
                        alert_subj = f"⚡ MCX CIRCUIT HIT: {name} reached {event_type} (₹{circuit_price:.2f})!"
                        alert_body = (
                            f"⚡ MCX DAILY CIRCUIT LIMIT (DPL) HIT!\n\n"
                            f"Commodity: {name} ({symbol})\n"
                            f"Event: {event_type}\n"
                            f"Live Price: ₹{current_price:.2f}\n"
                            f"Day Extreme: ₹{extreme_price:.2f}\n"
                            f"Circuit Limit Price: ₹{circuit_price:.2f} ({'+' if is_uc_hit else '-'}{dpl_initial*100:.1f}% DPL)\n"
                            f"Previous Close (Anchor): ₹{prev_close_ref:.2f}\n"
                            f"Day Move: {move_from_anchor:+.2f}%\n\n"
                            f"Exchange Status: Trading reached daily permissible limit.\n"
                            f"Historical Pattern: Check 30-day mean reversion bounce/pullback rates.\n\n"
                            f"Live Dashboard: https://VenuInturiphoos.github.io/commodity-alerts/index.html#circuits"
                        )
                        alerts.append({'subject': alert_subj, 'body': alert_body})
                        alert_status = event_type
                        last_alert_date = today_ist
                        last_alert_msg = event_type
                        print(f"🚨 [CIRCUIT ALERT GENERATED] {alert_subj}")
            
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
            
            # Note: Non-circuit alerts excluded per user request. No alerts generated for stocks.
            new_alerts, alert_status, last_alert_date, last_alert_msg, current_signal = self.evaluate_levels(name, symbol, levels, current_price, previous_state, is_commodity=False)
            
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
    def generate_near_alerts_json(self):
        import json, os
        near_buy = []
        near_sell = []
        
        # Check Commodities
        for symbol, data in self.commodities.items():
            name = data['name']
            levels = self.get_support_resistance_levels(symbol, is_commodity=True, yf_symbol=data.get('yf_symbol'))
            if levels and levels.get('RSI_14'):
                rsi = levels['RSI_14']
                if rsi < 45:
                    near_buy.append({'symbol': symbol, 'name': name, 'rsi': round(rsi, 2)})
                elif rsi > 55:
                    near_sell.append({'symbol': symbol, 'name': name, 'rsi': round(rsi, 2)})

        # Check Stocks
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
        if tv_quotes is None:
            tv_quotes = self.fetch_tradingview_commodity_quotes()
            
        circuits = []
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M:%S %p IST')
        
        for symbol, data in self.commodities.items():
            name = data.get('name', symbol)
            dpl_initial = data.get('dpl_initial', 0.04)
            dpl_max = data.get('dpl_max', 0.06)
            category = data.get('category', 'Commodities')
            unit = data.get('unit', '')
            ticker = data.get('tv_ticker', '')
            
            quote = tv_quotes.get(symbol) if tv_quotes else None
            if quote and quote.get('close') is not None:
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
            elif dist_to_uc_pct <= 1.0:
                status = "NEAR UPPER"
                status_color = "#f59e0b"
                status_text = "Near Upper Band ⚠️"
            elif dist_to_lc_pct <= 1.0:
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
                "status_text": status_text
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
        
        try:
            self.generate_circuits_history_30d()
        except Exception as e:
            print(f"Error generating 30d circuit history: {e}")
            
        return out_data

    def generate_circuits_history_30d(self):
        """Identifies all circuit limit and near-circuit events across the past 30 days and computes reversal outcomes."""
        try:
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
            cutoff_date = datetime.now() - timedelta(days=32)
            
            for sym, conf in commodities_map.items():
                commodity_counts[conf['name']] = 0
                try:
                    ticker = yf.Ticker(conf['symbol'])
                    df = ticker.history(period="60d", interval="1d")
                    if df.empty or len(df) < 5:
                        continue
                        
                    df['PrevClose'] = df['Close'].shift(1)
                    df['NextClose'] = df['Close'].shift(-1)
                    next_dates = [df.index[i + 1].strftime('%d %b %Y') if i + 1 < len(df) else None for i in range(len(df))]
                    next_days = [df.index[i + 1].strftime('%a') if i + 1 < len(df) else None for i in range(len(df))]
                    df['NextDate'] = next_dates
                    df['NextDay'] = next_days
                    
                    df = df.dropna(subset=['PrevClose'])
                    df = df[df.index >= pd.Timestamp(cutoff_date, tz=df.index.tz)]
                    
                    slab = conf['slab']
                    near_threshold = slab * 0.80
                    
                    for idx, row in df.iterrows():
                        date_str = idx.strftime('%d %b %Y')
                        day_name = idx.strftime('%a')
                        p_close = row['PrevClose']
                        high = row['High']
                        low = row['Low']
                        close = row['Close']
                        
                        high_move = (high - p_close) / p_close
                        low_move = (low - p_close) / p_close
                        close_move = (close - p_close) / p_close
                        
                        uc_limit = round(p_close * (1 + slab), 2)
                        lc_limit = round(p_close * (1 - slab), 2)
                        
                        has_next = pd.notna(row['NextClose'])
                        next_close = round(float(row['NextClose']), 2) if has_next else None
                        next_date = row['NextDate'] or "Next Session"
                        next_day = row['NextDay'] or "--"
                        next_move = round(((next_close - close) / close) * 100, 2) if has_next else None
                        
                        # Upper Extreme
                        if high_move >= near_threshold:
                            is_circuit_hit = bool(high_move >= slab)
                            pullback = float((high - close) / high * 100)
                            reversal_confirmed = bool(pullback >= 0.50)
                            
                            event_type = "UPPER CIRCUIT HIT" if is_circuit_hit else "NEAR UPPER BAND"
                            badge_status = "REVERSED" if reversal_confirmed else "LOCKED/CLOSED HIGH"
                            
                            if pullback >= 2.0:
                                rating = "Strong Reversal 🟢"
                            elif pullback >= 0.5:
                                rating = "Moderate Reversal 🟡"
                            else:
                                rating = "Locked / Closed High 🔴"
                                
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
                                
                            commodity_counts[conf['name']] += 1
                            events.append({
                                'date': date_str,
                                'day': day_name,
                                'timestamp': int(idx.timestamp()),
                                'symbol': sym,
                                'name': conf['name'],
                                'category': conf['category'],
                                'unit': conf['unit'],
                                'direction': 'UPPER',
                                'event_type': event_type,
                                'is_circuit_hit': is_circuit_hit,
                                'slab_pct': round(float(slab * 100), 1),
                                'prev_close': round(float(p_close), 2),
                                'circuit_limit': float(uc_limit),
                                'extreme_hit': round(float(high), 2),
                                'extreme_move_pct': round(float(high_move * 100), 2),
                                'close_price': round(float(close), 2),
                                'close_move_pct': round(float(close_move * 100), 2),
                                'reaction_move_pct': round(float(-pullback), 2),
                                'reaction_text': f"-{pullback:.2f}% Pullback",
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
                            reversal_confirmed = bool(bounce >= 0.50)
                            
                            event_type = "LOWER CIRCUIT HIT" if is_circuit_hit else "NEAR LOWER BAND"
                            badge_status = "REVERSED" if reversal_confirmed else "LOCKED/CLOSED LOW"
                            
                            if bounce >= 2.0:
                                rating = "Strong Reversal 🟢"
                            elif bounce >= 0.5:
                                rating = "Moderate Reversal 🟡"
                            else:
                                rating = "Locked / Closed Floor 🔴"
                                
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
                                
                            commodity_counts[conf['name']] += 1
                            events.append({
                                'date': date_str,
                                'day': day_name,
                                'timestamp': int(idx.timestamp()),
                                'symbol': sym,
                                'name': conf['name'],
                                'category': conf['category'],
                                'unit': conf['unit'],
                                'direction': 'LOWER',
                                'event_type': event_type,
                                'is_circuit_hit': is_circuit_hit,
                                'slab_pct': round(float(slab * 100), 1),
                                'prev_close': round(float(p_close), 2),
                                'circuit_limit': float(lc_limit),
                                'extreme_hit': round(float(low), 2),
                                'extreme_move_pct': round(float(low_move * 100), 2),
                                'close_price': round(float(close), 2),
                                'close_move_pct': round(float(close_move * 100), 2),
                                'reaction_move_pct': round(float(bounce), 2),
                                'reaction_text': f"+{bounce:.2f}% Bounce",
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
                    
            events.sort(key=lambda x: x['timestamp'], reverse=True)
            
            total_events = len(events)
            reversals = sum(1 for e in events if e['reversal_confirmed'])
            locked = total_events - reversals
            win_rate = round(reversals / total_events * 100, 1) if total_events > 0 else 0.0
            avg_reaction = round(sum(abs(e['reaction_move_pct']) for e in events) / total_events, 2) if total_events > 0 else 0.0
            
            next_day_completed = [e for e in events if e.get('next_day_reversed') is not None]
            next_day_reversals = sum(1 for e in next_day_completed if e['next_day_reversed'])
            next_day_win_rate = round(next_day_reversals / len(next_day_completed) * 100, 1) if next_day_completed else 0.0
            avg_next_day_move = round(sum(abs(e['next_day_move_pct']) for e in next_day_completed) / len(next_day_completed), 2) if next_day_completed else 0.0
            
            most_active = max(commodity_counts.items(), key=lambda x: x[1]) if commodity_counts else ("Crude Oil", 0)
            now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M:%S %p IST')
            
            data_payload = {
                'last_updated': now_ist,
                'period_days': 30,
                'summary': {
                    'total_events': total_events,
                    'reversals_count': reversals,
                    'locked_count': locked,
                    'reversal_win_rate_pct': win_rate,
                    'avg_reaction_pct': avg_reaction,
                    'next_day_completed_events': len(next_day_completed),
                    'next_day_reversals_count': next_day_reversals,
                    'next_day_win_rate_pct': next_day_win_rate,
                    'avg_next_day_move_pct': avg_next_day_move,
                    'most_active_commodity': f"{most_active[0]} ({most_active[1]} events)"
                },
                'events': events
            }
            
            out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_history_30d.json')
            with open(out_path, 'w') as f:
                json.dump(data_payload, f, indent=2)
                
            print(f"Exported {total_events} 30-day circuit events to web/circuits_history_30d.json")
            return data_payload
        except Exception as e:
            print(f"Error in generate_circuits_history_30d: {e}")
            return None

