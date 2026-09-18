import json
import time
import pytz
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from alerter import EmailAlerter
from price_checker import PriceChecker

def check_macro_risk():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req)
        xml_data = response.read()
        root = ET.fromstring(xml_data)
        
        today_str = datetime.now().strftime("%m-%d-%Y")
        for event in root.findall('event'):
            date = event.find('date').text
            impact = event.find('impact').text
            country = event.find('country').text
            if date == today_str and impact == "High" and country in ["USD", "INR"]:
                return True
    except Exception as e:
        print(f"Failed to fetch macro calendar: {e}")
    return False

def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)

def main():
    print("Starting Automated Commodity Alert System...")
    
    config = load_config()
    alerter = EmailAlerter(config)
    checker = PriceChecker(config)
    
    is_high_risk = check_macro_risk()
    if is_high_risk:
        print("⚠️ HIGH MACRO RISK DETECTED TODAY (Major USD/INR news event)")
    
    print("\n--- Running Price Checks ---")
    try:
        alerts = checker.check_alerts()
        
        for alert in alerts:
            subject = alert['subject']
            if is_high_risk:
                subject = f"[HIGH RISK - NEWS DAY] {subject}"
            body = alert['body']
            print(f">>> {subject} | {body.replace(chr(10), ' ')}")
            alerter.send_alert(subject=subject, body=body)
            
        if not alerts:
            print("No levels triggered.")
            
    except Exception as e:
        print(f"Error during check loop: {e}")
        
    print("\nCheck complete. Exiting.")

if __name__ == "__main__":
    main()
