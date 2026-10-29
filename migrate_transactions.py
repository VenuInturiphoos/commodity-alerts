import json
import requests

url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1'
key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'
headers = {
    'apikey': key,
    'Authorization': f'Bearer {key}',
    'Content-Type': 'application/json',
    'Prefer': 'return=representation'
}

LOT_SIZES = {
    'GOLD': 10, 'SILVER': 5, 'CRUDEOIL': 50, 'NATURALGAS': 1250, 'COPPER': 1250,
    'NIFTY': 25, 'BANKNIFTY': 15,
    'ADANIENT.NS': 309, 'ADANIPORTS.NS': 475, 'APOLLOHOSP.NS': 125, 'ASIANPAINT.NS': 250,
    'AXISBANK.NS': 625, 'BAJAJ-AUTO.NS': 75, 'BAJFINANCE.NS': 750, 'BAJAJFINSV.NS': 300,
    'BPCL.NS': 1975, 'BHARTIARTL.NS': 475, 'BRITANNIA.NS': 125, 'CIPLA.NS': 425,
    'COALINDIA.NS': 1350, 'DIVISLAB.NS': 100, 'DRREDDY.NS': 625, 'EICHERMOT.NS': 100,
    'GRASIM.NS': 250, 'HCLTECH.NS': 400, 'HDFCBANK.NS': 650, 'HDFCLIFE.NS': 1100,
    'HEROMOTOCO.NS': 150, 'HINDALCO.NS': 700, 'HINDUNILVR.NS': 300, 'ICICIBANK.NS': 700,
    'ITC.NS': 1725, 'INDUSINDBK.NS': 700, 'INFY.NS': 400, 'JSWSTEEL.NS': 675,
    'KOTAKBANK.NS': 2000, 'LTIM.NS': 250, 'LT.NS': 175, 'M&M.NS': 200, 'MARUTI.NS': 50,
    'NTPC.NS': 1500, 'NESTLEIND.NS': 500, 'ONGC.NS': 2250, 'POWERGRID.NS': 1900,
    'RELIANCE.NS': 500, 'SBILIFE.NS': 375, 'SBIN.NS': 750, 'SUNPHARMA.NS': 350,
    'TCS.NS': 225, 'TATACONSUM.NS': 550, 'TATAMOTORS.NS': 1425, 'TATASTEEL.NS': 2750,
    'TECHM.NS': 600, 'TITAN.NS': 175, 'SHRIRAMFIN.NS': 825, 'ULTRACEMCO.NS': 50, 'WIPRO.NS': 3000
}

# 1. Fetch all transactions
tx_resp = requests.get(f'{url}/transactions?select=*', headers=headers)
transactions = tx_resp.json()

# 2. Fetch all profiles
prof_resp = requests.get(f'{url}/profiles?select=*', headers=headers)
profiles = {p['id']: p['balance'] for p in prof_resp.json()}
profile_adjustments = {p_id: 0.0 for p_id in profiles}

# 3. Process transactions
for tx in transactions:
    symbol = tx['symbol']
    if not symbol.endswith('-FUT'):
        base_sym = symbol
        lot_size = LOT_SIZES.get(base_sym, 1)
        
        if lot_size > 1 or True: # always update to FUT
            old_qty = tx['quantity']
            new_qty = old_qty * lot_size
            new_sym = f"{base_sym}-FUT"
            
            print(f"Migrating TX {tx['id']}: {base_sym} ({old_qty}) -> {new_sym} ({new_qty})")
            
            # Update transaction
            patch_resp = requests.patch(f"{url}/transactions?id=eq.{tx['id']}", 
                json={"symbol": new_sym, "quantity": new_qty}, 
                headers=headers)
            
            # Calculate balance adjustment
            old_val = old_qty * tx['price']
            new_val = new_qty * tx['price']
            val_diff = new_val - old_val
            
            if tx['transaction_type'] == 'BUY':
                profile_adjustments[tx['user_id']] -= val_diff
            else:
                profile_adjustments[tx['user_id']] += val_diff

# 4. Update profiles
for p_id, adj in profile_adjustments.items():
    if adj != 0:
        new_bal = profiles[p_id] + adj
        print(f"Updating user {p_id} balance: {profiles[p_id]} -> {new_bal} (Adj: {adj})")
        requests.patch(f"{url}/profiles?id=eq.{p_id}", json={"balance": new_bal}, headers=headers)

print("Migration complete!")
