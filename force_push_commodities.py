import json, os
from supabase import create_client, Client
from price_checker import PriceChecker

# Need to load env vars manually if not in env
from dotenv import load_dotenv
load_dotenv()

with open('config.json') as f:
    config = json.load(f)
checker = PriceChecker(config)
payload = []
for symbol, data in config['commodities'].items():
    name = data['name']
    yf_sym = data.get('yf_symbol')
    mcx_mult = data.get('mcx_multiplier', 1.0)
    conversion = checker.usd_inr_rate * mcx_mult
    levels = checker.get_support_resistance_levels(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
    price = checker.get_current_price(symbol, is_commodity=True, fallback_multiplier=conversion, yf_symbol=yf_sym)
    
    if levels and price:
        new_alerts, alert_status = checker.evaluate_levels(name, symbol, levels, price)
        payload.append({
            "symbol": symbol,
            "name": name,
            "asset_type": "Commodity",
            "current_price": round(price, 2),
            "r1": round(levels['R1'], 2),
            "r2": round(levels['R2'], 2),
            "s1": round(levels['S1'], 2),
            "s2": round(levels['S2'], 2),
            "pivot": round(levels['Pivot'], 2),
            "alert_status": alert_status
        })

if payload:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    supabase: Client = create_client(url, key)
    supabase.table('market_data').upsert(payload).execute()
    print("Successfully pushed commodities!")
else:
    print("No payload generated.")
