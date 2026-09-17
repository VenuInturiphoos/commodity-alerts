import requests
url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/market_data?select=*'
key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'
r = requests.get(url, headers={'apikey': key})
data = r.json()
for d in data:
    if d['asset_type'] == 'Commodity':
        print(f"{d['name']} ({d['symbol']}): Price = {d['current_price']}, Updated = {d['last_updated']}")
