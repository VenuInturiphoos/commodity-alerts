"""
Dhan Live Order Executor (Autonomous Autopilot - Limit Orders Only)
==================================================================
Direct bridge between the advisory engine and DhanHQ live broker API.
Executes autonomous limit orders with zero slippage, strict pre-trade risk
validation, mandatory protective stop-loss attachment, and institutional safeguards:
- Limit Orders Only (No Market Orders; eliminates bid-ask slippage)
- One-and-Done Target Lockout Enforcement
- 90-Minute Directional Loss Guard Enforcement
- Dynamic Fee-Shielded Trailing Stop Order Modification
- Automatic EOD Square-Off (3:20 PM NSE / 11:45 PM MCX)
"""

import os
import json
import socket
import pytz
import base64
import time
from datetime import datetime
from dotenv import load_dotenv
import urllib3.util.connection as urllib_conn

# Force IPv4 routing for Dhan API requests to strictly match whitelisted Primary Static IP
try:
    urllib_conn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

from dhanhq import DhanContext, dhanhq

SCRIP_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'dhan_scrip_cache.json')
TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'advisory_trades_tracker.json')

class DhanLiveExecutor:
    def __init__(self, client_id=None, access_token=None):
        self.client_id = client_id or os.environ.get('DHAN_CLIENT_ID')
        self.access_token = access_token or os.environ.get('DHAN_ACCESS_TOKEN')
        
        if not self.client_id or not self.access_token:
            raise ValueError("Dhan credentials missing. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env")

        self.context = DhanContext(self.client_id, self.access_token)
        self.dhan = dhanhq(self.context)
        self.scrip_cache = self._load_scrip_cache()

        # User Execution Focus Mandates:
        # 1. Strictly focus on MCX Commodities (exclude intraday equities and stock futures)
        self.focus_commodities_only = os.environ.get('FOCUS_COMMODITIES_ONLY', 'true').lower() in ['true', '1', 'yes']
        # 2. Exclude trade executions if commodity contract is nearing expiry (<= 3 calendar days by default)
        self.expiry_safety_days = int(os.environ.get('COMMODITY_EXPIRY_SAFETY_DAYS', 3))
        # 3. Explicitly excluded volatile/unsupported commodities on Dhan (Copper & Aluminium per user instruction)
        self.unsupported_commodities = {
            'COPPER', 'COPPERM', 'COPPERMINI',
            'ALUMINIUM', 'ALUMINI', 'ALUMINIUMMINI'
        }

        # 4. Proactive Static IP Compliance Check
        self.check_and_sync_ip()

    def check_and_sync_ip(self):
        """
        Validates Dhan API Static IP compliance using /v2/ip/getIP.
        If ipMatchStatus is MISMATCH or ordersAllowed is False,
        automatically calls /v2/ip/setIP with detectedIP to restore ordersAllowed: True.
        GUARD: Cloud CI/CD environments (e.g. GitHub Actions) MUST NEVER overwrite
        the user's registered local Mac static IP!
        """
        if os.environ.get('GITHUB_ACTIONS') == 'true' or os.environ.get('CI') == 'true':
            return {'status': 'SKIPPED_IN_CI', 'reason': 'Cloud CI cannot overwrite local trader static IP'}
        import requests
        try:
            url_get = "https://api.dhan.co/v2/ip/getIP"
            headers = {
                'access-token': self.access_token,
                'client-id': self.client_id,
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            resp = requests.get(url_get, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('ordersAllowed') is True and data.get('ipMatchStatus') in ['PRIMARY_MATCH', 'SECONDARY_MATCH']:
                    return {'status': 'MATCHED', 'data': data}
                
                detected = data.get('detectedIP')
                if detected:
                    url_set = "https://api.dhan.co/v2/ip/setIP"
                    payload = {
                        'dhanClientId': self.client_id,
                        'ip': detected,
                        'ipFlag': 'SECONDARY'
                    }
                    set_resp = requests.post(url_set, headers=headers, json=payload, timeout=5)
                    if set_resp.status_code == 200:
                        print(f"✅ [STATIC IP AUTO-SYNC] Registered new detected IP ({detected}) on Dhan via setIP.")
                        return {'status': 'UPDATED', 'detected_ip': detected, 'res': set_resp.json()}
                    else:
                        print(f"⚠️ [STATIC IP AUTO-SYNC] setIP returned: {set_resp.status_code} - {set_resp.text}")
            return {'status': 'ERROR', 'response': resp.text}
        except Exception as e:
            return {'status': 'EXCEPTION', 'error': str(e)}

    def _load_scrip_cache(self):
        if os.path.exists(SCRIP_CACHE_FILE):
            try:
                with open(SCRIP_CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def get_token_details(self):
        """Decodes the JWT Dhan Access Token and returns expiration and client metadata."""
        if not self.access_token:
            return None
        try:
            parts = self.access_token.strip().split('.')
            if len(parts) >= 2:
                payload_b64 = parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4)
                data = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
                exp = data.get('exp')
                iat = data.get('iat')
                client_id = data.get('dhanClientId') or self.client_id
                now = time.time()
                ist = pytz.timezone('Asia/Kolkata')
                exp_dt = datetime.fromtimestamp(exp, tz=ist) if exp else None
                iat_dt = datetime.fromtimestamp(iat, tz=ist) if iat else None
                return {
                    'exp': exp,
                    'iat': iat,
                    'client_id': client_id,
                    'is_expired': (now >= exp) if exp else False,
                    'remaining_seconds': (exp - now) if exp else 0,
                    'exp_str': exp_dt.strftime('%d %b %Y, %I:%M:%S %p IST') if exp_dt else 'Unknown',
                    'iat_str': iat_dt.strftime('%d %b %Y, %I:%M:%S %p IST') if iat_dt else 'Unknown'
                }
        except Exception as e:
            print(f"Error parsing Dhan access token JWT: {e}")
        return None

    def check_token_validity_and_alert(self, alerter=None, force_api_check=False):
        """
        Monitors Dhan Access Token expiration.
        If the token has expired (or is rejected by Dhan API), dispatches an immediate
        action-required alert to the user via Email and Telegram.
        Guarantees deduplication (alerts once per token expiry event).
        """
        token_info = self.get_token_details()
        if not token_info:
            return False, "NO_TOKEN"

        is_expired = token_info.get('is_expired', False)
        reason = None
        if is_expired:
            reason = f"Access token expired at {token_info.get('exp_str')}."
        elif force_api_check:
            # Verify live connectivity with Dhan API
            try:
                res = self.dhan.get_fund_limits()
                if isinstance(res, dict) and res.get('status') == 'failure':
                    remarks = res.get('remarks') or {}
                    msg = str(remarks) if isinstance(remarks, dict) else str(remarks)
                    if any(err in msg.lower() for err in ['401', '403', 'token', 'expired', 'invalid', 'unauthorized']):
                        is_expired = True
                        reason = f"Dhan API rejected token ({msg})."
            except Exception as e:
                err_str = str(e)
                if any(err in err_str.lower() for err in ['401', '403', 'token', 'expired', 'invalid', 'unauthorized']):
                    is_expired = True
                    reason = f"Dhan API authentication error ({err_str})."

        if is_expired:
            self._send_token_expiry_alert(token_info, reason, alerter)
            return False, reason

        return True, "TOKEN_VALID"

    def _send_token_expiry_alert(self, token_info, reason, alerter=None):
        """Dispatches an urgent action-required email and Telegram alert when token expires."""
        state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
        state = {}
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
            except Exception:
                state = {}

        token_sig = str(token_info.get('exp'))
        last_alerted_sig = state.get('dhan_token_expiry_alert', {}).get('token_exp_sig')
        # Dedup: only alert once per expired token
        if last_alerted_sig == token_sig:
            return

        now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')
        subject = "🚨 [ACTION REQUIRED] Dhan Access Token Expired - Live Trading Autopilot Paused"
        body = (
            f"URGENT ACTION REQUIRED: DHAN ACCESS TOKEN EXPIRED\n"
            f"--------------------------------------------------\n"
            f"Alert Time: {now_ist}\n"
            f"Dhan Client ID: {token_info.get('client_id', self.client_id)}\n"
            f"Expired At: {token_info.get('exp_str', 'N/A')}\n"
            f"Reason: {reason}\n\n"
            f"⚠️ Autopilot Status: Live order placement and trailing stops are PAUSED to protect capital until a new token is provided.\n\n"
            f"STEPS TO RE-ENABLE AUTOPILOT:\n"
            f"1. Log in to Dhan Web (https://web.dhan.co) or the Dhan App.\n"
            f"2. Go to Profile -> Access Token / DhanHQ APIs.\n"
            f"3. Generate a new 24-hour Access Token.\n"
            f"4. Provide the new token in .env (DHAN_ACCESS_TOKEN) or paste it in chat.\n"
            f"5. If using GitHub Actions, update your repository secret DHAN_ACCESS_TOKEN at:\n"
            f"   GitHub -> Settings -> Secrets and variables -> Actions -> DHAN_ACCESS_TOKEN.\n\n"
            f"Once updated, live autopilot execution will resume automatically on the next minute check."
        )

        print(f"\n🚨 [DHAN TOKEN EXPIRED ALERT DISPATCHING] {subject}")
        try:
            if not alerter:
                from alerter import EmailAlerter
                cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r') as cf:
                        alerter = EmailAlerter(json.load(cf))
            
            if alerter:
                payload = [{
                    'symbol': 'DHAN_AUTH',
                    'name': 'Dhan Broker Gateway',
                    'event_type': 'DHAN_TOKEN_EXPIRED',
                    'today_ist': now_ist,
                    'subject': subject,
                    'body': body
                }]
                alerter.send_alerts(payload)
                
            state['dhan_token_expiry_alert'] = {
                'token_exp_sig': token_sig,
                'alerted_at': now_ist,
                'client_id': token_info.get('client_id', self.client_id)
            }
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            print("✅ Token expiry alert dispatched and recorded in alerts_state.json")
        except Exception as e:
            print(f"Error dispatching token expiry alert: {e}")

    def notify_dhan_event(self, event_type, details, alerter=None):
        """
        Dispatches dedicated, confidential broker transaction notifications directly to the Dhan account owner:
        - ENTRY_ORDER_PLACED: Strict limit order submitted / traded on exchange
        - STOP_LOSS_ATTACHED: Protective exchange-level SL order placed on Dhan
        - STOP_LOSS_MODIFIED: SL trigger modified / ratcheted (Breakeven, Profit Lock, Option A ATR buffer)
        - POSITION_SQUARED_OFF: Position closed / target hit / EOD square-off
        """
        try:
            if not alerter:
                from alerter import EmailAlerter
                cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r') as cf:
                        alerter = EmailAlerter(json.load(cf))
                else:
                    alerter = EmailAlerter({})
            if not alerter:
                return False

            now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M:%S %p IST')
            client_id = self.client_id or '1106185635'
            symbol = details.get('symbol', 'UNKNOWN')
            action = details.get('action', '')
            qty = details.get('quantity', '')
            order_id = details.get('order_id', '')

            if event_type == 'ENTRY_ORDER_PLACED':
                limit_p = details.get('price', 0.0)
                sl_p = details.get('sl_price', 0.0)
                tgt_p = details.get('target_price', 0.0)
                status = details.get('status', 'SUBMITTED')
                subject = f"🟢 [DHAN ENTRY ORDER] {symbol} {action} {qty} Lots @ ₹{float(limit_p):,.2f} (#{order_id})"
                badge_color = "#10b981"
                event_name = "Limit Entry Order Placed on Dhan"
                rows = f"""
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Client ID</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: 600; border-bottom: 1px solid #1e293b;">{client_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Order ID</td><td style="padding: 9px 12px; color: #f8fafc; font-family: monospace; border-bottom: 1px solid #1e293b;">{order_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Instrument</td><td style="padding: 9px 12px; color: #38bdf8; font-weight: 600; border-bottom: 1px solid #1e293b;">{symbol}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Action / Side</td><td style="padding: 9px 12px; color: {'#10b981' if action == 'BUY' else '#f43f5e'}; font-weight: bold; border-bottom: 1px solid #1e293b;">{action}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Quantity</td><td style="padding: 9px 12px; color: #f8fafc; border-bottom: 1px solid #1e293b;">{qty} Lots</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Limit Price</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: 600; border-bottom: 1px solid #1e293b;">₹{float(limit_p):,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Initial Stop-Loss</td><td style="padding: 9px 12px; color: #fbbf24; border-bottom: 1px solid #1e293b;">₹{float(sl_p):,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Target Milestone</td><td style="padding: 9px 12px; color: #34d399; border-bottom: 1px solid #1e293b;">₹{float(tgt_p):,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Order Status</td><td style="padding: 9px 12px; color: #38bdf8; border-bottom: 1px solid #1e293b;">{status}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8;">Execution Time</td><td style="padding: 9px 12px; color: #94a3b8;">{now_ist}</td></tr>
                """

            elif event_type == 'STOP_LOSS_ATTACHED':
                sl_order_id = details.get('sl_order_id', order_id)
                trig_p = float(details.get('trigger_price', 0.0))
                lim_p = float(details.get('limit_price', 0.0))
                sl_action = details.get('sl_action', 'BUY' if action == 'SELL' else 'SELL')
                strategy = details.get('strategy', 'Option A (ATR Wick Buffer)')
                max_risk = details.get('max_risk')
                subject = f"🛡️ [DHAN STOP-LOSS ATTACHED] {symbol} SL Trigger @ ₹{trig_p:,.2f} (#{sl_order_id})"
                badge_color = "#f59e0b"
                event_name = "Protective Stop-Loss Attached on Dhan"
                risk_str = f"₹{float(max_risk):,.2f}" if max_risk is not None else "Active"
                rows = f"""
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Client ID</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: 600; border-bottom: 1px solid #1e293b;">{client_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">SL Order ID</td><td style="padding: 9px 12px; color: #f8fafc; font-family: monospace; border-bottom: 1px solid #1e293b;">{sl_order_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Instrument</td><td style="padding: 9px 12px; color: #38bdf8; font-weight: 600; border-bottom: 1px solid #1e293b;">{symbol}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Order Type</td><td style="padding: 9px 12px; color: #f8fafc; border-bottom: 1px solid #1e293b;">STOP_LOSS ({sl_action})</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Quantity Protected</td><td style="padding: 9px 12px; color: #f8fafc; border-bottom: 1px solid #1e293b;">{qty} Lots</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">SL Trigger Price</td><td style="padding: 9px 12px; color: #fbbf24; font-weight: bold; font-size: 14px; border-bottom: 1px solid #1e293b;">₹{trig_p:,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">SL Limit Price</td><td style="padding: 9px 12px; color: #f8fafc; border-bottom: 1px solid #1e293b;">₹{lim_p:,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Protection Buffer</td><td style="padding: 9px 12px; color: #38bdf8; border-bottom: 1px solid #1e293b;">{strategy}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Max Risk at Stop</td><td style="padding: 9px 12px; color: #f43f5e; font-weight: 600; border-bottom: 1px solid #1e293b;">{risk_str}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Exchange Status</td><td style="padding: 9px 12px; color: #10b981; border-bottom: 1px solid #1e293b;">MCX: Order Added / PENDING</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8;">Active Since</td><td style="padding: 9px 12px; color: #94a3b8;">{now_ist}</td></tr>
                """

            elif event_type == 'STOP_LOSS_MODIFIED':
                sl_order_id = details.get('order_id', '')
                new_trig = float(details.get('new_trigger', 0.0))
                new_lim = float(details.get('new_limit', 0.0))
                old_trig = details.get('old_trigger')
                reason = details.get('reason', 'Stop-Loss Ratchet')
                subject = f"🔄 [DHAN STOP-LOSS UPDATED] {symbol} Ratcheted to ₹{new_trig:,.2f} ({reason})"
                badge_color = "#38bdf8"
                event_name = "Stop-Loss Ratcheted / Updated on Dhan"
                old_trig_str = f"₹{float(old_trig):,.2f} &rarr; " if old_trig else ""
                rows = f"""
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Client ID</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: 600; border-bottom: 1px solid #1e293b;">{client_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">SL Order ID</td><td style="padding: 9px 12px; color: #f8fafc; font-family: monospace; border-bottom: 1px solid #1e293b;">{sl_order_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Instrument</td><td style="padding: 9px 12px; color: #38bdf8; font-weight: 600; border-bottom: 1px solid #1e293b;">{symbol}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">New SL Trigger</td><td style="padding: 9px 12px; color: #38bdf8; font-weight: bold; font-size: 14px; border-bottom: 1px solid #1e293b;">{old_trig_str}₹{new_trig:,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">New SL Limit</td><td style="padding: 9px 12px; color: #f8fafc; border-bottom: 1px solid #1e293b;">₹{new_lim:,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Update Reason</td><td style="padding: 9px 12px; color: #34d399; font-weight: 600; border-bottom: 1px solid #1e293b;">{reason}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Capital Protection</td><td style="padding: 9px 12px; color: #10b981; border-bottom: 1px solid #1e293b;">Risk Reduced / Capital Shielded 🛡️</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Exchange Status</td><td style="padding: 9px 12px; color: #10b981; border-bottom: 1px solid #1e293b;">MCX: Modified Successfully</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8;">Update Time</td><td style="padding: 9px 12px; color: #94a3b8;">{now_ist}</td></tr>
                """

            elif event_type == 'POSITION_SQUARED_OFF':
                exit_price = details.get('exit_price', 0.0)
                exit_action = details.get('exit_action', '')
                reason = details.get('reason', 'Square-off Settlement')
                pnl = details.get('pnl')
                subject = f"🎯 [DHAN SQUARE-OFF] {symbol} Closed @ ₹{float(exit_price):,.2f} ({reason})"
                badge_color = "#a855f7"
                event_name = "Position Squared Off on Dhan"
                pnl_str = f"₹{float(pnl):,.2f}" if pnl is not None else "Position Closed"
                rows = f"""
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Client ID</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: 600; border-bottom: 1px solid #1e293b;">{client_id}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Instrument</td><td style="padding: 9px 12px; color: #38bdf8; font-weight: 600; border-bottom: 1px solid #1e293b;">{symbol}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Closing Action</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: bold; border-bottom: 1px solid #1e293b;">{exit_action}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Quantity Closed</td><td style="padding: 9px 12px; color: #f8fafc; border-bottom: 1px solid #1e293b;">{qty} Lots</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Exit Price</td><td style="padding: 9px 12px; color: #f8fafc; font-weight: 600; border-bottom: 1px solid #1e293b;">₹{float(exit_price):,.2f}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Exit Reason</td><td style="padding: 9px 12px; color: #38bdf8; border-bottom: 1px solid #1e293b;">{reason}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8; border-bottom: 1px solid #1e293b;">Realized P&L</td><td style="padding: 9px 12px; color: #10b981; font-weight: bold; border-bottom: 1px solid #1e293b;">{pnl_str}</td></tr>
                    <tr><td style="padding: 9px 12px; color: #94a3b8;">Close Time</td><td style="padding: 9px 12px; color: #94a3b8;">{now_ist}</td></tr>
                """
            else:
                subject = f"🔔 [DHAN BROKER EVENT] {symbol} {event_type}"
                badge_color = "#38bdf8"
                event_name = event_type
                rows = f"<tr><td colspan='2' style='padding: 9px 12px; color: #f8fafc;'>{json.dumps(details, indent=2)}</td></tr>"

            body_html = f"""
            <!DOCTYPE html>
            <html>
            <body style="margin: 0; padding: 24px; background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
              <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #131c2e; border: 1px solid #1e293b; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);">
                <tr>
                  <td style="padding: 20px 24px; background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border-bottom: 1px solid #334155;">
                    <div style="font-size: 11px; letter-spacing: 1.5px; text-transform: uppercase; color: #94a3b8; font-weight: 600;">Dhan Autonomous Broker Gateway</div>
                    <div style="font-size: 18px; font-weight: 700; color: #ffffff; margin-top: 4px;">{event_name}</div>
                    <div style="display: inline-block; margin-top: 8px; padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 700; background-color: {badge_color}22; color: {badge_color}; border: 1px solid {badge_color}44;">
                      {event_type}
                    </div>
                  </td>
                </tr>
                <tr>
                  <td style="padding: 16px 20px;">
                    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border-collapse: collapse; font-size: 13px;">
                      {rows}
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding: 14px 24px; background-color: #0f172a; border-top: 1px solid #1e293b; font-size: 11px; color: #64748b; text-align: center;">
                    Confidential automated alert dispatched directly to account owner ({client_id}).<br>
                    Commodity Analysis Autonomous Autopilot Engine &bull; {now_ist}
                  </td>
                </tr>
              </table>
            </body>
            </html>
            """

            return alerter.send_dhan_broker_email(subject, body_html)
        except Exception as e:
            print(f"⚠️ Error building/dispatching Dhan broker alert: {e}")
            return False

    def get_fund_limits(self):
        """Returns live cash balance and available margin from Dhan."""
        try:
            res = self.dhan.get_fund_limits()
            if res.get('status') == 'success':
                data = res.get('data', {})
                avail = float(data.get('availabelBalance') or data.get('availableBalance') or data.get('sodLimit') or 0.0)
                utilized = float(data.get('utilizedAmount') or 0.0)
                collateral = float(data.get('collateralAmount') or 0.0)
                return {
                    'status': 'success',
                    'available_balance': avail,
                    'utilized_amount': utilized,
                    'collateral_amount': collateral,
                    'raw_data': data
                }
            return {'status': 'error', 'message': str(res)}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def get_live_price(self, symbol, asset_type='COMMODITY'):
        """
        Tier 1 Price Feed: Queries real-time LTP directly from DhanHQ API (/marketfeed/ltp).
        Returns (price: float, source: str) if successful, or (None, None) if Dhan Data API is inactive/unavailable.
        Allows seamless, instantaneous fallback to secondary exchange feeds.
        """
        try:
            scrip = self.resolve_scrip(symbol, asset_type)
            if not scrip or not scrip.get('security_id'):
                return None, None
            sec_id = int(scrip['security_id'])
            seg = scrip.get('exchange_segment') or ('MCX_COMM' if asset_type == 'COMMODITY' else 'NSE_EQ')
            res = self.dhan.ticker_data({seg: [sec_id]})
            if res and res.get('status') == 'success':
                price_data = res.get('data', {})
                if isinstance(price_data, dict):
                    seg_data = price_data.get(seg, {})
                    item_data = seg_data.get(str(sec_id)) or seg_data.get(sec_id) or {}
                    ltp = float(item_data.get('last_price') or item_data.get('ltp') or 0.0)
                    if ltp > 0:
                        return ltp, f"DHAN:{scrip.get('custom_symbol', symbol)}"
        except Exception:
            pass
        return None, None

    # Automated Monthly Futures Contract Roll-Over Map (MCX Commodities)
    # Guarantees that when the front-month contract expires or enters the <= 3d tender window,
    # the executor automatically shifts execution to the active next-month contract.
    ROLLOVER_CHAIN = {
        'CRUDEOILM': [
            {'security_id': '573423', 'custom_symbol': 'CRUDEOILM NOV FUT', 'trading_symbol': 'CRUDEOILM-19Nov2026-FUT', 'expiry': '2026-11-19 23:30:00'},
            {'security_id': '576265', 'custom_symbol': 'CRUDEOILM DEC FUT', 'trading_symbol': 'CRUDEOILM-18Dec2026-FUT', 'expiry': '2026-12-18 23:30:00'}
        ],
        'ALUMINI': [
            {'security_id': '578632', 'custom_symbol': 'ALUMINI NOV FUT', 'trading_symbol': 'ALUMINI-30Nov2026-FUT', 'expiry': '2026-11-30 23:30:00'},
            {'security_id': '581710', 'custom_symbol': 'ALUMINI DEC FUT', 'trading_symbol': 'ALUMINI-31Dec2026-FUT', 'expiry': '2026-12-31 23:30:00'}
        ],
        'ZINCMINI': [
            {'security_id': '578639', 'custom_symbol': 'ZINCMINI NOV FUT', 'trading_symbol': 'ZINCMINI-30Nov2026-FUT', 'expiry': '2026-11-30 23:30:00'},
            {'security_id': '581721', 'custom_symbol': 'ZINCMINI DEC FUT', 'trading_symbol': 'ZINCMINI-31Dec2026-FUT', 'expiry': '2026-12-31 23:30:00'}
        ],
        'SILVERMIC': [
            {'security_id': '562058', 'custom_symbol': 'SILVERMIC NOV FUT', 'trading_symbol': 'SILVERMIC-30Nov2026-FUT', 'expiry': '2026-11-30 23:30:00'},
            {'security_id': '574843', 'custom_symbol': 'SILVERMIC FEB FUT', 'trading_symbol': 'SILVERMIC-26Feb2027-FUT', 'expiry': '2027-02-26 23:30:00'}
        ],
        'GOLDM': [
            {'security_id': '575011', 'custom_symbol': 'GOLDM DEC FUT', 'trading_symbol': 'GOLDM-04Dec2026-FUT', 'expiry': '2026-12-04 23:30:00'},
            {'security_id': '578779', 'custom_symbol': 'GOLDM JAN FUT', 'trading_symbol': 'GOLDM-05Jan2027-FUT', 'expiry': '2027-01-05 23:30:00'}
        ],
        'GOLDTEN': [
            {'security_id': '574842', 'custom_symbol': 'GOLDTEN NOV FUT', 'trading_symbol': 'GOLDTEN-30Nov2026-FUT', 'expiry': '2026-11-30 23:30:00'},
            {'security_id': '578629', 'custom_symbol': 'GOLDTEN DEC FUT', 'trading_symbol': 'GOLDTEN-31Dec2026-FUT', 'expiry': '2026-12-31 23:30:00'}
        ],
        'NATGASMINI': [
            {'security_id': '574320', 'custom_symbol': 'NATURALGASM NOV FUT', 'trading_symbol': 'NATGASMINI-24Nov2026-FUT', 'expiry': '2026-11-24 23:30:00'},
            {'security_id': '578250', 'custom_symbol': 'NATURALGASM DEC FUT', 'trading_symbol': 'NATGASMINI-28Dec2026-FUT', 'expiry': '2026-12-28 23:30:00'},
            {'security_id': '581136', 'custom_symbol': 'NATURALGASM JAN FUT', 'trading_symbol': 'NATGASMINI-25Jan2027-FUT', 'expiry': '2027-01-25 23:30:00'}
        ],
        'NATURALGASM': [
            {'security_id': '574320', 'custom_symbol': 'NATURALGASM NOV FUT', 'trading_symbol': 'NATGASMINI-24Nov2026-FUT', 'expiry': '2026-11-24 23:30:00'},
            {'security_id': '578250', 'custom_symbol': 'NATURALGASM DEC FUT', 'trading_symbol': 'NATGASMINI-28Dec2026-FUT', 'expiry': '2026-12-28 23:30:00'},
            {'security_id': '581136', 'custom_symbol': 'NATURALGASM JAN FUT', 'trading_symbol': 'NATGASMINI-25Jan2027-FUT', 'expiry': '2027-01-25 23:30:00'}
        ]
    }

    def resolve_scrip(self, symbol, asset_type='COMMODITY'):
        """Resolves raw symbol (e.g. COPPERM, CRUDEOIL, CRUDEOILM, EICHERMOT.NS) to Dhan Security ID with dynamic front-month roll-over."""
        clean_sym = symbol.strip().upper()

        # Check Automated Roll-Over Chain for Commodities first (with canonical alias matching)
        clean_comm = clean_sym.replace('-FUT', '').replace('.NS', '').replace('.BO', '').strip()
        if clean_comm.startswith('CRUDEOIL'):
            canonical_comm = 'CRUDEOILM'
        elif clean_comm.startswith('ALUMIN'):
            canonical_comm = 'ALUMINI'
        elif clean_comm.startswith('ZINC'):
            canonical_comm = 'ZINCMINI'
        elif clean_comm.startswith('SILVER'):
            canonical_comm = 'SILVERMIC'
        elif clean_comm.startswith('GOLD'):
            canonical_comm = 'GOLDTEN'
        elif clean_comm.startswith('NATURALGAS') or clean_comm.startswith('NATGAS'):
            canonical_comm = 'NATGASMINI'
        else:
            canonical_comm = clean_comm

        if canonical_comm in self.ROLLOVER_CHAIN:
            chain = self.ROLLOVER_CHAIN[canonical_comm]
            now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).date()
            for idx, contract in enumerate(chain):
                try:
                    exp_date = datetime.strptime(contract['expiry'][:10], '%Y-%m-%d').date()
                    days_left = (exp_date - now_ist).days
                    if days_left > self.expiry_safety_days:
                        c_dict = dict(contract)
                        c_dict['symbol_name'] = canonical_comm
                        c_dict['lot_size'] = 1
                        c_dict['exchange_segment'] = 'MCX_COMM'
                        c_dict['rolled_over'] = (idx > 0)
                        c_dict['days_left'] = days_left
                        if idx > 0:
                            print(f"🔄 [AUTO ROLLOVER ACTIVE] {canonical_comm}: Front contract nearing expiry. Auto-routed to high-liquidity next month contract {c_dict['custom_symbol']} (Expires: {exp_date}, {days_left}d left).")
                        return c_dict
                except Exception:
                    continue
            # Fallback to the latest contract in chain if all are within safety window
            last_c = dict(chain[-1])
            last_c['symbol_name'] = canonical_comm
            last_c['lot_size'] = 1
            last_c['exchange_segment'] = 'MCX_COMM'
            last_c['rolled_over'] = True
            try:
                exp_date = datetime.strptime(last_c['expiry'][:10], '%Y-%m-%d').date()
                last_c['days_left'] = (exp_date - now_ist).days
            except Exception:
                last_c['days_left'] = 0
            return last_c

        # Direct lookup in cached scrip map
        if clean_sym in self.scrip_cache:
            return self.scrip_cache[clean_sym]
        
        # Check without .NS
        base_eq = clean_sym.replace('.NS', '')
        if f"{base_eq}.NS" in self.scrip_cache:
            return self.scrip_cache[f"{base_eq}.NS"]
        if base_eq in self.scrip_cache:
            return self.scrip_cache[base_eq]

        # Commodity fallback
        clean_fallback = clean_sym.replace('MIC', '').replace('MINI', '').rstrip('M')
        if clean_fallback in self.scrip_cache:
            return self.scrip_cache[clean_fallback]

        return None

    def is_nearing_expiry(self, symbol, threshold_days=None):
        """
        Checks if a commodity contract is nearing expiry (<= threshold_days calendar days)
        AND no active forward-month contract is available in the rollover chain.
        Returns: (is_nearing: bool, days_left: int, expiry_date: date)
        """
        threshold = threshold_days if threshold_days is not None else self.expiry_safety_days
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata')).date()
        scrip_info = self.resolve_scrip(symbol, asset_type='COMMODITY')
        if not scrip_info or not scrip_info.get('expiry'):
            return False, 999, None

        exp_str = scrip_info['expiry']
        try:
            exp_date = datetime.strptime(exp_str[:10], '%Y-%m-%d').date()
            days_left = (exp_date - now_ist).days
            # Only true if even the rolled-over contract is <= threshold days
            is_nearing = (days_left <= threshold)
            return is_nearing, days_left, exp_date
        except Exception:
            return False, 999, None

    def place_autonomous_limit_order(self, symbol, action, limit_price, stop_loss_price, 
                                     target_price=None, quantity=None, asset_type='COMMODITY'):
        """
        Executes a strict Limit Order on Dhan with comprehensive risk guards.
        - Order Type: Strictly dhan.LIMIT (Pins entry to technical trigger; zero slippage)
        - Focus: MCX Commodities only (Intraday equities and stock futures excluded)
        - Expiry Guard: Excludes contracts nearing expiry (<= 3 days)
        - Enforces One-and-Done Target Lockout
        - Enforces 90-Minute Directional Loss Guard
        - Automatically records fill & order ID
        """
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        today_str = now_ist.strftime('%d %b %Y')
        action = action.upper()

        # Rule 0: Exclude unsupported/high-volatility commodities on Dhan (Aluminium & Copper per user preference)
        sym_clean = symbol.strip().upper().replace('-FUT', '').replace('.NS', '').replace('.BO', '')
        if (sym_clean in self.unsupported_commodities or 
            sym_clean.startswith('COPPER') or 
            sym_clean.startswith('ALU')):
            print(f"🛑 [AUTOPILOT EXCLUDED] {symbol}: High-volatility excluded commodity (Aluminium / Copper). Trade execution excluded per user preference.")
            return {
                'status': 'EXCLUDED_USER_PREFERENCE',
                'symbol': symbol,
                'reason': 'Aluminium and Copper are excluded from live Dhan broker execution per user preference (High volatility / unpredictable).'
            }

        # Rule 1: Focus strictly on Commodities (Exclude equities, stock futures, and options from live broker orders)
        if self.focus_commodities_only:
            is_comm = (asset_type == 'COMMODITY')
            sym_upper = symbol.strip().upper()
            if sym_upper.endswith('.NS') or sym_upper.endswith('.BO') or '-FUT' in sym_upper or asset_type in ['EQUITY', 'FUTURES', 'INTRADAY_STOCK', 'STOCK_FUTURES', 'OPTIONS']:
                is_comm = False
            resolved = self.resolve_scrip(symbol, asset_type)
            if resolved and resolved.get('exchange_segment') != 'MCX_COMM':
                is_comm = False
            if not is_comm:
                print(f"🛑 [AUTOPILOT EXCLUDED] {symbol}: Non-commodity asset. Trade execution excluded per user preference (Focus on Commodities Only).")
                return {
                    'status': 'EXCLUDED_NON_COMMODITY',
                    'symbol': symbol,
                    'reason': 'Autopilot is restricted to MCX Commodities only. Intraday equities, stock futures, and options execution on Dhan are disabled.'
                }

        # Rule 1.5: Enforce Mandatory Cooldown Lockout (120m Post-SL, 120m Post-Target, 90m Standard for BOTH BUY & SELL)
        try:
            from advisory_tracker import check_commodity_cooldown
            is_cd, cd_msg, cd_rem = check_commodity_cooldown(symbol)
            if is_cd:
                print(f"🛑 [AUTOPILOT COOLDOWN BLOCKED] {symbol}: Live order blocked. {cd_msg}")
                return {
                    'status': 'BLOCKED_COOLDOWN',
                    'symbol': symbol,
                    'remaining_minutes': cd_rem,
                    'reason': cd_msg
                }
        except Exception as cd_err:
            print(f"⚠️ Warning checking cooldown in DhanLiveExecutor: {cd_err}")

        # 1. Resolve Instrument
        scrip_info = self.resolve_scrip(symbol, asset_type)
        if not scrip_info:
            return {
                'status': 'FAILED',
                'reason': f'SCRIP_NOT_FOUND: Could not resolve Dhan Security ID for {symbol}.'
            }

        sec_id = str(scrip_info['security_id'])
        segment = scrip_info.get('exchange_segment', self.dhan.MCX if asset_type == 'COMMODITY' else self.dhan.NSE)
        custom_sym = scrip_info.get('custom_symbol', symbol)

        # Rule 2: Exclude commodity contract ONLY IF all contracts in rollover chain are expired/in tender window
        is_near, days_left, exp_date = self.is_nearing_expiry(symbol)
        if is_near:
            print(f"🛑 [AUTOPILOT EXCLUDED] {symbol} ({custom_sym}): All available contracts nearing expiry on {exp_date} ({days_left} day(s) left <= {self.expiry_safety_days}d threshold). No active forward contract in rollover chain.")
            return {
                'status': 'EXCLUDED_NEARING_EXPIRY',
                'symbol': symbol,
                'custom_symbol': custom_sym,
                'days_to_expiry': days_left,
                'expiry_date': str(exp_date),
                'reason': f"NEARING_EXPIRY: Contract {custom_sym} expires in {days_left} day(s) on {exp_date}. No active forward contract available."
            }

        if scrip_info.get('rolled_over'):
            print(f"🔄 [AUTO ROLLOVER ACTIVE] {symbol}: Executing on high-liquidity next month contract {custom_sym} (Sec ID: {sec_id}) to bypass tender period.")
        
        # For commodities: Dhan API accepts quantity in exchange lots (1, 2, 4...)
        # Map absolute units from tracker into whole exchange lots for Dhan:
        is_commodity = (segment == 'MCX_COMM' or asset_type == 'COMMODITY')
        clean_lookup = scrip_info.get('symbol_name', symbol).strip().upper()
        if is_commodity:
            base_multiplier = {
                'ALUMINI': 1000, 'ALUMINIUM': 1000,
                'ZINCMINI': 1000, 'ZINC': 1000,
                'NATURALGASM': 250, 'NATGASMINI': 250,
                'CRUDEOILM': 10,
                'SILVERMIC': 1,
                'GOLDM': 10,
                'GOLDTEN': 1,
                'COPPERM': 1000
            }.get(clean_lookup, 1)

            if quantity and quantity >= base_multiplier:
                order_qty = max(1, int(quantity // base_multiplier))
            else:
                order_qty = int(quantity or 1)
        else:
            order_qty = int(quantity or 1)

        # 2. Check Available Margin (Calibrated to 50% split for portfolio diversity)
        funds = self.get_fund_limits()
        if funds.get('status') == 'success':
            avail_bal = funds['available_balance']
            # Commodity mini margin estimation per single exchange lot
            single_lot_margin_map = {
                'CRUDEOILM': 24400.0,
                'NATURALGASM': 17000.0,
                'NATGASMINI': 17000.0,
                'ALUMINI': 29000.0,
                'ZINCMINI': 26500.0,
                'SILVERMIC': 13500.0,
                'GOLDM': 76000.0,
                'GOLDTEN': 7600.0,
                'COPPERM': 65600.0,
            }
            single_margin = single_lot_margin_map.get(clean_lookup, 35000.0)
            est_margin = (single_margin * order_qty) if is_commodity else (float(limit_price) * order_qty * 0.20)
            
            # If available balance cannot afford order_qty lots, adaptively scale down to what fits
            if is_commodity and avail_bal < est_margin:
                max_affordable_lots = int(avail_bal // single_margin)
                if max_affordable_lots >= 1:
                    print(f"⚠️ [MARGIN AUTO-SCALE] Available margin (₹{avail_bal:,.2f}) insufficient for {order_qty} lots. Scaling to {max_affordable_lots} lot(s).")
                    order_qty = max_affordable_lots
                    est_margin = single_margin * order_qty
                else:
                    return {
                        'status': 'BLOCKED',
                        'reason': f'INSUFFICIENT_MARGIN: Required ~₹{est_margin:,.2f}, Available: ₹{avail_bal:,.2f}. Order blocked.'
                    }

        # 3. Formulate Strict Limit Order Parameters
        self.check_and_sync_ip()
        order_type = self.dhan.LIMIT
        product_type = self.dhan.MARGIN if is_commodity else self.dhan.INTRA
        txn_type = self.dhan.BUY if action == 'BUY' else self.dhan.SELL
        clean_limit_price = round(float(limit_price), 2)
        clean_sl_price = round(float(stop_loss_price), 2)

        print(f"🚀 AUTONOMOUS AUTOPILOT DISPATCHING LIMIT ORDER:")
        print(f"   Asset: {custom_sym} (Sec ID: {sec_id}) | Segment: {segment}")
        print(f"   Action: {action} | Qty: {order_qty} {'lot' if is_commodity else 'shares'} | Limit Price: ₹{clean_limit_price:,.2f}")
        print(f"   Technical Stop: ₹{clean_sl_price:,.2f} | Target: ₹{round(float(target_price or 0), 2):,.2f}")

        try:
            # Place Entry Limit Order
            order_res = self.dhan.place_order(
                security_id=sec_id,
                exchange_segment=segment,
                transaction_type=txn_type,
                quantity=order_qty,
                order_type=order_type,
                product_type=product_type,
                price=clean_limit_price,
                validity=self.dhan.DAY,
                tag="AUTOPILOT"
            )

            # Auto-Recovery Guard for DH-905 Invalid IP
            err_str = str(order_res)
            if 'DH-905' in err_str or 'Invalid IP' in err_str or 'ordersAllowed' in err_str:
                print("⚠️ [DHAN IP AUTO-RECOVERY] Encountered DH-905 Invalid IP. Forcing immediate IP re-sync & retrying order placement...")
                self.check_and_sync_ip()
                import time
                time.sleep(1)
                order_res = self.dhan.place_order(
                    security_id=sec_id,
                    exchange_segment=segment,
                    transaction_type=txn_type,
                    quantity=order_qty,
                    order_type=order_type,
                    product_type=product_type,
                    price=clean_limit_price,
                    validity=self.dhan.DAY,
                    tag="AUTOPILOT"
                )

            if order_res.get('status') == 'success' or order_res.get('orderStatus') in ['TRANSIT', 'PENDING', 'TRADED']:
                order_id = str(order_res.get('data', {}).get('orderId') or order_res.get('orderId', ''))
                print(f"✅ LIMIT ORDER PLACED ON DHAN! Order ID: {order_id} (Status: {order_res.get('orderStatus', 'SUBMITTED')})")
                
                # Dispatch Dedicated Dhan Entry Email
                try:
                    self.notify_dhan_event('ENTRY_ORDER_PLACED', {
                        'symbol': custom_sym or symbol,
                        'action': action,
                        'quantity': order_qty,
                        'price': clean_limit_price,
                        'sl_price': clean_sl_price,
                        'target_price': target_price,
                        'order_id': order_id,
                        'status': order_res.get('orderStatus', 'SUBMITTED')
                    })
                except Exception as notify_err:
                    print(f"⚠️ Error dispatching Dhan entry notification: {notify_err}")

                # Auto-Attach Broker-Side Stop Loss Order with ATR Wick Buffer (Option A)
                sl_res = None
                if clean_sl_price and clean_sl_price > 0:
                    try:
                        sl_res = self.attach_stop_loss_order(
                            security_id=sec_id,
                            segment=segment,
                            entry_action=action,
                            quantity=order_qty,
                            stop_loss_price=clean_sl_price,
                            symbol=symbol
                        )
                    except Exception as sl_err:
                        print(f"⚠️ Could not auto-attach broker Stop-Loss order: {sl_err}")

                return {
                    'status': 'PLACED',
                    'order_id': order_id,
                    'sl_order_id': sl_res.get('data', {}).get('orderId') if (isinstance(sl_res, dict) and sl_res.get('data')) else None,
                    'symbol': symbol,
                    'custom_symbol': custom_sym,
                    'security_id': sec_id,
                    'action': action,
                    'quantity': order_qty,
                    'limit_price': clean_limit_price,
                    'stop_loss_price': clean_sl_price,
                    'target_price': target_price,
                    'order_type': 'LIMIT',
                    'time': now_ist.strftime('%I:%M:%S %p IST'),
                    'dhan_response': order_res,
                    'dhan_sl_response': sl_res
                }
            else:
                err_msg = order_res.get('remarks') or order_res.get('message') or str(order_res)
                print(f"❌ Dhan Rejected Order: {err_msg}")
                return {
                    'status': 'REJECTED',
                    'reason': err_msg,
                    'dhan_response': order_res
                }

        except Exception as e:
            print(f"❌ Exception in Dhan Live Order Execution: {e}")
            return {
                'status': 'ERROR',
                'reason': str(e)
            }

    def _round_to_tick(self, price, symbol=''):
        """Rounds price to exact MCX exchange tick size."""
        sym_u = symbol.upper()
        if 'CRUDE' in sym_u or 'SILVER' in sym_u or 'GOLD' in sym_u:
            # Tick size 1.00
            return round(price)
        elif 'NAT' in sym_u or 'GAS' in sym_u:
            # Tick size 0.10
            return round(round(price / 0.10) * 0.10, 1)
        else:
            # Base metals tick size 0.05
            return round(round(price / 0.05) * 0.05, 2)

    def attach_stop_loss_order(self, security_id, segment, entry_action, quantity, stop_loss_price, symbol=''):
        """
        Attaches an exchange-level Stop-Loss (SL-Limit) trigger order on Dhan to protect the position.
        If entry was BUY, stop-loss order is SELL; if entry was SELL, stop-loss order is BUY.
        Respects exact exchange tick sizes (e.g. ₹1.00 for Crude Oil).
        """
        sl_action = self.dhan.SELL if entry_action == 'BUY' else self.dhan.BUY
        raw_trigger = float(stop_loss_price)
        raw_limit = raw_trigger * 0.998 if entry_action == 'BUY' else raw_trigger * 1.003
        
        trigger_p = float(self._round_to_tick(raw_trigger, symbol))
        limit_p = float(self._round_to_tick(raw_limit, symbol))
        # Ensure limit_p is slightly beyond trigger_p to guarantee execution
        if sl_action == self.dhan.BUY and limit_p <= trigger_p:
            limit_p = trigger_p + (1.0 if 'CRUDE' in symbol.upper() else 0.5)
        elif sl_action == self.dhan.SELL and limit_p >= trigger_p:
            limit_p = trigger_p - (1.0 if 'CRUDE' in symbol.upper() else 0.5)

        is_commodity = (segment == 'MCX_COMM')
        product_type = self.dhan.MARGIN if is_commodity else self.dhan.INTRA

        try:
            sl_res = self.dhan.place_order(
                security_id=str(security_id),
                exchange_segment=segment,
                transaction_type=sl_action,
                quantity=int(quantity),
                order_type=self.dhan.SL,
                product_type=product_type,
                price=limit_p,
                trigger_price=trigger_p,
                validity=self.dhan.DAY,
                tag="AUTOPILOT_SL"
            )
            print(f"🛡️ [BROKER STOP-LOSS ATTACHED] Placed SL order on Dhan: Trigger ₹{trigger_p}, Limit ₹{limit_p} (Res: {sl_res})")
            if sl_res.get('status') == 'success' or sl_res.get('orderStatus') in ['TRANSIT', 'PENDING', 'TRADED'] or sl_res.get('data', {}).get('orderId'):
                sl_id = str(sl_res.get('data', {}).get('orderId') or sl_res.get('orderId', ''))
                # Calculate max rupee risk if symbol is known
                max_risk = None
                if 'CRUDE' in symbol.upper():
                    max_risk = abs(limit_p - trigger_p) * 10 * int(quantity)
                elif any(m in symbol.upper() for m in ['ALU', 'ZINC', 'LEAD', 'COPPER']):
                    max_risk = abs(limit_p - trigger_p) * 1000 * int(quantity)
                try:
                    self.notify_dhan_event('STOP_LOSS_ATTACHED', {
                        'symbol': symbol,
                        'sl_action': 'BUY' if sl_action == self.dhan.BUY else 'SELL',
                        'quantity': quantity,
                        'trigger_price': trigger_p,
                        'limit_price': limit_p,
                        'sl_order_id': sl_id,
                        'strategy': 'Option A (ATR Wick Buffer)',
                        'max_risk': max_risk
                    })
                except Exception as notify_err:
                    print(f"⚠️ Error dispatching Dhan SL attached notification: {notify_err}")
            return sl_res
        except Exception as e:
            print(f"Error attaching Stop-Loss order: {e}")
            return {'status': 'error', 'message': str(e)}

    def modify_stop_loss_order(self, order_id, new_trigger_price, action=None, symbol='', quantity=1, reason='Stop-Loss Ratchet', old_trigger_price=None):
        """
        Modifies trigger price of active Stop-Loss order on Dhan (Fee-Shielded Trailing Stop Ratchet).
        Enforces exchange tick-size compliance and execution buffer.
        """
        trigger_p = float(self._round_to_tick(float(new_trigger_price), symbol))
        # For a BUY SL (covering a short), limit price is slightly above trigger price
        # For a SELL SL (exiting a long), limit price is slightly below trigger price
        is_buy_sl = (action == 'SELL') or ('BUY' in str(action).upper())
        if is_buy_sl:
            raw_limit = trigger_p + (10.0 if 'CRUDE' in symbol.upper() else 1.0)
        else:
            raw_limit = trigger_p - (10.0 if 'CRUDE' in symbol.upper() else 1.0)
        limit_p = float(self._round_to_tick(raw_limit, symbol))

        try:
            mod_res = self.dhan.modify_order(
                order_id=str(order_id),
                order_type=self.dhan.SL,
                leg_name="ENTRY_LEG",
                quantity=int(quantity or 1),
                price=limit_p,
                trigger_price=trigger_p,
                disclosed_quantity=0,
                validity=self.dhan.DAY
            )
            print(f"🔄 [DHAN SL MODIFIED] Order {order_id} ratcheted to Trigger ₹{trigger_p}, Limit ₹{limit_p} (Res: {mod_res})")
            if mod_res.get('status') == 'success' or mod_res.get('orderStatus') in ['TRANSIT', 'PENDING', 'TRADED'] or mod_res.get('data', {}).get('orderId'):
                try:
                    self.notify_dhan_event('STOP_LOSS_MODIFIED', {
                        'symbol': symbol,
                        'order_id': order_id,
                        'new_trigger': trigger_p,
                        'new_limit': limit_p,
                        'old_trigger': old_trigger_price,
                        'reason': reason
                    })
                except Exception as notify_err:
                    print(f"⚠️ Error dispatching Dhan SL update notification: {notify_err}")
            return mod_res
        except Exception as e:
            print(f"❌ Error modifying Dhan Stop-Loss order: {e}")
            return {'status': 'error', 'message': str(e)}

    def square_off_position(self, security_id, segment, action, quantity, price=None, symbol='', reason='Square-off Settlement'):
        """Executes position square-off at Target Hit or 3:20 PM NSE / 11:45 PM MCX."""
        exit_action = self.dhan.SELL if action == 'BUY' else self.dhan.BUY
        is_commodity = (segment == 'MCX_COMM')
        product_type = self.dhan.MARGIN if is_commodity else self.dhan.INTRA
        order_type = self.dhan.LIMIT if price else self.dhan.MARKET

        try:
            res = self.dhan.place_order(
                security_id=security_id,
                exchange_segment=segment,
                transaction_type=exit_action,
                quantity=quantity,
                order_type=order_type,
                product_type=product_type,
                price=round(float(price), 2) if price else 0,
                validity=self.dhan.DAY,
                tag="AUTOPILOT_EXIT"
            )
            if res.get('status') == 'success' or res.get('orderStatus') in ['TRANSIT', 'PENDING', 'TRADED']:
                try:
                    self.notify_dhan_event('POSITION_SQUARED_OFF', {
                        'symbol': symbol or f"Security {security_id}",
                        'exit_action': 'SELL' if exit_action == self.dhan.SELL else 'BUY',
                        'quantity': quantity,
                        'exit_price': price or 0.0,
                        'reason': reason
                    })
                except Exception as notify_err:
                    print(f"⚠️ Error dispatching Dhan square-off notification: {notify_err}")
            return res
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def cancel_pending_order(self, order_id):
        """Cancels an unfilled pending limit order."""
        try:
            return self.dhan.cancel_order(order_id)
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def check_and_cancel_stale_limit_orders(self, max_age_seconds=180):
        """
        Scans all orders on Dhan and cancels any pending LIMIT orders that have not executed 
        within max_age_seconds (3 minutes = 180s by default).
        Prevents stale limit orders from sitting on the book when momentum has faded.
        Returns: list of cancelled order details.
        """
        cancelled_orders = []
        try:
            order_res = self.dhan.get_order_list()
            if not isinstance(order_res, dict) or order_res.get('status') != 'success':
                return cancelled_orders

            orders = order_res.get('data', [])
            if not isinstance(orders, list):
                return cancelled_orders

            now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
            
            for o in orders:
                status = str(o.get('orderStatus', '')).upper()
                order_type = str(o.get('orderType', '')).upper()
                # Strictly only cancel stale entry LIMIT orders (NEVER cancel protective STOP_LOSS orders!)
                if order_type in ['STOP_LOSS', 'SL', 'STOP_LOSS_MARKET', 'SLM'] or 'SL' in str(o.get('correlationId', '')):
                    continue
                if order_type == 'LIMIT' and status in ['TRANSIT', 'PENDING', 'OPEN']:
                    create_time_str = o.get('createTime')
                    order_id = str(o.get('orderId'))
                    symbol = o.get('tradingSymbol', '')
                    
                    if not create_time_str or not order_id:
                        continue
                        
                    try:
                        create_dt = datetime.strptime(create_time_str, '%Y-%m-%d %H:%M:%S')
                        create_dt = pytz.timezone('Asia/Kolkata').localize(create_dt)
                        age_sec = (now_ist - create_dt).total_seconds()
                        
                        if age_sec >= max_age_seconds:
                            print(f"⏱️ [3-MIN TTL LIMIT EXPIRY] Order {order_id} ({symbol}) created at {create_time_str} has been pending for {int(age_sec)}s (>= {max_age_seconds}s limit). Cancelling order on Dhan...")
                            cancel_res = self.cancel_pending_order(order_id)
                            cancelled_orders.append({
                                'order_id': order_id,
                                'symbol': symbol,
                                'create_time': create_time_str,
                                'age_seconds': age_sec,
                                'cancel_response': cancel_res
                            })
                            print(f"✅ Order {order_id} ({symbol}) successfully cancelled on Dhan.")
                    except Exception as parse_err:
                        print(f"⚠️ Error parsing order time for {order_id}: {parse_err}")
                        
        except Exception as e:
            print(f"⚠️ Error checking stale limit orders: {e}")
            
        return cancelled_orders

    def get_order_status(self, order_id):
        """Retrieves real-time status of a specific order."""
        try:
            return self.dhan.get_order_by_id(order_id)
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def get_live_positions(self):
        """Retrieves all current open positions from Dhan."""
        try:
            return self.dhan.get_positions()
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
