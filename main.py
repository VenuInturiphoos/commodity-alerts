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

    # Check Dhan Access Token validity & send alert if expired
    try:
        from dhan_live_executor import DhanLiveExecutor
        dhan_exec = DhanLiveExecutor()
        dhan_exec.check_token_validity_and_alert(alerter=alerter)
    except Exception as e:
        pass
        
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
        if now_ist.weekday() < 5 and (now_ist.hour >= 13 and now_ist.hour < 15 and now_ist.minute >= 28):
            print("\n--- Checking 1:30 PM IST London Breakout Advisory ---")
            from daily_6pm_advisory import generate_and_send_130pm_advisory
            generate_and_send_130pm_advisory()
    except Exception as e:
        print(f"Error checking 1:30 PM advisory: {e}")

    # Check and trigger NSE Intraday 3:20 PM Square-off (Monday - Friday, 15:20 IST onwards)
    try:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        if now_ist.weekday() < 5 and (now_ist.hour > 15 or (now_ist.hour == 15 and now_ist.minute >= 20)):
            print("\n--- Checking NSE Intraday 3:20 PM Square-off & Settlement ---")
            from advisory_tracker import square_off_intraday_equity_trades
            square_off_intraday_equity_trades(checker, alerter)
    except Exception as e:
        print(f"Error checking 3:20 PM intraday square-off: {e}")

    # Dynamic US market aligned timings (Summer EDT vs Winter EST)
    try:
        from advisory_tracker import get_us_aligned_session_times
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        timing = get_us_aligned_session_times(now_ist)
        cur_hm = (now_ist.hour, now_ist.minute)

        # Check and trigger US Pit Open Action Advisory (6:00 PM Summer EDT / 7:00 PM Winter EST)
        if now_ist.weekday() < 5 and cur_hm >= timing["pit_cutoff"] and cur_hm < (timing["pit_cutoff"][0] + 1, 30):
            print(f"\n--- Checking US Pit Open Action Advisory ({timing['pit_str']}) ---")
            from daily_6pm_advisory import generate_and_send_6pm_advisory
            generate_and_send_6pm_advisory()

        # Check and trigger 15-Minute Confirmation Check (6:15 PM Summer EDT / 7:15 PM Winter EST)
        if now_ist.weekday() < 5 and cur_hm >= timing["conf_start"] and cur_hm < timing["conf_end"]:
            print(f"\n--- Checking 15-Minute Confirmation & Execution ({timing['conf_str']}) ---")
            from advisory_tracker import check_and_execute_615_confirmation
            check_and_execute_615_confirmation(checker, alerter)

        # Check and trigger EIA Inventory & US Evening Advisory (8:00 PM Summer EDT / 9:00 PM Winter EST)
        if now_ist.weekday() < 5 and cur_hm >= timing["evening_cutoff"] and cur_hm < (timing["evening_cutoff"][0] + 2, 0):
            print(f"\n--- Checking US Evening & EIA Inventory Advisory ({timing['evening_str']}) ---")
            from daily_6pm_advisory import generate_and_send_8pm_advisory
            generate_and_send_8pm_advisory()

        # Check and trigger EOD Session Close Exit & Evaluation (11:20 PM Summer EDT / 11:45 PM Winter EST)
        is_eod_sweep_window = cur_hm >= timing["eod_cutoff"] or (now_ist.hour >= 0 and now_ist.hour < 9)
        if is_eod_sweep_window:
            print(f"\n--- Checking EOD Advisory Session Close Exit & Settlement ({timing['eod_str']}) ---")
            from advisory_tracker import exit_and_evaluate_eod_trades
            exit_and_evaluate_eod_trades(checker, alerter)
    except Exception as e:
        print(f"Error checking US-aligned evening session advisories: {e}")
        
    print("\nCheck complete. Exiting.")

if __name__ == "__main__":
    main()
