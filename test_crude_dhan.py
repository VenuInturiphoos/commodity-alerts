import pandas as pd
df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', usecols=['SEM_EXM_EXCH_ID', 'SEM_INSTRUMENT_NAME', 'SM_SYMBOL_NAME', 'SEM_CUSTOM_SYMBOL', 'SEM_EXPIRY_DATE'], low_memory=False)
mcx = df[(df['SEM_EXM_EXCH_ID'] == 'MCX') & (df['SM_SYMBOL_NAME'] == 'CRUDEOIL')]
print("Instrument Names for CRUDEOIL:")
print(mcx['SEM_INSTRUMENT_NAME'].unique())
print("\nFirst few rows:")
print(mcx.head())
