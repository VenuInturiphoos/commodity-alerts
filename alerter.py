import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailAlerter:
    def __init__(self, config):
        self.config = config['email_settings']
        self.supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/subscribers?select=email'
        self.supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'

    def get_recipients(self):
        # Start with static recipients from config.json
        recipients = set(self.config.get('recipient_emails', []))
        
        # Fetch dynamic subscribers from Supabase
        try:
            headers = {
                'apikey': self.supabase_key,
                'Authorization': f'Bearer {self.supabase_key}'
            }
            response = requests.get(self.supabase_url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for row in data:
                    if 'email' in row and row['email']:
                        recipients.add(row['email'])
                print(f"Successfully loaded {len(recipients)} total subscribers (including database).")
            else:
                print(f"Warning: Failed to fetch from Supabase. Status: {response.status_code}")
        except Exception as e:
            print(f"Warning: Exception fetching from Supabase: {e}")
            
        return list(recipients)

    def send_alert(self, subject, body):
        # Send Telegram notification if configured
        self._send_telegram(subject, body)
        
        sender_email = self.config.get('sender_email', 'your_email@gmail.com')
        sender_password = os.environ.get('SENDER_PASSWORD', self.config.get('sender_password', '')).strip()
        recipient_emails = self.get_recipients()
        smtp_server = self.config['smtp_server']
        smtp_port = self.config['smtp_port']

        # Don't try to send if credentials are not set up
        if sender_email == "your_email@gmail.com" or not sender_password:
            print(f"[ALERT NOT SENT - Please configure email in config.json or SENDER_PASSWORD env var]: {subject}\n{body}")
            return

        try:
            # Connect once for all recipients to avoid being rate-limited or generating 501s from multiple fast logins
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(sender_email, sender_password)
            
            for recipient in recipient_emails:
                msg = MIMEMultipart()
                msg['From'] = sender_email
                msg['To'] = recipient
                msg['Subject'] = subject

                personalized_body = f"{body}\n\n---\nTo unsubscribe from these market alerts, click here:\nhttps://VenuInturiphoos.github.io/commodity-alerts/unsubscribe.html?email={recipient}"
                msg.attach(MIMEText(personalized_body, 'plain'))

                try:
                    server.send_message(msg)
                    print(f"Successfully sent alert to {recipient}")
                except Exception as e:
                    print(f"Failed to send email to {recipient}: {e}")
                    
            server.quit()
        except Exception as e:
            print(f"Failed to connect or login to SMTP server: {e}")

    def _send_telegram(self, subject, body):
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        if not bot_token or not chat_id:
            return
            
        text = f"{subject}\n\n{body}"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
            if response.status_code == 200:
                print("Telegram alert sent successfully.")
            else:
                print(f"Failed to send Telegram alert: {response.text}")
        except Exception as e:
            print(f"Failed to send Telegram alert Exception: {e}")
