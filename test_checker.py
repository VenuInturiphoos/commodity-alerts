import json
from price_checker import PriceChecker
with open('config.json') as f:
    config = json.load(f)
checker = PriceChecker(config)
for symbol, data in config['commodities'].items():
    yf_sym = data.get('yf_symbol')
    mcx_mult = data.get('mcx_multiplier', 1.0)
    conversion = checker.usd_inr_rate * mcx_mult
    levels = checker.get_support_resistance_levels(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
    price = checker.get_current_price(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
    print(f"{symbol} -> price: {price}, levels: {levels}")
