import yfinance as yf
import pandas as pd

ticker = "CL=F"
symbol = "CRUDEOIL"
mcx_multiplier = 1.001215

hist = yf.Ticker(ticker).history(period="max")
current_price = hist['Close'].iloc[-1] * mcx_multiplier
all_time_low = hist['Low'].min() * mcx_multiplier
all_time_high = hist['High'].max() * mcx_multiplier

print(f"Current: {current_price}")
print(f"All Time Low: {all_time_low}")
print(f"All Time High: {all_time_high}")
print(f"Low Alert condition (AllTimeLow): abs({current_price} - {all_time_low}) / {all_time_low} = {abs(current_price - all_time_low) / all_time_low if all_time_low else 'N/A'}")

