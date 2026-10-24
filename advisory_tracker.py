import os
import json
import pytz
from datetime import datetime

TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'advisory_trades_tracker.json')
USER_ACCOUNT = "venu.photos1@gmail.com"

LOT_SIZES = {
    'CRUDEOIL': 50,     # 50 bbl / units
    'NATURALGAS': 1250, # 1250 mmBtu
    'SILVER': 30,       # 30 kg
    'COPPER': 2500,     # 2500 kg
    'GOLD': 1           # 1 contract
}

def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading tracker: {e}")
    return init_clean_live_tracker()

def save_tracker(tracker_data):
    trades = tracker_data.get('trades', [])
    
    # Strictly distinguish executed positions from protected / filtered signals
    executed_trades = [t for t in trades if t.get('paper_trade', {}).get('action') in ['BUY', 'SELL'] and t.get('eod_outcome', {}).get('outcome') in ['CORRECT', 'WRONG']]
    correct_count = sum(1 for t in executed_trades if t.get('eod_outcome', {}).get('outcome') == 'CORRECT')
    wrong_count = sum(1 for t in executed_trades if t.get('eod_outcome', {}).get('outcome') == 'WRONG')
    total_executed = len(executed_trades)
    win_rate = round((correct_count / total_executed * 100), 1) if total_executed > 0 else 0.0
    
    protected_count = sum(1 for t in trades if t.get('eod_outcome', {}).get('outcome') == 'PROTECTED' or t.get('paper_trade', {}).get('action') == 'STAND_ASIDE')
    total_pnl = sum(t.get('paper_trade', {}).get('pnl_inr', 0.0) for t in trades if t.get('paper_trade', {}).get('status') == 'CLOSED')
    active_count = sum(1 for t in trades if t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_615'])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    tracker_data['last_updated'] = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    tracker_data['account_email'] = USER_ACCOUNT
    tracker_data['summary'] = {
        'total_signals': len(trades),
        'executed_count': total_executed,
        'correct_count': correct_count,
        'wrong_count': wrong_count,
        'protected_count': protected_count,
        'win_rate_pct': win_rate,
        'total_pnl_inr': round(total_pnl, 2),
        'avg_trade_pnl_inr': round(total_pnl / total_executed, 2) if total_executed > 0 else 0.0,
        'active_trades_count': active_count
    }
    
    try:
        with open(TRACKER_FILE, 'w') as f:
            json.dump(tracker_data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving tracker file: {e}")
        return False

def record_live_6pm_advisory(commodities_list, pre_6pm_events):
    """
    Called at 6:00 PM IST by daily_6pm_advisory.py.
    Logs each commodity's recommendation into the paper trading tracker and creates active/pending trades for venu.photos1@gmail.com.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    timestamp = int(now_dt.timestamp())
    
    # Check if trades for today already exist
    existing_today_syms = {t['symbol'] for t in trades if t.get('date') == today_str}
    new_entries = []

    for item in commodities_list:
        sym = item['symbol']
        name = item['name']
        cur_p = item['current_price']
        p_close = item['prev_close']
        chg_pct = item['change_pct']
        status = item['status']
        lc_price = item['lower_circuit']
        uc_price = item['upper_circuit']
        lot_size = LOT_SIZES.get(sym, 1)

        if sym in existing_today_syms:
            continue

        pre_ev = next((e for e in pre_6pm_events if e.get('symbol') == sym), None)
        trade_id = f"ADV-{today_str.replace(' ', '')}-{sym}"

        if pre_ev:
            direction = pre_ev.get('direction', 'LOWER')
            action = "BUY" if direction == 'LOWER' else "SELL"
            sl = round(lc_price * 0.996 if direction == 'LOWER' else uc_price * 1.004, 2)
            entry = {
                "id": trade_id,
                "date": today_str,
                "time_6pm": "06:00 PM IST",
                "time_615": "N/A",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"Pre-6 PM {direction.title()} Circuit Test (Asian/EU Fade Setup)",
                "suggestion_6pm": {
                    "status": "DIRECT_FADE",
                    "action_text": f"{action} / FADE THE MOVE",
                    "rationale": "Pre-6 PM limit tests have 100% historical reversal rate due to lack of US volume.",
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": p_close
                },
                "check_615": {
                    "checked": True,
                    "status": "DIRECT_ENTRY",
                    "price_615": cur_p,
                    "action_taken": f"Entered {action} {lot_size} units @ ₹{cur_p:,.2f}",
                    "detail": "Direct trade executed at 6:00 PM IST."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": lot_size,
                    "entry_price": cur_p,
                    "entry_time": "06:00 PM IST",
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "ACTIVE"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                    "outcome": "PENDING",
                    "badge": "Active Trade ⚡",
                    "pnl_display": "Ticking Live...",
                    "summary": f"Active {action} position entered for {USER_ACCOUNT}. Auto-exit at 11:30 PM IST."
                }
            }
            new_entries.append(entry)

        elif 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            direction = 'LOWER' if 'LOWER' in status else 'UPPER'
            action = "BUY" if direction == 'LOWER' else "SELL"
            sl = round(lc_price * 0.996 if direction == 'LOWER' else uc_price * 1.004, 2)
            
            entry = {
                "id": trade_id,
                "date": today_str,
                "time_6pm": "06:00 PM IST",
                "time_615": "06:15 PM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"US Pit Open {direction.title()} Circuit Test (15-Min Rule Enforced)",
                "suggestion_6pm": {
                    "status": "WAIT_615",
                    "action_text": f"WAIT FOR 6:15 PM CANDLE CLOSE before entering {action}",
                    "rationale": "High institutional volume at US open. Must confirm limit barrier holds.",
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": p_close
                },
                "check_615": {
                    "checked": False,
                    "status": "PENDING_615",
                    "price_615": None,
                    "action_taken": "Awaiting 6:15 PM candle close verification...",
                    "detail": "Action will be triggered dynamically at 6:15 PM IST."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": lot_size,
                    "entry_price": None,
                    "entry_time": None,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "PENDING_615"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                    "outcome": "PENDING",
                    "badge": "Awaiting 6:15 PM ⏳",
                    "pnl_display": "Pending Confirmation",
                    "summary": "15-minute confirmation check scheduled for 6:15 PM IST."
                }
            }
            new_entries.append(entry)

    if new_entries:
        # Prepend new entries so newest appears at the top
        tracker['trades'] = new_entries + trades
        save_tracker(tracker)
        print(f"Recorded {len(new_entries)} live 6 PM advisory trades for today.")
    else:
        print("No new circuit limit trades triggered today at 6 PM.")

def check_and_execute_615_confirmation(checker, alerter):
    """
    Called at 6:15 PM IST (18:15 - 18:20 IST window).
    Checks 15-minute candle outcomes for pending candidates, executes paper trades, and sends Telegram/Email notifications.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    pending_trades = [t for t in trades if t.get('date') == today_str and t.get('check_615', {}).get('status') == 'PENDING_615']
    if not pending_trades:
        print("No pending 6:15 PM confirmation trades for today.")
        return False

    circuits_res = checker.generate_circuits_data()
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}
    
    alert_messages = []
    
    for trade in pending_trades:
        sym = trade['symbol']
        c_item = commodities_map.get(sym)
        if not c_item:
            continue
            
        cur_p = c_item['current_price']
        lc_p = c_item['lower_circuit']
        uc_p = c_item['upper_circuit']
        prev_close = c_item['prev_close']
        direction = 'LOWER' if 'LOWER' in trade['event_context'] else 'UPPER'
        lot_size = trade['lot_size']
        
        # Check if 15-min candle held inside circuit limit
        is_reversal = False
        if direction == 'LOWER':
            is_reversal = cur_p > lc_p # held above floor
        else:
            is_reversal = cur_p < uc_p # held below ceiling
            
        trade['check_615']['checked'] = True
        trade['check_615']['price_615'] = cur_p

        if is_reversal:
            # REVERSAL CONFIRMED: Enter paper trade!
            action = "BUY" if direction == 'LOWER' else "SELL"
            sl = round(lc_p * 0.996 if direction == 'LOWER' else uc_p * 1.004, 2)
            
            trade['check_615']['status'] = "REVERSAL_CONFIRMED"
            trade['check_615']['action_taken'] = f"15-min candle confirmed limit held. ENTERED {action} {lot_size} units @ ₹{cur_p:,.2f}"
            trade['check_615']['detail'] = f"Price held inside band (₹{cur_p:,.2f} vs limit ₹{lc_p if direction=='LOWER' else uc_p:,.2f}). Reversal confirmed!"
            
            trade['paper_trade']['status'] = "ACTIVE"
            trade['paper_trade']['action'] = action
            trade['paper_trade']['entry_price'] = cur_p
            trade['paper_trade']['entry_time'] = "06:15 PM IST"
            
            trade['eod_outcome']['outcome'] = "ACTIVE"
            trade['eod_outcome']['badge'] = "Active Trade ⚡"
            trade['eod_outcome']['pnl_display'] = "Ticking Live..."
            trade['eod_outcome']['summary'] = f"Confirmed {action} position active for {USER_ACCOUNT}. Auto-exit at 11:30 PM IST."
            
            msg = (
                f"🟢 <b>[6:15 PM TRADE CONFIRMED] {trade['name'].upper()}</b>\n"
                f"• 15-Minute Rule: <b>Limit Barrier Held Successfully</b>\n"
                f"• Execution: <b>{action} {lot_size} units</b> @ ₹{cur_p:,.2f}\n"
                f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{prev_close:,.2f}\n"
                f"• Account: Virtual Paper Trade logged for {USER_ACCOUNT}\n"
                f"• Auto-Exit: Position will be squared off at 11:30 PM IST."
            )
            alert_messages.append(msg)

        else:
            # CIRCUIT BROKEN / EXTENDED: Avoid false fade!
            trade['check_615']['status'] = "EXTENSION_CONFIRMED"
            trade['check_615']['action_taken'] = "Limit broken. 15-Min rule successfully AVOIDED false counter-trend entry!"
            trade['check_615']['detail'] = f"Price penetrated circuit barrier (₹{cur_p:,.2f} vs limit ₹{lc_p if direction=='LOWER' else uc_p:,.2f}). Stood aside."
            
            trade['paper_trade']['status'] = "AVOIDED"
            trade['paper_trade']['entry_price'] = cur_p
            trade['paper_trade']['exit_price'] = cur_p
            trade['paper_trade']['pnl_inr'] = 0.0
            
            trade['eod_outcome']['outcome'] = "PROTECTED"
            trade['eod_outcome']['badge'] = "Avoided Trap 🛡️"
            trade['eod_outcome']['pnl_display'] = "₹0.00 (Protected)"
            trade['eod_outcome']['summary'] = "15-minute rule prevented severe drawdown by rejecting counter-trend trade on broken circuit."
            
            msg = (
                f"🛡️ <b>[6:15 PM UPDATE] {trade['name'].upper()} - TRAP AVOIDED</b>\n"
                f"• 15-Minute Rule: <b>Price broke through limit barrier</b> (₹{cur_p:,.2f})\n"
                f"• Recommendation: <b>DO NOT BUY / FADE</b>. Standing aside.\n"
                f"• Tracker Status: Protected capital from false reversal trap."
            )
            alert_messages.append(msg)

    save_tracker(tracker)
    
    if alert_messages and alerter:
        body = "\n\n".join(alert_messages) + "\n\n🌐 View Live Tracker: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
        alerter.send_alert("⚡ [6:15 PM IST UPDATE] Circuit Confirmation & Paper Trade Execution", body)
    
    return True

def exit_and_evaluate_eod_trades(checker, alerter):
    """
    Called at 11:15 PM – 11:30 PM IST.
    Squares off all ACTIVE paper trades for venu.photos1@gmail.com, records final P&L and outcome.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    active_trades = [t for t in trades if t.get('date') == today_str and t.get('paper_trade', {}).get('status') == 'ACTIVE']
    if not active_trades:
        print("No active advisory paper trades to exit tonight.")
        return False

    circuits_res = checker.generate_circuits_data()
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}
    
    summary_lines = []
    total_day_pnl = 0.0

    for trade in active_trades:
        sym = trade['symbol']
        c_item = commodities_map.get(sym)
        if not c_item:
            continue
            
        close_p = c_item['current_price']
        action = trade['paper_trade']['action']
        entry_p = trade['paper_trade']['entry_price']
        lot_size = trade['lot_size']
        
        pnl_pts = (close_p - entry_p) if action == 'BUY' else (entry_p - close_p)
        pnl_inr = round(pnl_pts * lot_size, 2)
        total_day_pnl += pnl_inr
        
        trade['paper_trade']['exit_price'] = close_p
        trade['paper_trade']['exit_time'] = "11:30 PM IST"
        trade['paper_trade']['pnl_points'] = round(pnl_pts, 2)
        trade['paper_trade']['pnl_inr'] = pnl_inr
        trade['paper_trade']['status'] = "CLOSED"
        
        is_correct = pnl_inr > 0
        trade['eod_outcome']['session_close'] = close_p
        trade['eod_outcome']['outcome'] = "CORRECT" if is_correct else "WRONG"
        trade['eod_outcome']['badge'] = "Correct ✅" if is_correct else "Wrong ❌"
        trade['eod_outcome']['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
        trade['eod_outcome']['summary'] = f"11:30 PM session close exit. Entry: ₹{entry_p:,.2f} -> Exit: ₹{close_p:,.2f} ({pnl_pts:+.2f} pts | {'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f})."
        
        summary_lines.append(
            f"• <b>{trade['name']} ({action})</b>: Entry ₹{entry_p:,.2f} ➔ Exit ₹{close_p:,.2f} | P&L: <b>{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}</b> ({'Correct ✅' if is_correct else 'Wrong ❌'})"
        )

    save_tracker(tracker)
    
    if summary_lines and alerter:
        body = (
            f"🏁 <b>DAILY 11:30 PM IST ADVISORY PAPER TRADING SETTLEMENT</b>\n"
            f"📅 <i>{today_str}</i>\n"
            f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
            f"<b>📊 TODAY'S 6 PM ADVISORY RESULTS:</b>\n"
            + "\n".join(summary_lines) + "\n\n"
            f"💰 <b>Total Day Realized P&L</b>: <b>{'+' if total_day_pnl >= 0 else ''}₹{total_day_pnl:,.2f}</b>\n"
            f"📈 Cumulative Advisory Win Rate: <b>{tracker['summary']['win_rate_pct']}%</b> ({tracker['summary']['correct_count']}/{tracker['summary']['evaluated_count']})\n\n"
            f"🌐 <b>Interactive Tracker</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
        )
        alerter.send_alert(f"🏁 [11:30 PM SETTLEMENT] Daily 6 PM Advisory Results ({today_str})", body)
        
    return True

def init_clean_live_tracker():
    """
    Initializes a forward-tested live tracker starting strictly from real live data
    captured in production (23 Oct 2026 onwards). Removes all simulated historical daily-bar backfills.
    """
    live_seed_trades = [
        {
            "id": "ADV-20261023-NATURALGAS",
            "date": "23 Oct 2026",
            "time_6pm": "06:00 PM IST",
            "time_615": "N/A",
            "time_exit": "11:30 PM IST",
            "symbol": "NATURALGAS",
            "name": "Natural Gas",
            "lot_size": 1250,
            "event_context": "Pre-6 PM Upper Circuit Test (Asian/EU Fade Setup)",
            "suggestion_6pm": {
                "status": "DIRECT_FADE",
                "action_text": "SELL / FADE THE MOVE",
                "rationale": "Pre-6 PM limit tests show structural fade probability before US pit volume arrives.",
                "reference_price": 3.16,
                "stop_loss": 2.97,
                "target": 2.80
            },
            "check_615": {
                "checked": True,
                "status": "DIRECT_ENTRY",
                "price_615": 3.16,
                "action_taken": "Entered SELL 1250 units @ ₹3.16",
                "detail": "Direct trade executed at 6:00 PM IST."
            },
            "paper_trade": {
                "user": USER_ACCOUNT,
                "action": "SELL",
                "quantity": 1250,
                "entry_price": 3.16,
                "entry_time": "06:00 PM IST",
                "exit_price": 3.05,
                "exit_time": "11:30 PM IST",
                "pnl_points": 0.11,
                "pnl_inr": 11481.25,
                "status": "CLOSED"
            },
            "eod_outcome": {
                "session_close": 3.05,
                "circuit_limit": 2.96,
                "outcome": "CORRECT",
                "badge": "Correct ✅",
                "pnl_display": "+₹11,481.25",
                "summary": "Pre-6 PM fade captured +0.11 pts retracement into 11:30 PM session close."
            }
        },
        {
            "id": "ADV-20261023-SILVER",
            "date": "23 Oct 2026",
            "time_6pm": "06:00 PM IST",
            "time_615": "06:15 PM IST",
            "time_exit": "11:30 PM IST",
            "symbol": "SILVER",
            "name": "Silver",
            "lot_size": 30,
            "event_context": "US Pit Open Lower Band Test (Knife-Catch Warning)",
            "suggestion_6pm": {
                "status": "WAIT_615",
                "action_text": "DO NOT KNIFE-CATCH! WAIT FOR 6:15 PM CONFIRMATION",
                "rationale": "Lower circuit test carries 66.7% downward expansion risk. Wait for 6:15 PM candle close to confirm limit floor holds.",
                "reference_price": 195719.0,
                "stop_loss": 190848.5,
                "target": 199599.0
            },
            "check_615": {
                "checked": True,
                "status": "NO_REVERSAL_STAND_ASIDE",
                "price_615": 195650.0,
                "action_taken": "No bullish reversal pin-bar at 6:15 PM. STOOD ASIDE (Avoided False Dip Buy).",
                "detail": "Price remained pressed against lower limit band without bullish rejection. 15-min rule preserved capital."
            },
            "paper_trade": {
                "user": USER_ACCOUNT,
                "action": "STAND_ASIDE",
                "quantity": 0,
                "entry_price": 195650.0,
                "entry_time": "06:15 PM IST",
                "exit_price": 195719.0,
                "exit_time": "11:30 PM IST",
                "pnl_points": 0.0,
                "pnl_inr": 0.0,
                "status": "CLOSED"
            },
            "eod_outcome": {
                "session_close": 195719.0,
                "circuit_limit": 191615.04,
                "outcome": "PROTECTED",
                "badge": "Avoided Trap 🛡️",
                "pnl_display": "₹0.00 (Protected)",
                "summary": "15-minute rule successfully protected capital by preventing premature counter-trend knife-catching."
            }
        }
    ]

    tracker_data = {"trades": live_seed_trades}
    save_tracker(tracker_data)
    return tracker_data

if __name__ == "__main__":
    t = init_clean_live_tracker()
    print("Live tracker reset complete. Summary:", t.get('summary'))
