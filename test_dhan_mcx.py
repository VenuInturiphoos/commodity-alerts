import json, os
from price_checker import PriceChecker
with open('config.json') as f:
    config = json.load(f)
checker = PriceChecker(config)
sec_id = checker.get_dhan_mcx_near_month("GOLD")
print(f"Sec ID for GOLD: {sec_id}")
if sec_id:
    minute = checker.dhan.intraday_minute_data(
        security_id=str(sec_id),
        exchange_segment="MCX_COMM",
        instrument_type="FUTCOM"
    )
    print(f"Intraday Minute Data: {minute}")
    
    hist = checker.dhan.historical_daily_data(
        security_id=str(sec_id),
        exchange_segment="MCX_COMM",
        instrument_type="FUTCOM",
        expiry_code=0,
        from_date="2026-08-01",
        to_date="2026-08-15"
    )
    print(f"Historical Data: {hist}")
