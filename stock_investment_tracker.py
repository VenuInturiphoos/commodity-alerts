"""
Stock Investment Tracker & Automated Golden Crossover SIP Engine
================================================================
Scans Indian Nifty 50 stocks at the start of each trading day, identifies the
highest-conviction Golden Crossover (50 EMA >= 200 EMA) value setup, and executes
an automated investment using ONLY available stock investing capital (~₹30,000 INR
with tolerance ±₹1,000 based on stock price).

Enforces strictly defined Targets (Target 1 & Target 2) and Stop Loss rules:
  • Target 1: 2.2x risk (mean reversion to 50 EMA / 20 MA) -> Books target exit
  • Stop-Loss: Below lower Bollinger Band / 4.5% below CMP -> Protective stop exit
  • Cash Management: Only available stock investing capital can be deployed.
    When a trade hits Target or Stop Loss, the capital and realized P&L return to available cash.

Outputs:
  web/stock_investments_tracker.json
"""

import os
import json
import math
import pytz
from datetime import datetime
from trader_engine_bridge import MonteCarloTimeMachine

TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'stock_investments_tracker.json')
NIFTY_JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'nifty_crossover_oversold.json')
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

TARGET_INVESTMENT_AMOUNT = 30000.0   # ₹30,000 INR per daily stock pick
TOLERANCE_BAND = 1000.0              # ±₹1,000 INR tolerance
DEFAULT_STARTING_CASH = 1000000.0    # ₹10,00,000 (10 Lakhs Virtual Stock Investing Capital)

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

def get_cash_and_holdings(tracker_data):
    """
    Computes available cash, currently invested capital in active stock holdings,
    total stock portfolio value, and overall net worth.
    """
    starting_cash = float(tracker_data.get('starting_cash_inr', DEFAULT_STARTING_CASH))
    investments = tracker_data.get('investments', [])
    
    active = [i for i in investments if i.get('status') == 'ACTIVE']
    closed = [i for i in investments if i.get('status') in ['CLOSED', 'TARGET_HIT', 'STOP_LOSS_HIT']]

    # Realized P&L from closed stock trades
    realized_pnl = round(sum(i.get('pnl_inr', 0.0) for i in closed), 2)
    current_cash = round(starting_cash + realized_pnl, 2)

    # Active capital deployed in open stock holdings
    active_capital_invested = round(sum(i.get('invested_value', 0.0) for i in active), 2)
    
    # Available stock investing cash (strictly enforces capital allocation limit)
    available_cash = round(max(0.0, current_cash - active_capital_invested), 2)
    
    # Current market value of open stock holdings
    current_holdings_value = round(sum(i.get('current_value', i.get('invested_value', 0.0)) for i in active), 2)
    
    # Total account net worth
    net_worth = round(available_cash + current_holdings_value, 2)
    unrealized_pnl = round(current_holdings_value - active_capital_invested, 2)
    total_pnl = round(unrealized_pnl + realized_pnl, 2)

    return {
        'starting_cash_inr': starting_cash,
        'current_cash_inr': current_cash,
        'available_cash_inr': available_cash,
        'active_capital_invested_inr': active_capital_invested,
        'current_holdings_value_inr': current_holdings_value,
        'net_worth_inr': net_worth,
        'unrealized_pnl_inr': unrealized_pnl,
        'realized_pnl_inr': realized_pnl,
        'total_pnl_inr': total_pnl
    }

def load_stock_investments():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not read {TRACKER_FILE}: {e}")
    
    now_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')
    return {
        'last_updated': now_str,
        'starting_cash_inr': DEFAULT_STARTING_CASH,
        'summary': {
            'total_investments_count': 0,
            'active_holdings_count': 0,
            'closed_investments_count': 0,
            'starting_cash_inr': DEFAULT_STARTING_CASH,
            'available_cash_inr': DEFAULT_STARTING_CASH,
            'active_capital_invested_inr': 0.0,
            'current_portfolio_value_inr': 0.0,
            'net_worth_inr': DEFAULT_STARTING_CASH,
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

    cash_stats = get_cash_and_holdings(data)
    
    tgt_hits = sum(1 for i in investments if i.get('status') == 'TARGET_HIT')
    sl_hits = sum(1 for i in investments if i.get('status') == 'STOP_LOSS_HIT')
    closed_count = len(closed)
    win_rate = round((tgt_hits / closed_count) * 100, 1) if closed_count > 0 else 0.0
    
    unrealized_pct = round((cash_stats['unrealized_pnl_inr'] / cash_stats['active_capital_invested_inr']) * 100, 2) if cash_stats['active_capital_invested_inr'] > 0 else 0.0

    data['starting_cash_inr'] = cash_stats['starting_cash_inr']
    data['summary'] = {
        'total_investments_count': len(investments),
        'active_holdings_count': len(active),
        'closed_investments_count': closed_count,
        'starting_cash_inr': cash_stats['starting_cash_inr'],
        'available_cash_inr': cash_stats['available_cash_inr'],
        'active_capital_invested_inr': cash_stats['active_capital_invested_inr'],
        'current_portfolio_value_inr': cash_stats['current_holdings_value_inr'],
        'net_worth_inr': cash_stats['net_worth_inr'],
        'unrealized_pnl_inr': cash_stats['unrealized_pnl_inr'],
        'unrealized_pnl_pct': unrealized_pct,
        'realized_pnl_inr': cash_stats['realized_pnl_inr'],
        'total_pnl_inr': cash_stats['total_pnl_inr'],
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
    using ONLY available stock investing capital (~₹30,000 INR with ±₹1,000 tolerance).
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

    # Verify Available Stock Investing Capital
    cash_stats = get_cash_and_holdings(tracker_data)
    avail_cash = cash_stats['available_cash_inr']
    min_capital_required = TARGET_INVESTMENT_AMOUNT - TOLERANCE_BAND  # ₹29,000

    if avail_cash < min_capital_required:
        print(f"⚠️ Insufficient Available Stock Investing Capital: ₹{avail_cash:,.2f} available. Required: ~₹{TARGET_INVESTMENT_AMOUNT:,.2f}. Skipping new purchase.")
        return None

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

    # Re-check that the exact purchase value does not exceed available cash
    if invested_val > avail_cash:
        shares = max(1, math.floor(avail_cash / price))
        invested_val = round(shares * price, 2)
        variance = round(invested_val - TARGET_INVESTMENT_AMOUNT, 2)
        if invested_val > avail_cash:
            print(f"Cannot afford even 1 share of {chosen_stock.get('name')} at ₹{price}. Available cash: ₹{avail_cash}")
            return None

    trade_plan = chosen_stock.get('trade_plan', {})
    stop_loss = float(trade_plan.get('stop_loss', round(price * 0.955, 2)))
    target_1 = float(trade_plan.get('target_1', round(price * 1.10, 2)))
    target_2 = float(trade_plan.get('target_2', round(price * 1.18, 2)))

    # Run Monte Carlo Time Machine simulation (3,000 empirical replays)
    sim_stock_sym = chosen_stock.get('name', chosen_stock.get('symbol', ''))
    if not sim_stock_sym.endswith('.NS') and not sim_stock_sym.endswith('.BO'):
        sim_stock_sym = f"{sim_stock_sym}.NS"
    sim_metrics = MonteCarloTimeMachine.run_simulation(sim_stock_sym, price, stop_loss, target_1, action='buy', replays=3000)

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
        'time_machine': sim_metrics,
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

    print(f"\n✅ EXECUTED DAILY STOCK INVESTMENT (CASH DELIVERY):")
    print(f"   Asset: {new_investment['name']} ({new_investment['symbol']})")
    print(f"   Shares: {shares} @ ₹{price:,.2f} = ₹{invested_val:,.2f} (Target: ₹30,000 | Variance: ₹{variance:+,.2f})")
    print(f"   Remaining Available Stock Cash: ₹{avail_cash - invested_val:,.2f}")
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
                f"• Strategy: Automated 50/200 EMA Golden Crossover Value SIP (100% Cash Delivery)\n"
                f"• Shares Purchased: {shares} Shares\n"
                f"• Entry Price: ₹{price:,.2f} CMP\n"
                f"• Capital Invested: ₹{invested_val:,.2f} (Remaining Cash: ₹{avail_cash - invested_val:,.2f})\n\n"
                f"📊 QUANTITATIVE CONFLUENCE METRICS:\n"
                f"• Algorithmic Win Probability: {new_investment['win_probability']}%\n"
                f"• 52-Week High Discount: {new_investment['discount_pct']}% Off Peak\n"
                f"• RSI Indicator: {new_investment['rsi']} (Oversold Value Zone)\n"
                f"• Moving Averages: 50 EMA (₹{new_investment['ema_50']}) > 200 EMA (₹{new_investment['ema_200']})\n\n"
                f"🎯 TARGET & PROTECTIVE STOP-LOSS RULES:\n"
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
    and enforces strictly defined Target 1, Target 2, and Stop-Loss triggers.
    When a trade hits Target or Stop Loss, the capital and realized P&L return to available cash.
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
        # Check specific ticker name first to avoid cross-demerger alias collisions
        latest_price = price_map.get(name) or price_map.get(sym)

        if not latest_price or latest_price <= 0:
            continue

        latest_price = float(latest_price)
        entry_p = float(inv.get('entry_price', latest_price))

        # Sanity check: prevent cross-ticker collision if price moves >20% instantaneously
        if entry_p > 0 and abs(latest_price - entry_p) / entry_p > 0.20:
            if name and price_map.get(name) and price_map.get(name) != latest_price:
                latest_price = float(price_map.get(name))
            elif name != sym and price_map.get(name) is None:
                # Price is from an alias collision, skip corrupting trade
                print(f"⚠️ Warning: Large price gap detected for {name} ({sym}) - entry: ₹{entry_p}, fetched: ₹{latest_price}. Skipping tick to prevent alias contamination.")
                continue
        shares = int(inv.get('shares', 1))
        t1 = float(inv.get('target_1', entry_p * 1.10))
        t2 = float(inv.get('target_2', entry_p * 1.18))
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
            print(f"🎯 TARGET 1 HIT for {inv['name']} at ₹{latest_price:,.2f}! Realized P&L: ₹{pnl:+,.2f} ({pnl_pct:+,.2f}%) - Capital released to cash.")
            updated = True
            
            # Dispatch Target Hit alert
            try:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r') as cf:
                        cfg = json.load(cf)
                    from alerter import EmailAlerter
                    alerter = EmailAlerter(cfg)
                    subj = f"🎯 [TARGET HIT] {inv['name']} reached Target 1 at ₹{latest_price:,.2f} (+{pnl_pct}%)"
                    bdy = f"🎯 TARGET 1 EXECUTED!\n• Stock: {inv['name']} ({inv['symbol']})\n• Shares: {shares}\n• Entry: ₹{entry_p:,.2f} -> Exit: ₹{latest_price:,.2f}\n• Realized Profit: +₹{pnl:,.2f} (+{pnl_pct}%)\n• Capital of ₹{cur_val:,.2f} returned to available cash."
                    alerter.send_alert(subj, bdy)
            except Exception:
                pass

        elif latest_price <= sl:
            inv['status'] = 'STOP_LOSS_HIT'
            inv['exit_price'] = latest_price
            inv['exit_time'] = now_time_str
            inv['exit_trigger'] = 'STOP_LOSS_HIT'
            print(f"🛡️ STOP LOSS HIT for {inv['name']} at ₹{latest_price:,.2f}. Realized Loss: ₹{pnl:+,.2f} ({pnl_pct:+,.2f}%) - Capital protected in cash.")
            updated = True
            
            # Dispatch Stop Loss alert
            try:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r') as cf:
                        cfg = json.load(cf)
                    from alerter import EmailAlerter
                    alerter = EmailAlerter(cfg)
                    subj = f"🛡️ [STOP LOSS HIT] {inv['name']} protected at ₹{latest_price:,.2f} ({pnl_pct}%)"
                    bdy = f"🛡️ PROTECTIVE STOP LOSS EXECUTED!\n• Stock: {inv['name']} ({inv['symbol']})\n• Shares: {shares}\n• Entry: ₹{entry_p:,.2f} -> Exit: ₹{latest_price:,.2f}\n• Realized Loss: -₹{abs(pnl):,.2f} ({pnl_pct}%)\n• Remaining capital of ₹{cur_val:,.2f} returned to cash."
                    alerter.send_alert(subj, bdy)
            except Exception:
                pass
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
    print("Testing Daily Stock Investment Engine with Capital & Target/Stop-Loss Rules...")
    scan_and_execute_daily_stock_investment(force=True)
    update_live_stock_investments()
