import json
import pandas as pd

with open('config.json') as f:
    config = json.load(f)
stocks = list(config.get('stocks', {}).keys())
stock_symbols = [s.replace('.NS', '').replace('.BO', '') for s in stocks]

df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', usecols=['SEM_EXM_EXCH_ID', 'SEM_INSTRUMENT_NAME', 'SEM_TRADING_SYMBOL', 'SEM_LOT_UNITS'], low_memory=False)

fno = df[(df['SEM_EXM_EXCH_ID'] == 'NSE') & (df['SEM_INSTRUMENT_NAME'] == 'FUTSTK')]

lot_sizes = {}
for sym in stock_symbols:
    # Find rows where trading symbol starts with '{sym}-'
    matches = fno[fno['SEM_TRADING_SYMBOL'].str.startswith(f"{sym}-", na=False)]
    if not matches.empty:
        lot_size = int(matches.iloc[0]['SEM_LOT_UNITS'])
        lot_sizes[sym] = lot_size
    else:
        lot_sizes[sym] = 1 # Fallback if not found

print(json.dumps(lot_sizes, indent=2))
