import yfinance as yf

class PriceChecker:
    def __init__(self, config):
        self.commodities = config['commodities']
        self.usd_inr_rate = self.get_usd_inr_rate()

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

    def get_support_resistance_levels(self, ticker_symbol, multiplier=1.0):
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="5d")
            
            if len(hist) < 2:
                return None
                
            prev_day = hist.iloc[-2]
            
            # Apply the currency and contract multiplier to the base data
            conversion = self.usd_inr_rate * multiplier
            
            high = prev_day['High'] * conversion
            low = prev_day['Low'] * conversion
            close = prev_day['Close'] * conversion
            
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

    def get_current_price(self, ticker_symbol, multiplier=1.0):
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                price = hist['Close'].iloc[-1]
                return price * self.usd_inr_rate * multiplier
            return None
        except Exception as e:
            print(f"Error fetching current price for {ticker_symbol}: {e}")
            return None
            
    def check_alerts(self):
        alerts = []
        for symbol, data in self.commodities.items():
            name = data['name']
            # Default to 1.0 if not specified in config
            mcx_multiplier = data.get('mcx_multiplier', 1.0)
            
            print(f"\nChecking {name} ({symbol}) in INR...")
            
            levels = self.get_support_resistance_levels(symbol, mcx_multiplier)
            current_price = self.get_current_price(symbol, mcx_multiplier)
            
            if levels is None or current_price is None:
                print(f"Could not fetch data for {name}. Skipping.")
                continue
                
            print(f"Current Price: ₹{current_price:.2f}")
            print(f"Levels -> R2: ₹{levels['R2']:.2f}, R1: ₹{levels['R1']:.2f}, P: ₹{levels['Pivot']:.2f}, S1: ₹{levels['S1']:.2f}, S2: ₹{levels['S2']:.2f}")
            
            # If price is within 0.2% of a level, trigger an alert
            threshold_pct = 0.002 
            
            # Check resistances
            for level_name in ['R1', 'R2']:
                level_price = levels[level_name]
                if abs(current_price - level_price) / level_price <= threshold_pct:
                    alerts.append(f"ALERT: {name} is testing Resistance Level {level_name} at ₹{level_price:.2f}. Current price: ₹{current_price:.2f}.")
                    
            # Check supports
            for level_name in ['S1', 'S2']:
                level_price = levels[level_name]
                if abs(current_price - level_price) / level_price <= threshold_pct:
                    alerts.append(f"ALERT: {name} is testing Support Level {level_name} at ₹{level_price:.2f}. Current price: ₹{current_price:.2f}.")

        return alerts
