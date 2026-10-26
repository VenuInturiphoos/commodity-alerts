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
        
        if alerts:
            formatted_alerts = []
            for alert in alerts:
                subject = alert['subject']
                if is_high_risk:
                    subject = f"[HIGH RISK - NEWS DAY] {subject}"
                body = alert['body']
                print(f">>> {subject} | {body.replace(chr(10), ' ')}")
                formatted_alerts.append({
                    'symbol': alert.get('symbol'),
                    'name': alert.get('name'),
                    'event_type': alert.get('event_type'),
                    'today_ist': alert.get('today_ist'),
                    'subject': subject,
                    'body': body
                })

            success, sent_alerts = alerter.send_alerts(formatted_alerts)
            if success and sent_alerts:
                checker.record_alerts_dispatched(sent_alerts)
            else:
                print("Alert dispatch was not confirmed via email. State not locked in Supabase (will retry on next trigger).")
        else:
            print("No levels triggered.")
    except Exception as e:
        print(f"Error during check_alerts loop: {e}")
        
    try:
        print("Generating near alerts JSON for Web UI...")
        checker.generate_near_alerts_json()
    except Exception as e:
        print(f"Error generating near alerts: {e}")
        
    try:
        # Note: checker.check_alerts() already generates circuits data and history.
        # This call checks cache guard (<60s) to guarantee web files are fresh without duplicate work.
        checker.generate_circuits_data()
    except Exception as e:
        print(f"Error generating circuits data & history: {e}")

    # Check and trigger 1:30 PM IST London/European Session Breakout Advisory (Mon - Fri)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if now_ist.weekday() < 5 and (now_ist.hour == 13 and 28 <= now_ist.minute <= 42):
            print("\n--- Checking 1:30 PM IST London Breakout Advisory ---")
            from daily_6pm_advisory import generate_and_send_130pm_advisory
            generate_and_send_130pm_advisory()
    except Exception as e:
        print(f"Error checking 1:30 PM advisory: {e}")

    # Check and trigger Daily 6:00 PM IST Action Advisory (Monday - Friday)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if now_ist.weekday() < 5 and (now_ist.hour == 18 and now_ist.minute <= 10):
            print("\n--- Checking Daily 6:00 PM IST Action Advisory ---")
            from daily_6pm_advisory import generate_and_send_6pm_advisory
            generate_and_send_6pm_advisory()
    except Exception as e:
        print(f"Error checking daily 6 PM advisory: {e}")

    # Check and trigger 6:15 PM IST Confirmation Check (Monday - Friday)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if now_ist.weekday() < 5 and (now_ist.hour == 18 and 14 <= now_ist.minute <= 25):
            print("\n--- Checking 6:15 PM IST Confirmation & Paper Trade Execution ---")
            from advisory_tracker import check_and_execute_615_confirmation
            check_and_execute_615_confirmation(checker, alerter)
    except Exception as e:
        print(f"Error checking 6:15 PM confirmation: {e}")

    # Check and trigger 8:00 PM IST EIA Inventory & US Evening Advisory (Mon - Fri)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if now_ist.weekday() < 5 and (now_ist.hour == 20 and now_ist.minute <= 12):
            print("\n--- Checking 8:00 PM IST US Evening & EIA Inventory Advisory ---")
            from daily_6pm_advisory import generate_and_send_8pm_advisory
            generate_and_send_8pm_advisory()
    except Exception as e:
        print(f"Error checking 8:00 PM advisory: {e}")

    # Check and trigger 11:30 PM IST Session Close Exit & Evaluation (Monday - Friday)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if now_ist.weekday() < 5 and (now_ist.hour == 23 and now_ist.minute >= 15):
            print("\n--- Checking 11:30 PM IST EOD Advisory Exit & Settlement ---")
            from advisory_tracker import exit_and_evaluate_eod_trades
            exit_and_evaluate_eod_trades(checker, alerter)
    except Exception as e:
        print(f"Error executing 11:30 PM EOD exit: {e}")
        
    print("\nCheck complete. Exiting.")

if __name__ == "__main__":
    main()
