import yfinance as yf
ticker = yf.Ticker('INR=X')
hist = ticker.history(period="1d")
print("INR=X Close:", hist['Close'].iloc[-1])
