import os
import ssl
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailAlerter:
    def __init__(self, config):
        self.config = config.get('email_settings', {})
        self.supabase_url = 'https://cohupetijvykzmeliubg.supabase.co/rest/v1/subscribers?select=email'
        self.supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvaHVwZXRpanZ5a3ptZWxpdWJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcxMjg4ODAsImV4cCI6MjEwMjcwNDg4MH0.zA5IwTKp0f-IRQ5dB3a9vXJSD1X2EVzxIDEyzXC27Cw'
        self._cached_recipients = None

    def get_recipients(self):
        if self._cached_recipients is not None:
            return self._cached_recipients

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
            
        self._cached_recipients = list(recipients)
        return self._cached_recipients

    def _authenticate_smtp(self, server, sender_email, sender_password):
        import base64
        # Google Gmail SMTP accepts AUTH PLAIN with base64 encoded "\0username\0password"
        # Directly sending AUTH PLAIN allows us to capture Google's exact 535 response message
        # before Python's smtplib tries fallback auth on a connection that Google terminates.
        auth_bytes = f"\0{sender_email}\0{sender_password}".encode('utf-8')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        code, resp = server.docmd("AUTH", f"PLAIN {auth_b64}")
        if code in (235, 503):
            return True
        elif code == 535:
            err_msg = resp.decode('utf-8', errors='ignore') if isinstance(resp, bytes) else str(resp)
            raise smtplib.SMTPAuthenticationError(code, f"Google rejected credentials (535 Bad Credentials):\n{err_msg}")
        else:
            # Fallback to standard login if server prefers another scheme
            server.login(sender_email, sender_password)
            return True

    def _create_smtp_session(self, sender_email, sender_password):
        smtp_server = self.config.get('smtp_server', 'smtp.gmail.com')
        preferred_port = int(self.config.get('smtp_port', 465))
        context = ssl.create_default_context()

        # Prioritize Port 465 (Implicit SSL/TLS) because cloud CI/CD environments (GitHub Actions / Azure)
        # commonly drop or block STARTTLS on port 587. Fall back to port 587 if 465 fails.
        if preferred_port == 465:
            attempts = [(465, True), (587, False)]
        elif preferred_port == 587:
            attempts = [(465, True), (587, False)]
        else:
            attempts = [(preferred_port, preferred_port == 465), (465, True), (587, False)]

        last_err = None
        for port, use_ssl in attempts:
            try:
                if use_ssl:
                    print(f"Connecting to SMTP server {smtp_server}:{port} via SSL/TLS...")
                    server = smtplib.SMTP_SSL(smtp_server, port, timeout=20, context=context)
                    server.ehlo()
                else:
                    print(f"Connecting to SMTP server {smtp_server}:{port} via STARTTLS...")
                    server = smtplib.SMTP(smtp_server, port, timeout=20)
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()

                print(f"Authenticating on {smtp_server}:{port} as {sender_email}...")
                self._authenticate_smtp(server, sender_email, sender_password)
                print(f"SMTP authenticated successfully on {smtp_server}:{port}!")
                return server
            except smtplib.SMTPAuthenticationError as auth_err:
                print(f"\n[CRITICAL SMTP AUTHENTICATION ERROR] on {smtp_server}:{port}:")
                print(f"{auth_err}")
                print("\n>>> HOW TO FIX GMAIL SMTP AUTHENTICATION:")
                print("1. Go to your Google Account (https://myaccount.google.com/security)")
                print("2. Make sure '2-Step Verification' is turned ON.")
                print("3. Search for 'App passwords' (or go to https://myaccount.google.com/apppasswords).")
                print("4. Create a new App Password named 'GitHub Actions' (it will give you a 16-character code like 'abcd efgh ijkl mnop').")
                print("5. In GitHub repo Settings -> Secrets and variables -> Actions, update the secret SENDER_PASSWORD with this 16-character code.\n")
                last_err = auth_err
                break
            except Exception as e:
                print(f"SMTP connection/handshake failed on port {port}: {e}")
                last_err = e

        if last_err:
            raise last_err
        raise RuntimeError("Could not establish SMTP connection.")

    @classmethod
    def is_trade_execution_alert(cls, subject: str, body: str = '') -> bool:
        """
        Determines whether an alert represents an executed trade entry (taken)
        or an executed trade exit (target hit, stop-loss, square-off, closed).

        Per user instruction:
        Email alerts are ONLY provided for trades that are taken and exited trades.
        All other alerts (circuit warnings, daily advisories, market pulse,
        playbooks, commentary, event risk updates) are suppressed from email.
        """
        sub_upper = (subject or '').upper()
        bdy_upper = (body or '').upper()

        # 1. Explicit Exclusions (Never send these non-trade alerts via email)
        explicit_blocks = [
            "CIRCUIT WARNING",
            "CIRCUIT HIT",
            "NEAR UPPER CIRCUIT",
            "NEAR LOWER CIRCUIT",
            "REACHED UPPER CIRCUIT",
            "REACHED LOWER CIRCUIT",
            "HIGH RISK - NEWS DAY",
            "6 PM IST ADVISORY",
            "1:30 PM IST ADVISORY",
            "8:00 PM IST ADVISORY",
            "9:01 AM IST ADVISORY",
            "MORNING MARKET PULSE",
            "LONDON / EUROPEAN OPEN",
            "ACTION PLAYBOOK",
            "STAND ASIDE PLAYBOOK",
            "TRAP AVOIDED",
            "CONFIRMED_STAND_ASIDE",
            "STAGE 1: BREAKEVEN LOCK",
            "STAGE 2: PROFIT LOCKED",
            "EVENT RISK PROTECTION",
        ]
        for block in explicit_blocks:
            if block in sub_upper:
                return False

        # 2. Trades Taken (Entry / Order Executions)
        trade_taken_signatures = [
            "[DAILY STOCK SIP]",
            "[NIFTY INTRADAY MIS TRIGGER]",
            "[NSE STOCK FUTURE TRIGGER]",
            "[NSE FUTURE TRIGGER]",
            "[REAL-TIME TECHNICAL TRIGGER]",
            "[LIMIT ORDER FILLED]",
            "[6:15 PM TRADE CONFIRMED]",
            "REAL-TIME INTRADAY STOCK TRIGGER",
            "DAILY STOCK INVESTMENT (CASH DELIVERY)",
            "ORDER EXECUTED",
            "TRADE TAKEN",
            "POSITION OPENED",
        ]
        for sig in trade_taken_signatures:
            if sig in sub_upper or sig in bdy_upper:
                return True

        if "BOUGHT" in sub_upper and ("SHARES" in sub_upper or "SIP" in sub_upper):
            return True

        # 3. Trades Exited (Target Hit, Stop Loss, Breakeven, Square-off, EOD Settlement)
        trade_exited_signatures = [
            "[TARGET HIT]",
            "TARGET 1 EXECUTED",
            "TARGET 2 EXECUTED",
            "TARGET ACHIEVED",
            "[STOP LOSS HIT]",
            "STOP LOSS HIT",
            "STOP-LOSS TRIGGERED",
            "PROTECTIVE STOP LOSS EXECUTED",
            "PAPER TRADE EXIT",
            "PAPER TRADE AUTO-EXIT",
            "BREAKEVEN EXIT",
            "STAGNATION EXIT",
            "03:20 PM SQUARE-OFF",
            "INTRADAY SQUARE-OFF",
            "EOD SQUARE-OFF",
            "SQUARE-OFF SETTLED",
            "SQUARE-OFF COMPLETED",
            "[11:30 PM SETTLEMENT]",
            "POSITION CLOSED",
        ]
        for sig in trade_exited_signatures:
            if sig in sub_upper or sig in bdy_upper:
                return True

        return False

    def send_alerts(self, alerts):
        if not alerts:
            return False, []

        telegram_sent_indices = set()
        # 1. Send Telegram notifications
        for idx, alert in enumerate(alerts):
            try:
                if self._send_telegram(alert['subject'], alert['body']):
                    telegram_sent_indices.add(idx)
            except Exception as e:
                print(f"Error dispatching Telegram alert {idx}: {e}")

        # 2. Check email settings and credentials
        sender_email = (os.environ.get('SENDER_EMAIL') or self.config.get('sender_email') or 'your_email@gmail.com').strip()
        raw_password = (os.environ.get('SENDER_PASSWORD') or self.config.get('sender_password') or '').strip()
        # Clean up any spaces or accidental quotes in app password
        sender_password = raw_password.replace(' ', '').replace('"', '').replace("'", "")
        recipient_emails = self.get_recipients()

        email_sent_indices = set()
        email_filtered_indices = set()
        if sender_email != "your_email@gmail.com" and sender_password and recipient_emails:
            # Policy Enforcement: Only provide email alerts for trades taken and exited trades
            email_alerts_to_send = []
            for idx, alert in enumerate(alerts):
                subject = alert.get('subject', '')
                body = alert.get('body', '')
                if self.is_trade_execution_alert(subject, body):
                    email_alerts_to_send.append((idx, alert))
                else:
                    print(f"📧 [EMAIL FILTER] Suppressing non-trade alert per user rule: {subject}")
                    email_filtered_indices.add(idx)

            if email_alerts_to_send:
                server = None
                try:
                    server = self._create_smtp_session(sender_email, sender_password)
                    for idx, alert in email_alerts_to_send:
                        subject = alert['subject']
                        body = alert['body']
                        body_html = body.replace('\n', '<br>')

                        alert_dispatched_successfully = False
                        for recipient in recipient_emails:
                            msg = MIMEMultipart('alternative')
                            msg['From'] = sender_email
                            msg['To'] = recipient
                            msg['Subject'] = subject

                            plain_body = f"{body}\n\n---\nTo unsubscribe from these market alerts, click here:\nhttps://VenuInturiphoos.github.io/commodity-alerts/unsubscribe.html?email={recipient}"
                            html_body = f"""
                            <html>
                              <body>
                                <p>{body_html}</p>
                                <hr style="margin-top: 20px; border: 0; border-top: 1px solid #eee;">
                                <p style="font-size: 12px; color: #888;">
                                  To unsubscribe from these market alerts, <a href="https://VenuInturiphoos.github.io/commodity-alerts/unsubscribe.html?email={recipient}">click here</a>.
                                </p>
                              </body>
                            </html>
                            """

                            msg.attach(MIMEText(plain_body, 'plain'))
                            msg.attach(MIMEText(html_body, 'html'))

                            try:
                                server.send_message(msg)
                                print(f"Successfully sent trade execution email to {recipient}: {subject}")
                                alert_dispatched_successfully = True
                            except Exception as e:
                                print(f"Failed to send email to {recipient}: {e}")

                        if alert_dispatched_successfully:
                            email_sent_indices.add(idx)

                except Exception as e:
                    print(f"SMTP Dispatch Error: {e}")
                finally:
                    if server:
                        try:
                            server.quit()
                        except Exception:
                            pass
            else:
                print("📧 [EMAIL FILTER] No trades taken or exited in this batch. 0 emails sent.")
        else:
            if not sender_password or sender_email == "your_email@gmail.com":
                print("Email credentials (SENDER_PASSWORD) not configured. Email dispatch skipped.")
            elif not recipient_emails:
                print("No email recipients found. Email dispatch skipped.")

        dispatched_indices = telegram_sent_indices | email_sent_indices | email_filtered_indices
        sent_alerts = [alerts[i] for i in sorted(dispatched_indices)]
        
        return (len(sent_alerts) > 0), sent_alerts

    def send_alert(self, subject, body):
        return self.send_alerts([{'subject': subject, 'body': body}])

    def _send_telegram(self, subject, body):
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN') or self.config.get('telegram_bot_token')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID') or self.config.get('telegram_chat_id')
        if not bot_token or not chat_id:
            return False
            
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # Attempt HTML formatted send first for rich styling
        formatted_text = f"<b>{subject}</b>\n\n{body}"
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": formatted_text, "parse_mode": "HTML"},
                timeout=10
            )
            if response.status_code == 200:
                print("Telegram alert sent successfully (HTML).")
                return True
            else:
                # If HTML parsing fails due to unescaped tags, fallback to plain text
                print(f"Telegram HTML send returned status {response.status_code}: {response.text}. Retrying plain text...")
                plain_text = f"{subject}\n\n{body}"
                plain_resp = requests.post(
                    url,
                    json={"chat_id": chat_id, "text": plain_text},
                    timeout=10
                )
                if plain_resp.status_code == 200:
                    print("Telegram alert sent successfully (plain text fallback).")
                    return True
                else:
                    print(f"Failed to send Telegram alert: {plain_resp.text}")
                    return False
        except Exception as e:
            print(f"Failed to send Telegram alert Exception: {e}")
            return False
