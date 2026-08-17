import json
import time
from alerter import EmailAlerter
from price_checker import PriceChecker

def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)

def main():
    print("Starting Automated Commodity Alert System...")
    
    config = load_config()
    alerter = EmailAlerter(config)
    checker = PriceChecker(config)
    
    print("\n--- Running Price Checks ---")
    try:
        alerts = checker.check_alerts()
        
        for alert in alerts:
            print(f">>> {alert}")
            alerter.send_alert(subject="Commodity Price Alert", body=alert)
            
        if not alerts:
            print("No levels triggered.")
            
    except Exception as e:
        print(f"Error during check loop: {e}")
        
    print("\nCheck complete. Exiting.")

if __name__ == "__main__":
    main()
