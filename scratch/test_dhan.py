import pandas as pd
from dhanhq import dhanhq

client_id = "1106185635"
access_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzg3MjIzOTk2LCJpYXQiOjE3ODcxMzc1OTYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTA2MTg1NjM1In0.6jwD9J5LydAIA8q090zsb9vqcwxJ3ZhNAiMhuo-WIdfen11iNsph1ydMv0JN_HYvfwWOCh8ENoNqzaQ5GsqmOw"

dhan = dhanhq(client_id, access_token)

print("Fetching instrument master...")
df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv')

# Find RELIANCE
instrument = df[(df['SEM_EXM_EXCH_ID'] == 'NSE') & (df['SEM_CUSTOM_SYMBOL'] == 'RELIANCE')]
if not instrument.empty:
    sec_id = str(instrument['SEM_SM_SECURITY_ID'].values[0])
    print(f"RELIANCE Security ID: {sec_id}")
    
    # Try fetching daily historical data
    print("Fetching historical data...")
    data = dhan.historical_daily_data(
        security_id=sec_id,
        exchange_segment="NSE_EQ",
        instrument_type="EQUITY",
        expiry_code=0,
        from_date="2024-01-01",
        to_date="2024-01-10"
    )
    print(data)
else:
    print("Could not find RELIANCE in master CSV.")
