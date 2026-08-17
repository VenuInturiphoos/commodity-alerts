import yfinance as yf

class PriceChecker:
    def __init__(self, config):
        self.commodities = config['commodities']

    def get_support_resistance_levels(self, ticker_symbol):
        """
        Calculates Standard Pivot Points to find Support and Resistance levels.
        We use the previous day's High, Low, and Close.
        Pivot Point (P) = (High + Low + Close) / 3
        Resistance 1 (R1) = (P x 2) - Low
        Resistance 2 (R2) = P + (High - Low)
        Support 1 (S1) = (P x 2) - High
        Support 2 (S2) = P - (High - Low)
        """
        try:
            ticker = yf.Ticker(ticker_symbol)
            # Get historical data for the last 5 days
            hist = ticker.history(period="5d")
            
            if len(hist) < 2:
                return None
                
            # Get the previous day's data
            prev_day = hist.iloc[-2]
            
            high = prev_day['High']
            low = prev_day['Low']
            close = prev_day['Close']
            
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

    def get_current_price(self, ticker_symbol):
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1d")
            if len(hist) > 0:
                return hist['Close'].iloc[-1]
            return None
        except Exception as e:
            print(f"Error fetching current price for {ticker_symbol}: {e}")
            return None
            
    def check_alerts(self):
        alerts = []
        for symbol, data in self.commodities.items():
            name = data['name']
            print(f"\nChecking {name} ({symbol})...")
            
            levels = self.get_support_resistance_levels(symbol)
            current_price = self.get_current_price(symbol)
            
            if levels is None or current_price is None:
                print(f"Could not fetch data for {name}. Skipping.")
                continue
                
            print(f"Current Price: {current_price:.2f}")
            print(f"Levels -> R2: {levels['R2']:.2f}, R1: {levels['R1']:.2f}, P: {levels['Pivot']:.2f}, S1: {levels['S1']:.2f}, S2: {levels['S2']:.2f}")
            
            # If price is within 0.2% of a level, trigger an alert
            threshold_pct = 0.002 
            
            # Check resistances
            for level_name in ['R1', 'R2']:
                level_price = levels[level_name]
                if abs(current_price - level_price) / level_price <= threshold_pct:
                    alerts.append(f"ALERT: {name} ({symbol}) is testing Resistance Level {level_name} at {level_price:.2f}. Current price: {current_price:.2f}.")
                    
            # Check supports
            for level_name in ['S1', 'S2']:
                level_price = levels[level_name]
                if abs(current_price - level_price) / level_price <= threshold_pct:
                    alerts.append(f"ALERT: {name} ({symbol}) is testing Support Level {level_name} at {level_price:.2f}. Current price: {current_price:.2f}.")

        return alerts
