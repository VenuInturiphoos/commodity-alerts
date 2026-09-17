import yfinance as yf
import json

ticker = yf.Ticker('RELIANCE.NS')
info = ticker.info

keys_of_interest = ['trailingEps', 'forwardEps', 'earningsGrowth', 'revenueGrowth', 'trailingPE', 'bookValue', 'priceToBook']
data = {k: info.get(k) for k in keys_of_interest}

print(json.dumps(data, indent=2))
