"""
Stock Investment Tracker & Automated Golden Crossover SIP Engine
================================================================
Scans Indian Nifty 50 stocks at the start of each trading day, identifies the
highest-conviction Golden Crossover (50 EMA >= 200 EMA) value setup, and executes
an automated investment with approx ₹30,000 INR capital (tolerance ±₹1,000 based on stock price).

Outputs:
  web/stock_investments_tracker.json
"""

import os
import json
import math
import pytz
from datetime import datetime

TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'stock_investments_tracker.json')
NIFTY_JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'nifty_crossover_oversold.json')
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

TARGET_INVESTMENT_AMOUNT = 30000.0  # ₹30,000 INR per daily stock pick
TOLERANCE_BAND = 1000.0             # ±₹1,000 INR tolerance

def calculate_shares_for_capital(price, target_capital=TARGET_INVESTMENT_AMOUNT):
    """
    Computes the exact integer number of shares such that:
    total_cost = shares * price is closest to target_capital (₹30,000 ± ₹1,000).
    """
    if price <= 0:
        return 1, 0.0, 0.0
    
    raw = target_capital / price
    floor_shares = max(1, math.floor(raw))
    ceil_shares = max(1, math.ceil(raw))

    diff_floor = abs((floor_shares * price) - target_capital)
    diff_ceil = abs((ceil_shares * price) - target_capital)

    chosen_shares = floor_shares if diff_floor <= diff_ceil else ceil_shares
    invested_value = round(chosen_shares * price, 2)
    variance = round(invested_value - target_capital, 2)

    return chosen_shares, invested_value, variance

def load_stock_investments():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not read {TRACKER_FILE}: {e}")
    
    return {
        'last_updated': datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST'),
        'summary': {
            'total_investments_count': 0,
            'active_holdings_count': 0,
            'closed_investments_count': 0,
            'total_capital_invested_inr': 0.0,
            'current_portfolio_value_inr': 0.0,
            'unrealized_pnl_inr': 0.0,
            'unrealized_pnl_pct': 0.0,
            'realized_pnl_inr': 0.0,
            'total_pnl_inr': 0.0,
            'win_rate_pct': 0.0,
            'target_hit_count': 0,
            'stop_loss_hit_count': 0
        },
        'today_pick': None,
        'investments': []
    }

def save_stock_investments(data):
    investments = data.get('investments', [])
    active = [i for i in investments if i.get('status') == 'ACTIVE']
    closed = [i for i in investments if i.get('status') in ['CLOSED', 'TARGET_HIT', 'STOP_LOSS_HIT']]

    total_invested = round(sum(i.get('invested_value', 0.0) for i in active), 2)
    current_val = round(sum(i.get('current_value', i.get('invested_value', 0.0)) for i in active), 2)
    unrealized_pnl = round(current_val - total_invested, 2)
    unrealized_pct = round((unrealized_pnl / total_invested) * 100, 2) if total_invested > 0 else 0.0

    realized_pnl = round(sum(i.get('pnl_inr', 0.0) for i in closed), 2)
    total_pnl = round(unrealized_pnl + realized_pnl, 2)

    tgt_hits = sum(1 for i in investments if i.get('status') == 'TARGET_HIT')
    sl_hits = sum(1 for i in investments if i.get('status') == 'STOP_LOSS_HIT')
    closed_count = len(closed)
    win_rate = round((tgt_hits / closed_count) * 100, 1) if closed_count > 0 else 0.0

    data['summary'] = {
        'total_investments_count': len(investments),
        'active_holdings_count': len(active),
        'closed_investments_count': closed_count,
        'total_capital_invested_inr': total_invested,
        'current_portfolio_value_inr': current_val,
        'unrealized_pnl_inr': unrealized_pnl,
        'unrealized_pnl_pct': unrealized_pct,
        'realized_pnl_inr': realized_pnl,
        'total_pnl_inr': total_pnl,
        'win_rate_pct': win_rate,
        'target_hit_count': tgt_hits,
        'stop_loss_hit_count': sl_hits
    }
    data['last_updated'] = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')

    os.makedirs(os.path.dirname(TRACKER_FILE), exist_ok=True)
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def scan_and_execute_daily_stock_investment(force=False, test_date=None):
    """
    Scans Golden Crossover opportunities and executes 1 daily stock purchase
    with ~₹30,000 INR (tolerance ±₹1,000).
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = test_date or now_dt.strftime('%d %b %Y')
    today_time_ist = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    date_compact = now_dt.strftime('%Y%m%d')

    tracker_data = load_stock_investments()
    investments = tracker_data.setdefault('investments', [])

    # Check if an investment has already been taken today
    today_existing = [i for i in investments if i.get('date') == today_str]
    if today_existing and not force:
        print(f"Daily stock investment already executed for today ({today_str}): {today_existing[0].get('name')}. Skipping.")
        return today_existing[0]

    # Load Golden Crossover Scanner data
    if not os.path.exists(NIFTY_JSON_FILE):
        print(f"Error: {NIFTY_JSON_FILE} not found. Running scanner first...")
        try:
            from nifty_scanner import scan_nifty50_opportunities
            scan_nifty50_opportunities()
        except Exception as e:
            print(f"Failed to scan Nifty 50: {e}")
            return None

    try:
        with open(NIFTY_JSON_FILE, 'r') as f:
            nifty_data = json.load(f)
    except Exception as e:
        print(f"Error loading {NIFTY_JSON_FILE}: {e}")
        return None

    stocks = nifty_data.get('stocks', [])
    # Filter strictly for Golden Crossover stocks (50 EMA >= 200 EMA)
    golden_stocks = [s for s in stocks if s.get('is_golden') and s.get('close', 0) > 0]

    if not golden_stocks:
        print("No Golden Crossover stocks found in scanner.")
        return None

    # Sort by multi-factor quantitative ranking:
    # 1. Win Probability descending
    # 2. 52-Week Discount percentage descending
    # 3. RSI ascending (prefer deep oversold accumulation)
    golden_stocks.sort(
        key=lambda s: (s.get('win_probability', 0), s.get('discount_pct', 0), -s.get('rsi', 50)),
        reverse=True
    )

    # Check active holdings to ensure portfolio diversification across days
    active_symbols = {i.get('symbol') for i in investments if i.get('status') == 'ACTIVE'}

    chosen_stock = None
    for cand in golden_stocks:
        if cand.get('symbol') not in active_symbols:
            chosen_stock = cand
            break

    # If all golden cross stocks are already held, pick the top one to accumulate
    if not chosen_stock:
        chosen_stock = golden_stocks[0]

    price = float(chosen_stock.get('close', 0.0))
    if price <= 0:
        print(f"Invalid price {price} for {chosen_stock.get('name')}")
        return None

    shares, invested_val, variance = calculate_shares_for_capital(price, TARGET_INVESTMENT_AMOUNT)

    trade_plan = chosen_stock.get('trade_plan', {})
    stop_loss = float(trade_plan.get('stop_loss', round(price * 0.955, 2)))
    target_1 = float(trade_plan.get('target_1', round(price * 1.10, 2)))
    target_2 = float(trade_plan.get('target_2', round(price * 1.18, 2)))

    inv_id = f"STOCK-INV-{date_compact}-{chosen_stock['name'].replace('.NS','')}"

    new_investment = {
        'id': inv_id,
        'date': today_str,
        'time': today_time_ist,
        'symbol': chosen_stock.get('symbol'),
        'name': chosen_stock.get('name'),
        'full_name': chosen_stock.get('full_name', chosen_stock.get('name')),
        'sector': chosen_stock.get('sector', 'Indian Equities'),
        'entry_price': price,
        'shares': shares,
        'invested_value': invested_val,
        'variance_from_30k': variance,
        'current_price': price,
        'current_value': invested_val,
        'pnl_inr': 0.0,
        'pnl_pct': 0.0,
        'stop_loss': stop_loss,
        'target_1': target_1,
        'target_2': target_2,
        'potential_upside_t1': trade_plan.get('potential_upside_t1', round(((target_1 - price) / price) * 100, 1)),
        'potential_upside_t2': trade_plan.get('potential_upside_t2', round(((target_2 - price) / price) * 100, 1)),
        'risk_pct': trade_plan.get('risk_pct', round(((price - stop_loss) / price) * 100, 1)),
        'win_probability': chosen_stock.get('win_probability', 90),
        'rsi': chosen_stock.get('rsi'),
        'discount_pct': chosen_stock.get('discount_pct'),
        'ema_50': chosen_stock.get('ema_50'),
        'ema_200': chosen_stock.get('ema_200'),
        'crossover_status': 'Golden Cross Active 🌟',
        'action': 'STRONG BUY (Daily SIP)',
        'rationale': trade_plan.get('rationale', '50 EMA > 200 EMA Golden Cross with deep value confluence'),
        'status': 'ACTIVE',
        'exit_price': None,
        'exit_time': None,
        'exit_trigger': None
    }

    # If force running for today, replace today's existing or prepend
    if today_existing:
        for idx, item in enumerate(investments):
            if item.get('date') == today_str:
                investments[idx] = new_investment
                break
    else:
        investments.insert(0, new_investment)

    tracker_data['today_pick'] = new_investment
    save_stock_investments(tracker_data)

    print(f"\n✅ EXECUTED DAILY STOCK INVESTMENT:")
    print(f"   Asset: {new_investment['name']} ({new_investment['symbol']})")
    print(f"   Shares: {shares} @ ₹{price:,.2f} = ₹{invested_val:,.2f} (Variance from ₹30,000: ₹{variance:+,.2f})")
    print(f"   Win Prob: {new_investment['win_probability']}% | 52W Disc: {new_investment['discount_pct']}% | RSI: {new_investment['rsi']}")
    print(f"   Stop-Loss: ₹{stop_loss:,.2f} | Target 1: ₹{target_1:,.2f} | Target 2: ₹{target_2:,.2f}")

    # Dispatch Email / Telegram Alert
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as cf:
                cfg = json.load(cf)
            from alerter import EmailAlerter
            alerter = EmailAlerter(cfg)
            
            subject = f"🌟 [DAILY STOCK SIP] Bought {shares} shares of {new_investment['name']} (~₹30,000 Golden Cross Investment)"
            body = (
                f"🌟 DAILY GOLDEN CROSSOVER STOCK INVESTMENT EXECUTION\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• Stock: {new_investment['full_name']} ({new_investment['symbol']})\n"
                f"• Strategy: Automated 50/200 EMA Golden Crossover Value SIP\n"
                f"• Shares Purchased: {shares} Shares\n"
                f"• Entry Price: ₹{price:,.2f} CMP\n"
                f"• Total Capital Invested: ₹{invested_val:,.2f} (Target: ₹30,000 | Variance: ₹{variance:+,.2f})\n\n"
                f"📊 QUANTITATIVE CONFLUENCE METRICS:\n"
                f"• Algorithmic Win Probability: {new_investment['win_probability']}%\n"
                f"• 52-Week High Discount: {new_investment['discount_pct']}% Off Peak\n"
                f"• RSI Indicator: {new_investment['rsi']} (Oversold Value Zone)\n"
                f"• Moving Averages: 50 EMA (₹{new_investment['ema_50']}) > 200 EMA (₹{new_investment['ema_200']})\n\n"
                f"🎯 TARGET & PROTECTIVE STOP-LOSS:\n"
                f"• Stop-Loss (Protective Floor): ₹{stop_loss:,.2f} (-{new_investment['risk_pct']}% risk)\n"
                f"• Target 1 (Mean Reversion): ₹{target_1:,.2f} (+{new_investment['potential_upside_t1']}% upside)\n"
                f"• Target 2 (Swing High Retest): ₹{target_2:,.2f} (+{new_investment['potential_upside_t2']}% upside)\n\n"
                f"💡 Rationale: {new_investment['rationale']}\n"
                f"View live tracker on: https://VenuInturiphoos.github.io/commodity-alerts/"
            )
            alerter.send_alert(subject, body)
            print("Successfully dispatched Daily Stock Investment alert via Telegram/Email.")
    except Exception as e:
        print(f"Note: Could not send alert (non-fatal): {e}")

    return new_investment

def update_live_stock_investments():
    """
    Updates live market prices, calculates current MTM returns,
    and checks for Target or Stop-Loss triggers for all active stock investments.
    """
    if not os.path.exists(TRACKER_FILE):
        return

    tracker_data = load_stock_investments()
    investments = tracker_data.get('investments', [])
    active = [i for i in investments if i.get('status') == 'ACTIVE']

    if not active:
        return

    # Load latest prices from nifty_crossover_oversold.json
    price_map = {}
    if os.path.exists(NIFTY_JSON_FILE):
        try:
            with open(NIFTY_JSON_FILE, 'r') as f:
                nifty_data = json.load(f)
            for s in nifty_data.get('stocks', []):
                sym = s.get('symbol')
                name = s.get('name')
                close = s.get('close')
                if sym and close:
                    price_map[sym] = close
                if name and close:
                    price_map[name] = close
        except Exception as e:
            print(f"Warning: Could not read latest stock prices: {e}")

    updated = False
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    now_time_str = now_dt.strftime('%d %b %Y, %I:%M %p IST')

    for inv in active:
        sym = inv.get('symbol')
        name = inv.get('name')
        latest_price = price_map.get(sym) or price_map.get(name)

        if not latest_price or latest_price <= 0:
            continue

        latest_price = float(latest_price)
        entry_p = float(inv.get('entry_price', latest_price))
        shares = int(inv.get('shares', 1))
        t1 = float(inv.get('target_1', entry_p * 1.10))
        sl = float(inv.get('stop_loss', entry_p * 0.95))

        inv['current_price'] = latest_price
        cur_val = round(shares * latest_price, 2)
        inv['current_value'] = cur_val
        pnl = round(cur_val - inv['invested_value'], 2)
        pnl_pct = round((pnl / inv['invested_value']) * 100, 2) if inv['invested_value'] > 0 else 0.0
        inv['pnl_inr'] = pnl
        inv['pnl_pct'] = pnl_pct

        # Check for Target 1 or Stop Loss triggers
        if latest_price >= t1:
            inv['status'] = 'TARGET_HIT'
            inv['exit_price'] = latest_price
            inv['exit_time'] = now_time_str
            inv['exit_trigger'] = 'TARGET_1_HIT'
            print(f"🎯 TARGET 1 HIT for {inv['name']} at ₹{latest_price:,.2f}! Realized P&L: ₹{pnl:+,.2f} ({pnl_pct:+,.2f}%)")
            updated = True
        elif latest_price <= sl:
            inv['status'] = 'STOP_LOSS_HIT'
            inv['exit_price'] = latest_price
            inv['exit_time'] = now_time_str
            inv['exit_trigger'] = 'STOP_LOSS_HIT'
            print(f"🛡️ STOP LOSS HIT for {inv['name']} at ₹{latest_price:,.2f}. Realized P&L: ₹{pnl:+,.2f} ({pnl_pct:+,.2f}%)")
            updated = True
        else:
            updated = True

    # Keep today's pick synchronized with latest updates
    if tracker_data.get('today_pick'):
        today_id = tracker_data['today_pick'].get('id')
        match = next((i for i in investments if i.get('id') == today_id), None)
        if match:
            tracker_data['today_pick'] = match

    if updated:
        save_stock_investments(tracker_data)

if __name__ == '__main__':
    print("Testing Daily Stock Investment Engine...")
    scan_and_execute_daily_stock_investment(force=True)
    update_live_stock_investments()
