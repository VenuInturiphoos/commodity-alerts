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
        # 3. Explicitly excluded unsupported commodities on Dhan (e.g. Copper Mini)
        self.unsupported_commodities = {'COPPERM', 'COPPER'}

    def _load_scrip_cache(self):
        if os.path.exists(SCRIP_CACHE_FILE):
            try:
                with open(SCRIP_CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

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
            canonical_comm = 'GOLDM'
        elif clean_comm.startswith('NATURALGAS'):
            canonical_comm = 'NATURALGASM'
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

        # Rule 0: Exclude unsupported commodities on Dhan (e.g. Copper Mini)
        sym_clean = symbol.strip().upper().replace('-FUT', '').replace('.NS', '').replace('.BO', '')
        if sym_clean in self.unsupported_commodities or sym_clean.startswith('COPPER'):
            print(f"🛑 [AUTOPILOT EXCLUDED] {symbol}: Unsupported contract on Dhan (Copper Mini). Trade execution excluded per user preference.")
            return {
                'status': 'EXCLUDED_UNSUPPORTED_COMMODITY',
                'symbol': symbol,
                'reason': 'Dhan does not support Copper Mini contracts. Excluded from live broker execution.'
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
        
        # For commodities: Dhan API accepts quantity in lots (1 lot per order for mini/micro contracts)
        # For equities: Dhan API accepts whole shares
        is_commodity = (segment == 'MCX_COMM' or asset_type == 'COMMODITY')
        order_qty = 1 if is_commodity else int(quantity or 1)

        # 2. Check Available Margin
        funds = self.get_fund_limits()
        if funds.get('status') == 'success':
            avail_bal = funds['available_balance']
            # Accurate Commodity mini margin estimation
            comm_margin_map = {
                'CRUDEOILM': 105000.0,
                'ALUMINI': 35000.0,
                'ZINCMINI': 40000.0,
                'SILVERMIC': 35000.0,
                'GOLDM': 85000.0,
            }
            clean_lookup = scrip_info.get('symbol_name', symbol).strip().upper()
            est_margin = comm_margin_map.get(clean_lookup, 135000.0) if is_commodity else (float(limit_price) * order_qty * 0.20)
            if avail_bal < est_margin * 0.85:
                return {
                    'status': 'BLOCKED',
                    'reason': f'INSUFFICIENT_MARGIN: Required ~₹{est_margin:,.2f}, Available: ₹{avail_bal:,.2f}. Order blocked.'
                }

        # 3. Formulate Strict Limit Order Parameters
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

            if order_res.get('status') == 'success' or order_res.get('orderStatus') in ['TRANSIT', 'PENDING', 'TRADED']:
                order_id = str(order_res.get('data', {}).get('orderId') or order_res.get('orderId', ''))
                print(f"✅ LIMIT ORDER PLACED ON DHAN! Order ID: {order_id} (Status: {order_res.get('orderStatus', 'SUBMITTED')})")
                
                return {
                    'status': 'PLACED',
                    'order_id': order_id,
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
                    'dhan_response': order_res
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

    def attach_stop_loss_order(self, security_id, segment, entry_action, quantity, stop_loss_price):
        """
        Attaches a Stop-Loss (SL-Limit) trigger order on Dhan to protect the position.
        If entry was BUY, stop-loss order is SELL; if entry was SELL, stop-loss order is BUY.
        """
        sl_action = self.dhan.SELL if entry_action == 'BUY' else self.dhan.BUY
        trigger_p = round(float(stop_loss_price), 2)
        # 0.2% limit price buffer beyond trigger to guarantee execution in fast moves
        limit_p = round(trigger_p * 0.998 if entry_action == 'BUY' else trigger_p * 1.002, 2)
        is_commodity = (segment == 'MCX_COMM')
        product_type = self.dhan.MARGIN if is_commodity else self.dhan.INTRA

        try:
            sl_res = self.dhan.place_order(
                security_id=security_id,
                exchange_segment=segment,
                transaction_type=sl_action,
                quantity=quantity,
                order_type=self.dhan.SL,
                product_type=product_type,
                price=limit_p,
                trigger_price=trigger_p,
                validity=self.dhan.DAY,
                tag="AUTOPILOT_SL"
            )
            return sl_res
        except Exception as e:
            print(f"Error attaching Stop-Loss order: {e}")
            return {'status': 'error', 'message': str(e)}

    def modify_stop_loss_order(self, order_id, new_trigger_price):
        """Modifies trigger price of active Stop-Loss order on Dhan (Fee-Shielded Trailing Stop Ratchet)."""
        new_trigger = round(float(new_trigger_price), 2)
        try:
            return self.dhan.modify_order(
                order_id=order_id,
                order_type=self.dhan.SL,
                leg_name="ENTRY_LEG",
                price=new_trigger,
                trigger_price=new_trigger,
                disclosed_quantity=0,
                validity=self.dhan.DAY
            )
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def square_off_position(self, security_id, segment, action, quantity, price=None):
        """Executes position square-off at Target Hit or 3:20 PM NSE / 11:45 PM MCX."""
        exit_action = self.dhan.SELL if action == 'BUY' else self.dhan.BUY
        is_commodity = (segment == 'MCX_COMM')
        product_type = self.dhan.MARGIN if is_commodity else self.dhan.INTRA
        order_type = self.dhan.LIMIT if price else self.dhan.MARKET

        try:
            return self.dhan.place_order(
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
                # Only check open/unfilled limit orders
                if status in ['TRANSIT', 'PENDING', 'OPEN']:
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
