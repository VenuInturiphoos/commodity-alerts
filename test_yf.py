import yfinance as yf
ticker = yf.Ticker("GC=F")
hist = ticker.history(period="1d")
print("Gold YF:", hist['Close'].iloc[-1])
inr = yf.Ticker("INR=X")
print("INR:", inr.history(period="1d")['Close'].iloc[-1])
