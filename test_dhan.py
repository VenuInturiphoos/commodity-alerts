import pandas as pd
df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', usecols=['SEM_EXM_EXCH_ID', 'SEM_INSTRUMENT_NAME', 'SM_SYMBOL_NAME', 'SEM_CUSTOM_SYMBOL'], low_memory=False)
mcx = df[df['SEM_EXM_EXCH_ID'] == 'MCX']
print("Unique MCX Symbol Names:")
print(mcx['SM_SYMBOL_NAME'].unique()[:20])
