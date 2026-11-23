import os
import ssl
import smtplib
import json
import re
import requests
from datetime import datetime
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

        # 0. Dedicated Dhan Broker Events (Orders, SL Attach, SL Ratchet/Update, Square-off)
        if any(tag in sub_upper for tag in ["[DHAN", "DHAN ORDER", "DHAN STOP-LOSS", "DHAN SL", "DHAN POSITION", "DHAN BROKER"]):
            return True

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

        # 4. Critical Administrative & Broker Auth Alerts (Always allow email delivery)
        admin_alert_signatures = [
            "DHAN ACCESS TOKEN",
            "DHAN TOKEN EXPIRED",
            "[ACTION REQUIRED]",
            "BROKER AUTHENTICATION",
            "TOKEN EXPIRED",
        ]
        for sig in admin_alert_signatures:
            if sig in sub_upper or sig in bdy_upper:
                return True

        return False

    DEDUP_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.local_dedup_cache.json')

    def _get_dedup_window(self, subject, default_minutes=15):
        """
        Determines the protective cooldown window for alert deduplication.
        Real-time technical entry triggers strictly receive 120-minute (2 hour) lockout
        to prevent duplicate or recurring email spam while an active trade is underway.
        """
        sub_upper = subject.upper()
        if 'REAL-TIME TECHNICAL TRIGGER' in sub_upper or 'INTRADAY MIS TRIGGER' in sub_upper or 'STOCK FUTURE TRIGGER' in sub_upper:
            return 120
        if '100% TARGET ACHIEVED' in sub_upper or 'TARGET HIT' in sub_upper or 'STOP-LOSS' in sub_upper:
            return 30
        return default_minutes

    def _is_duplicate_trade_alert(self, subject, channel="EMAIL", window_minutes=None):
        """
        Guards against sending duplicate trade execution alerts across overlapping
        cron cycles or runner loops.
        Uses a local non-git persisted cache file (.local_dedup_cache.json) so Git pulls
        never wipe out active deduplication timestamps.
        Strips dynamic timestamps and price fractions to create a deterministic signature.
        e.g. '⚡ [REAL-TIME TECHNICAL TRIGGER] Silver Micro BUY @ ₹154,287.00 (12:28 PM IST)'
        -> key: 'EMAIL_⚡ [REAL-TIME TECHNICAL TRIGGER] SILVER MICRO BUY'
        """
        try:
            norm_key = re.sub(r'\(.*?\)', '', subject)
            norm_key = re.sub(r'@\s*₹?[\d,.]+', '', norm_key)
            norm_key = f"{channel}_" + re.sub(r'\s+', ' ', norm_key).strip().upper()

            req_window = window_minutes if window_minutes is not None else self._get_dedup_window(subject)
            now = datetime.now()

            # Read from persistent local non-git cache first, then fallback to web/alerts_state.json
            dedup_store = {}
            for cache_path in [self.DEDUP_CACHE_FILE, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')]:
                if os.path.exists(cache_path):
                    try:
                        with open(cache_path, 'r') as f:
                            data = json.load(f)
                        dedup_store.update(data.get('__email_dedup_cache__', {}))
                    except Exception:
                        pass

            if norm_key in dedup_store:
                last_sent_str = dedup_store[norm_key]
                last_sent_dt = datetime.fromisoformat(last_sent_str)
                elapsed = (now - last_sent_dt).total_seconds()
                if elapsed < (req_window * 60):
                    rem_mins = int((req_window * 60 - elapsed) / 60)
                    print(f"🛡️ [{channel} DEDUP] Suppressed duplicate alert: '{norm_key}' dispatched {int(elapsed/60)}m ago ({rem_mins}m remaining in {req_window}m lockout window).")
                    return True
        except Exception as e:
            print(f"Warning: Exception in _is_duplicate_trade_alert: {e}")
        return False

    def _record_trade_alert_sent(self, subject, channel="EMAIL"):
        """
        Records dispatched trade alert signature in persistent local non-git cache
        AND web/alerts_state.json. Uses file locking to prevent race conditions.
        Auto-purges entries older than 24 hours.
        """
        try:
            norm_key = re.sub(r'\(.*?\)', '', subject)
            norm_key = re.sub(r'@\s*₹?[\d,.]+', '', norm_key)
            norm_key = f"{channel}_" + re.sub(r'\s+', ' ', norm_key).strip().upper()

            now = datetime.now()
            
            # 1. Update persistent local non-git cache with file locking
            import fcntl
            local_dedup_state = {}
            if os.path.exists(self.DEDUP_CACHE_FILE):
                try:
                    with open(self.DEDUP_CACHE_FILE, 'r') as lf:
                        local_dedup_state = json.load(lf)
                except Exception:
                    local_dedup_state = {}

            l_store = local_dedup_state.setdefault('__email_dedup_cache__', {})
            l_store[norm_key] = now.isoformat()
            # Purge older than 24h
            l_del = [k for k, v in l_store.items() if (now - datetime.fromisoformat(v)).total_seconds() > 86400]
            for k in l_del:
                l_store.pop(k, None)

            try:
                with open(self.DEDUP_CACHE_FILE, 'w') as lf:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                    json.dump(local_dedup_state, lf, indent=2)
                    fcntl.flock(lf, fcntl.LOCK_UN)
            except Exception as lock_err:
                with open(self.DEDUP_CACHE_FILE, 'w') as lf:
                    json.dump(local_dedup_state, lf, indent=2)

            # 2. Also mirror to web/alerts_state.json for web dashboard visibility
            local_state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
            state = {}
            if os.path.exists(local_state_path):
                try:
                    with open(local_state_path, 'r') as f:
                        state = json.load(f)
                except Exception:
                    state = {}

            dedup_store = state.setdefault('__email_dedup_cache__', {})
            dedup_store[norm_key] = now.isoformat()
            keys_to_del = [k for k, v in dedup_store.items() if (now - datetime.fromisoformat(v)).total_seconds() > 86400]
            for k in keys_to_del:
                dedup_store.pop(k, None)

            with open(local_state_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Warning: Exception in _record_trade_alert_sent: {e}")

    def send_alerts(self, alerts):
        if not alerts:
            return False, []

        telegram_sent_indices = set()
        # 1. Send Telegram notifications
        for idx, alert in enumerate(alerts):
            try:
                sub = alert.get('subject', '')
                if self._is_duplicate_trade_alert(sub, channel="TELEGRAM", window_minutes=15):
                    print(f"🛡️ [TELEGRAM DEDUP] Suppressing duplicate Telegram alert (dispatched < 15m ago): {sub}")
                    continue
                if self._send_telegram(sub, alert['body']):
                    telegram_sent_indices.add(idx)
                    self._record_trade_alert_sent(sub, channel="TELEGRAM")
            except Exception as e:
                print(f"Error dispatching Telegram alert {idx}: {e}")

        # 2. Check email settings and credentials
        sender_email = (os.environ.get('SENDER_EMAIL') or self.config.get('sender_email') or 'your_email@gmail.com').strip()
        raw_password = (os.environ.get('SENDER_PASSWORD') or self.config.get('sender_password') or '').strip()
        if not raw_password:
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
            if os.path.exists(env_path):
                try:
                    with open(env_path, 'r') as ef:
                        for line in ef:
                            line = line.strip()
                            if line.startswith('SENDER_PASSWORD='):
                                raw_password = line.split('=', 1)[1].strip().strip('"').strip("'")
                            elif line.startswith('SENDER_EMAIL=') and sender_email == 'your_email@gmail.com':
                                sender_email = line.split('=', 1)[1].strip().strip('"').strip("'")
                except Exception:
                    pass
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
                if not self.is_trade_execution_alert(subject, body):
                    print(f"📧 [EMAIL FILTER] Suppressing non-trade alert per user rule: {subject}")
                    email_filtered_indices.add(idx)
                elif self._is_duplicate_trade_alert(subject, channel="EMAIL", window_minutes=15):
                    print(f"🛡️ [EMAIL DEDUP] Suppressing duplicate trade email (dispatched < 15m ago): {subject}")
                    email_filtered_indices.add(idx)
                else:
                    email_alerts_to_send.append((idx, alert))

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
                            self._record_trade_alert_sent(subject, channel="EMAIL")

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

    def send_dhan_broker_email(self, subject, body_html, body_text=None, recipients=None):
        """
        Dispatches a dedicated, confidential broker transaction email directly to the Dhan account owner
        (venu.photos1@gmail.com). Never distributed to external or newsletter subscribers.
        """
        sender_email = (os.environ.get('SENDER_EMAIL') or self.config.get('sender_email') or 'venu.photos1@gmail.com').strip()
        raw_password = (os.environ.get('SENDER_PASSWORD') or self.config.get('sender_password') or '').strip()
        if not raw_password:
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
            if os.path.exists(env_path):
                try:
                    with open(env_path, 'r') as ef:
                        for line in ef:
                            line = line.strip()
                            if line.startswith('SENDER_PASSWORD='):
                                raw_password = line.split('=', 1)[1].strip().strip('"').strip("'")
                            elif line.startswith('SENDER_EMAIL=') and sender_email == 'your_email@gmail.com':
                                sender_email = line.split('=', 1)[1].strip().strip('"').strip("'")
                except Exception:
                    pass
        sender_password = raw_password.replace(' ', '').replace('"', '').replace("'", "")
        
        target_recipients = recipients or [sender_email]
        if isinstance(target_recipients, str):
            target_recipients = [target_recipients]

        # Also trigger Telegram alert for instant mobile notification
        plain_text = body_text or body_html.replace('<br>', '\n').replace('</p>', '\n').replace('<tr>', '\n')
        import re
        plain_text_clean = re.sub(r'<[^>]+>', '', plain_text).strip()
        self._send_telegram(subject, plain_text_clean)

        if not sender_password or sender_email == "your_email@gmail.com":
            print(f"📧 [DHAN BROKER EMAIL QUEUED] SENDER_PASSWORD not configured. Alert logged:\n   Subject: {subject}")
            return False

        server = None
        try:
            server = self._create_smtp_session(sender_email, sender_password)
            for recipient in target_recipients:
                msg = MIMEMultipart('alternative')
                msg['From'] = f"Dhan Autopilot <{sender_email}>"
                msg['To'] = recipient
                msg['Subject'] = subject

                msg.attach(MIMEText(plain_text_clean, 'plain'))
                msg.attach(MIMEText(body_html, 'html'))

                server.send_message(msg)
                print(f"📧 [DHAN BROKER EMAIL DISPATCHED] Successfully sent to {recipient}: {subject}")
            return True
        except Exception as e:
            print(f"❌ [DHAN SMTP DISPATCH ERROR]: {e}")
            return False
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass
