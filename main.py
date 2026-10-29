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

    try:
        print("Scanning Nifty 50 Golden Cross & Oversold stocks...")
        from nifty_scanner import scan_nifty50_opportunities
        scan_nifty50_opportunities()
    except Exception as e:
        print(f"Error scanning Nifty 50 Golden Cross & Oversold opportunities: {e}")

    # Check and trigger Daily Golden Crossover Stock Investment (~₹30,000 Allocation)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        from stock_investment_tracker import scan_and_execute_daily_stock_investment, update_live_stock_investments
        # Execute morning purchase on weekdays between 9:15 AM and 11:30 AM IST (or when first run of the day)
        if now_ist.weekday() < 5 and ((now_ist.hour == 9 and now_ist.minute >= 15) or now_ist.hour >= 10):
            print("\n--- Checking Daily Golden Crossover Stock Investment (~₹30k Sizing) ---")
            scan_and_execute_daily_stock_investment()
        # Always update live MTM and target/stop triggers of existing stock holdings
        update_live_stock_investments()
    except Exception as e:
        print(f"Error in daily stock investment engine: {e}")

    # Monitor active advisory paper trades for Target and Stop-Loss triggers
    try:
        from advisory_tracker import check_and_execute_intraday_targets_and_stops
        check_and_execute_intraday_targets_and_stops(checker, alerter)
    except Exception as e:
        print(f"Error checking advisory targets/stops: {e}")

    # Real-Time Continuous Technical Confluence Scanner (Mon - Fri, 09:05 AM - 10:45 PM IST)
    try:
        from advisory_tracker import evaluate_realtime_technical_triggers
        evaluate_realtime_technical_triggers(checker, alerter)
    except Exception as e:
        print(f"Error evaluating real-time technical triggers: {e}")

    # Check and trigger 09:01 AM IST Morning Market Pulse & Opening Gap Advisory (Mon - Fri)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        # Active window: 09:01 AM to 11:00 AM IST (Waits until 09:01 AM so MCX opening order matching is in effect)
        if now_ist.weekday() < 5 and ((now_ist.hour == 9 and now_ist.minute >= 1) or now_ist.hour == 10):
            print("\n--- Checking 09:01 AM IST Morning Pulse & Opening Gap Advisory ---")
            from daily_6pm_advisory import generate_and_send_9am_advisory
            generate_and_send_9am_advisory()
    except Exception as e:
        print(f"Error checking 09:01 AM advisory: {e}")

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
