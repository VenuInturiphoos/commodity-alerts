import yfinance as yf
import requests

class PriceChecker:
    def __init__(self, config):
        self.config = config
        self.commodities = config.get('commodities', {})
        self.stocks = config.get('stocks', {})
        self.usd_inr_rate = self.get_usd_inr_rate()
        
        # Supabase config for pushing market data
        self.supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/market_data'
        self.supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'

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

    def get_support_resistance_levels(self, ticker_symbol, conversion_rate=1.0):
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
            print(f"Error calculating levels for {ticker_symbol}: {e}")
            return None

    def get_current_price(self, ticker_symbol, conversion_rate=1.0):
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                price = hist['Close'].iloc[-1]
                return price * conversion_rate
            return None
        except Exception as e:
            print(f"Error fetching current price for {ticker_symbol}: {e}")
            return None

    def evaluate_levels(self, name, symbol, levels, current_price):
        alerts = []
        alert_status = None
        
        if levels is None or current_price is None:
            print(f"Could not fetch data for {name}. Skipping.")
            return alerts, alert_status
            
        print(f"Current Price: ₹{current_price:.2f}")
        print(f"Levels -> R2: ₹{levels['R2']:.2f}, R1: ₹{levels['R1']:.2f}, P: ₹{levels['Pivot']:.2f}, S1: ₹{levels['S1']:.2f}, S2: ₹{levels['S2']:.2f}")
        
        # If price is within 0.2% of a level, trigger an alert
        threshold_pct = 0.002 
        
        # Check resistances
        for level_name in ['R1', 'R2']:
            level_price = levels[level_name]
            if abs(current_price - level_price) / level_price <= threshold_pct:
                alert_msg = f"testing Resistance {level_name}"
                alerts.append(f"ALERT: {name} ({symbol}) is {alert_msg} at ₹{level_price:.2f}. Current price: ₹{current_price:.2f}.")
                alert_status = alert_msg
                
        # Check supports
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
        
        # 1. Check Commodities (Needs USD-INR conversion)
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
            
        # 2. Check Stocks (Natively in INR, conversion = 1.0)
        for symbol, data in self.stocks.items():
            name = data['name']
            
            print(f"\nChecking Stock {name} ({symbol})...")
            # Stock prices are natively in INR, so conversion multiplier is 1.0
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

        # Sync all latest data to Supabase
        if market_data_payload:
            self.sync_to_supabase(market_data_payload)

        return alerts
