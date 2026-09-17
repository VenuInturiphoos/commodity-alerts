import requests
supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/market_data?select=*'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'
headers = { 'apikey': supabase_key, 'Authorization': f'Bearer {supabase_key}' }
data = requests.get(supabase_url, headers=headers).json()
for row in data:
    if 'ADANI' in row['symbol']:
        print(f"{row['symbol']}: {row['last_alert_date']} - {row['last_alert_msg']}")
