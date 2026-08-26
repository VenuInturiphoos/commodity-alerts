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
        sender_email = self.config['sender_email']
        sender_password = os.environ.get('SENDER_PASSWORD', self.config.get('sender_password', ''))
        recipient_emails = self.get_recipients()
        smtp_server = self.config['smtp_server']
        smtp_port = self.config['smtp_port']

        # Don't try to send if credentials are not set up
        if sender_email == "your_email@gmail.com":
            print(f"[ALERT NOT SENT - Please configure email in config.json]: {subject}\n{body}")
            return

        for recipient in recipient_emails:
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = recipient
            msg['Subject'] = subject

            personalized_body = f"{body}\n\n---\nTo unsubscribe from these market alerts, click here:\nhttps://dancing-semolina-31f889.netlify.app/unsubscribe.html?email={recipient}"
            msg.attach(MIMEText(personalized_body, 'plain'))

            try:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
                server.quit()
                print(f"Successfully sent alert to {recipient}")
            except Exception as e:
                print(f"Failed to send alert to {recipient}. Error: {e}")
