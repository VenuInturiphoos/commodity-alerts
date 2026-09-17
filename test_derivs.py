import json
import yfinance as yf

config = json.load(open('config.json'))
derivatives = config.get('derivatives', {})

for base_symbol, config_data in derivatives.items():
    print(f"Checking {base_symbol}")
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
    
    if spot_price and config_data.get('track_futures'):
        print(f"Would append {base_symbol}-FUT at price {spot_price}")
    else:
        print(f"Failed. spot_price: {spot_price}, track_futures: {config_data.get('track_futures')}")
