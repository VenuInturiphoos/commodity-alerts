import yfinance as yf

def test_ticker(symbol):
    try:
        t = yf.Ticker(symbol)
        h = t.history(period='1d')
        print(f"{symbol}: {'SUCCESS - ' + str(h['Close'].iloc[-1]) if not h.empty else 'EMPTY'}")
    except Exception as e:
        print(f"{symbol}: FAILED - {e}")

test_ticker('CRUDEOIL.MCX')
test_ticker('CRUDEOIL24SEPFUT.MCX')
test_ticker('NIFTY_F1.NS')
test_ticker('BANKNIFTY_F1.NS')
test_ticker('CL=F') # NYMEX Crude Oil Future
test_ticker('GC=F') # COMEX Gold Future
test_ticker('SI=F') # COMEX Silver Future
test_ticker('NG=F') # NYMEX Natural Gas Future
