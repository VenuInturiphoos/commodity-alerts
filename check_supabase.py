import requests

supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/market_data?select=*'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'

headers = {
    'apikey': supabase_key,
    'Authorization': f'Bearer {supabase_key}',
    'Content-Type': 'application/json'
}
response = requests.get(supabase_url, headers=headers)
data = response.json()
print("Total rows:", len(data))
comms = [item for item in data if item['asset_type'] == 'Commodity']
print("Commodities in DB:", len(comms))
for item in comms:
    print(f"{item['name']} ({item['symbol']}): ₹{item['current_price']}")
