import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailAlerter:
    def __init__(self, config):
        self.config = config['email_settings']

    def send_alert(self, subject, body):
        sender_email = self.config['sender_email']
        sender_password = os.environ.get('SENDER_PASSWORD', self.config.get('sender_password', ''))
        recipient_emails = self.config['recipient_emails']
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

            msg.attach(MIMEText(body, 'plain'))

            try:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
                server.quit()
                print(f"Successfully sent alert to {recipient}")
            except Exception as e:
                print(f"Failed to send alert to {recipient}. Error: {e}")
