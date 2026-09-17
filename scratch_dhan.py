import pandas as pd
from datetime import datetime
import numpy as np

print("Downloading DhanHQ Instrument Master...")
cols_to_use = [
    'SEM_EXM_EXCH_ID',
    'SEM_INSTRUMENT_NAME',
    'SM_SYMBOL_NAME',
    'SEM_EXPIRY_DATE',
    'SEM_SMST_SECURITY_ID',
    'SEM_CUSTOM_SYMBOL',
    'SEM_STRIKE_PRICE',
    'SEM_OPTION_TYPE'
]
df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', usecols=cols_to_use, low_memory=False)

def get_derivatives(base_symbol, spot_price):
    now = datetime.now()
    
    # Futures
    futures = df[
        (df['SEM_EXM_EXCH_ID'] == 'NSE') & 
        (df['SEM_INSTRUMENT_NAME'] == 'FUTIDX') &
        (df['SM_SYMBOL_NAME'] == base_symbol)
    ].copy()
    
    if not futures.empty:
        futures['EXP_DATE'] = pd.to_datetime(futures['SEM_EXPIRY_DATE'])
        futures = futures[futures['EXP_DATE'] >= now].sort_values('EXP_DATE')
        near_fut = futures.iloc[0]
        print(f"Near Future: {near_fut['SEM_CUSTOM_SYMBOL']} (ID: {near_fut['SEM_SMST_SECURITY_ID']})")
    
    # Options
    options = df[
        (df['SEM_EXM_EXCH_ID'] == 'NSE') & 
        (df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') &
        (df['SM_SYMBOL_NAME'] == base_symbol)
    ].copy()
    
    if not options.empty:
        options['EXP_DATE'] = pd.to_datetime(options['SEM_EXPIRY_DATE'])
        options = options[options['EXP_DATE'] >= now].sort_values('EXP_DATE')
        
        # Get all options for the nearest expiry
        nearest_expiry = options.iloc[0]['EXP_DATE']
        near_options = options[options['EXP_DATE'] == nearest_expiry].copy()
        
        # Find ATM strikes
        near_options['STRIKE_DIFF'] = abs(near_options['SEM_STRIKE_PRICE'] - spot_price)
        atm_strike = near_options.loc[near_options['STRIKE_DIFF'].idxmin()]['SEM_STRIKE_PRICE']
        print(f"Spot: {spot_price}, ATM Strike: {atm_strike}")
        
        atm_options = near_options[near_options['SEM_STRIKE_PRICE'] == atm_strike]
        for _, opt in atm_options.iterrows():
            print(f"ATM Option: {opt['SEM_CUSTOM_SYMBOL']} (ID: {opt['SEM_SMST_SECURITY_ID']})")

get_derivatives('NIFTY', 25000)
