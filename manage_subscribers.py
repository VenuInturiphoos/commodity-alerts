import json
import os
import requests

CONFIG_FILE = 'config.json'
SUPABASE_URL = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/subscribers'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json'
}

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def get_supabase_subs():
    response = requests.get(SUPABASE_URL, headers=HEADERS)
    if response.status_code == 200:
        return [row['email'] for row in response.json()]
    return []

def delete_supabase_sub(email):
    url = f"{SUPABASE_URL}?email=eq.{email}"
    response = requests.delete(url, headers=HEADERS)
    if response.status_code in [200, 204]:
        print(f"Deleted {email} from Supabase.")
    else:
        print(f"Error deleting from Supabase: {response.text}")

def main():
    while True:
        print("\n=== Subscriber Management ===")
        config = load_config()
        hardcoded_emails = config.get('email_settings', {}).get('recipient_emails', [])
        
        print("\n1. View Active Subscribers")
        print("2. Remove an Email")
        print("3. Exit")
        
        choice = input("\nSelect an option: ")
        
        if choice == '1':
            print("\n--- Hardcoded Emails (config.json) ---")
            for e in hardcoded_emails:
                print(f"- {e}")
                
            print("\n--- Web Subscriptions (Supabase) ---")
            db_emails = get_supabase_subs()
            if not db_emails:
                print("No active web subscribers.")
            for e in db_emails:
                print(f"- {e}")
                
        elif choice == '2':
            email_to_remove = input("\nEnter the exact email address to remove: ").strip()
            removed_anything = False
            
            # Remove from config.json
            if email_to_remove in hardcoded_emails:
                hardcoded_emails.remove(email_to_remove)
                config['email_settings']['recipient_emails'] = hardcoded_emails
                save_config(config)
                print(f"Removed {email_to_remove} from config.json")
                removed_anything = True
                
            # Remove from Supabase
            db_emails = get_supabase_subs()
            if email_to_remove in db_emails:
                delete_supabase_sub(email_to_remove)
                removed_anything = True
                
            if not removed_anything:
                print(f"Could not find {email_to_remove} in any database.")
                
        elif choice == '3':
            break
        else:
            print("Invalid option.")

if __name__ == "__main__":
    main()
