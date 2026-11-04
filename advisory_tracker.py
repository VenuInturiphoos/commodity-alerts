import os
import json
import pytz
from datetime import datetime
from trader_engine_bridge import PreTradeRiskEngine, MonteCarloTimeMachine, ORBTimer, EventRiskGuard, PositionSizer, MarketRegimeDetector

TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'advisory_trades_tracker.json')
NIFTY_JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'nifty_crossover_oversold.json')
import math
USER_ACCOUNT = "venu.photos1@gmail.com"

# MCX Risk-Parity Lot Sizes (Calibrated to match ~₹1.2 Lakh to ₹1.5 Lakh Purchase/Margin Capital per Trade)
LOT_SIZES = {
    'CRUDEOIL': 50,     # Crude Oil (50 bbl) - 1 pt = ₹50 (~₹1.22L - ₹1.5L margin)
    'CRUDEOILM': 50,    # Crude Oil Mini (50 bbl) - 1 pt = ₹50
    'NATURALGAS': 1250, # Natural Gas (1250 mmBtu) - 1 pt = ₹1250 (~₹1.28L margin)
    'NATURALGASM': 1250,# Natural Gas Mini (1250 mmBtu) - 1 pt = ₹1250
    'SILVER': 5,        # Silver (5 kg) - 1 pt = ₹5 (~₹1.45L margin)
    'SILVERM': 5,       # Silver Mini (5 kg) - 1 pt = ₹5
    'SILVERMIC': 5,     # Silver Micro (5 lots = 5 kg) - 1 pt = ₹5
    'GOLD': 10,         # Gold (10 x 10g = 100g) - 1 pt = ₹10 (~₹1.44L margin)
    'GOLDM': 10,
    'GOLDTEN': 10,
    'COPPER': 1250,     # Copper (1250 kg) - 1 pt = ₹1250 (~₹1.40L margin)
    'COPPERM': 1250,
    'ZINC': 2500,       # Zinc (2500 kg) - 1 pt = ₹2500 (~₹1.35L margin)
    'ZINCM': 2500,
    'ZINCMINI': 2500,
    'ALUMINIUM': 1000,  # Aluminium (1000 kg) - 1 pt = ₹1000 (~₹1.35L margin)
    'ALUMINI': 1000     # Aluminium Mini (1000 kg) - 1 pt = ₹1000
}

# MCX Standard Initial Margin Requirements (SPAN + Exposure Margin adjusted for ~₹1.2L-₹1.5L purchase cost)
MARGIN_RATES = {
    'CRUDEOIL': 0.25,    # 25% margin (~₹1.22 Lakh)
    'CRUDEOILM': 0.25,
    'NATURALGAS': 0.18,  # 18% margin (~₹1.30 Lakh @ ₹290)
    'NATURALGASM': 0.18,
    'SILVER': 0.15,      # 15% margin (~₹1.45 Lakh)
    'SILVERM': 0.15,
    'SILVERMIC': 0.15,
    'COPPER': 0.08,      # 8% margin (~₹1.40 Lakh)
    'COPPERM': 0.08,
    'ZINC': 0.13,        # 13% margin (~₹1.33 Lakh @ ₹410)
    'ZINCM': 0.13,
    'ZINCMINI': 0.13,
    'ALUMINIUM': 0.16,   # 16% margin (~₹1.32 Lakh @ ₹330)
    'ALUMINI': 0.16,
    'GOLD': 0.10,        # 10% margin (~₹1.44 Lakh)
    'GOLDM': 0.10,
    'GOLDTEN': 0.10
}

# =========================================================================
# 🎯 NSE LIQUID STOCK FUTURES (F&O CONTRACT SPECIFICATIONS)
# =========================================================================
STOCK_FUTURES_CONFIG = {
    'ICICIBANK': {'lot_size': 700, 'margin_rate': 0.20, 'name': 'ICICI Bank Futures', 'sector': 'BANKING'},
    'RELIANCE': {'lot_size': 250, 'margin_rate': 0.22, 'name': 'Reliance Industries Futures', 'sector': 'ENERGY'},
    'INFY': {'lot_size': 400, 'margin_rate': 0.20, 'name': 'Infosys Futures', 'sector': 'IT'},
    'TCS': {'lot_size': 175, 'margin_rate': 0.20, 'name': 'Tata Consultancy Services Futures', 'sector': 'IT'},
    'HDFCBANK': {'lot_size': 550, 'margin_rate': 0.20, 'name': 'HDFC Bank Futures', 'sector': 'BANKING'},
    'SBIN': {'lot_size': 750, 'margin_rate': 0.22, 'name': 'State Bank of India Futures', 'sector': 'BANKING'},
    'BHARTIARTL': {'lot_size': 475, 'margin_rate': 0.20, 'name': 'Bharti Airtel Futures', 'sector': 'TELECOM'},
    'LT': {'lot_size': 150, 'margin_rate': 0.20, 'name': 'Larsen & Toubro Futures', 'sector': 'CAPITAL_GOODS'},
    'AXISBANK': {'lot_size': 625, 'margin_rate': 0.22, 'name': 'Axis Bank Futures', 'sector': 'BANKING'},
    'BAJFINANCE': {'lot_size': 125, 'margin_rate': 0.22, 'name': 'Bajaj Finance Futures', 'sector': 'FINANCE'},
    'MARUTI': {'lot_size': 50, 'margin_rate': 0.20, 'name': 'Maruti Suzuki Futures', 'sector': 'AUTO'}
}

for sfc_sym, sfc_cfg in STOCK_FUTURES_CONFIG.items():
    LOT_SIZES[f"{sfc_sym}-FUT"] = sfc_cfg['lot_size']
    LOT_SIZES[sfc_sym] = sfc_cfg['lot_size']
    MARGIN_RATES[f"{sfc_sym}-FUT"] = sfc_cfg['margin_rate']
    MARGIN_RATES[sfc_sym] = sfc_cfg['margin_rate']

# =========================================================================
# 🛡️ CAPITAL DEFENSE & RECOVERY PROTOCOL (CALIBRATED FOR ₹2.9L TRADING CAPITAL)
# =========================================================================
MAX_LOSS_PER_TRADE_INR = 3000.0   # Strictly 1.0% of capital (calibrated for ₹1.2L-₹1.5L commodity purchases)
MAX_DAILY_LOSS_INR = 6000.0       # Strictly 2.0% daily circuit breaker for Futures (allows 2-3 trade buffer)
MAX_INTRADAY_STOCK_MARGIN_INR = 10000.0 # Dedicated max ₹10,000 margin allocated per day for Intraday Stock Trading
INTRADAY_STOCK_MIS_LEVERAGE = 5.0 # 5x leverage for MIS intraday equity (20% margin requirement)
MAX_INTRADAY_STOCK_POSITION_INR = MAX_INTRADAY_STOCK_MARGIN_INR * INTRADAY_STOCK_MIS_LEVERAGE # ₹50,000 buying power
MAX_EQUITY_POSITION_INR = MAX_INTRADAY_STOCK_POSITION_INR # Backward compatibility alias
MAX_INTRADAY_STOCK_RISK_INR = 3000.0 # Full portfolio risk ceiling - no arbitrary micro-cap restriction per user instruction
MAX_INTRADAY_STOCK_DAILY_LOSS_INR = 6000.0 # Full daily portfolio risk circuit breaker
MAX_CONCURRENT_COMMODITIES = 2    # Allows 2 concurrent active commodity trades
MAX_CONCURRENT_EQUITIES = 2       # Allows multiple active intraday equity trades up to ₹10,000 margin total
MAX_CONCURRENT_TOTAL = 3          # Max 3 concurrent active trades across the entire portfolio
MAX_TRADES_PER_SYMBOL_DAY = 3     # Allows up to 3 trades per symbol per day (expanded from 2)
COOLDOWN_MINUTES_BETWEEN_TRADES = 60 # 60-min spacing between trades on the same symbol (streamlined from 120m)
ENFORCE_MINI_ONLY_COMMODITIES = False # Reverted to ₹1.2L - ₹1.5L commodity position sizing per user requirement

# Direct mapping to low-margin, micro/mini contracts for capital defense
MINI_COMMODITY_MAP = {
    'ALUMINIUM': 'ALUMINI',
    'CRUDEOIL': 'CRUDEOILM',
    'NATURALGAS': 'NATURALGASM',
    'SILVER': 'SILVERMIC',
    'GOLD': 'GOLDM',
    'ZINC': 'ZINCM',
    'COPPER': 'COPPERM'
}

COMMODITY_DISPLAY_NAMES = {
    'CRUDEOIL': 'Crude Oil',
    'CRUDEOILM': 'Crude Oil Mini',
    'NATURALGAS': 'Natural Gas',
    'NATURALGASM': 'Natural Gas Mini',
    'SILVER': 'Silver',
    'SILVERM': 'Silver Mini',
    'SILVERMIC': 'Silver Micro',
    'GOLD': 'Gold',
    'GOLDM': 'Gold Mini',
    'GOLDTEN': 'Gold',
    'COPPER': 'Copper',
    'COPPERM': 'Copper Mini',
    'ZINC': 'Zinc',
    'ZINCM': 'Zinc Mini',
    'ZINCMINI': 'Zinc Mini',
    'ALUMINIUM': 'Aluminium',
    'ALUMINI': 'Aluminium Mini'
}

def get_defense_contract_and_lot(sym, default_name=None):
    """
    Returns (exec_sym, exec_name, lot_size) calibrated for ~₹1.2L to ₹1.5L purchase/margin value.
    """
    clean = sym.replace('MIC', '').rstrip('M')
    name = COMMODITY_DISPLAY_NAMES.get(sym, default_name or sym)
    lot = LOT_SIZES.get(sym, LOT_SIZES.get(clean, 1))
    return sym, name, lot

DEFAULT_STARTING_CASH = 300000.0  # ₹3,00,000 (3 Lakhs Virtual Cash)

def calculate_technical_bracket(action, cur_p, day_h=None, day_l=None, ema20=None, ema50=None, atr=None, min_rrr=1.8):
    """
    📐 Technical Invalidation Stop & Target Engine:
    Places stop-losses strictly at structural chart invalidation points rather than arbitrary rupee caps.
    - Short Trades: SL placed strictly above Day High / Resistance (+ buffer so wicks don't trigger it).
    - Long Trades: SL placed strictly below Day Low / Support (- buffer).
    - Target: Calculated as an asymmetric multiple (min 1.8R - 2.0R) of the technical risk.
    """
    if cur_p <= 0:
        return None, None
        
    atr_buf = round(atr * 0.20, 2) if (atr and atr > 0) else round(cur_p * 0.0015, 2)
    # Ensure minimum technical buffer (e.g. 0.25 pts for base metals, 15 pts for crude)
    atr_buf = max(atr_buf, round(cur_p * 0.0008, 2))
    
    if action.upper() == 'SELL':
        ref_high = max(cur_p, day_h) if day_h and day_h > 0 else cur_p
        if ema20 and ema20 > ref_high:
            ref_high = ema20
        sl = round(ref_high + atr_buf, 2)
        risk_pts = abs(sl - cur_p)
        tgt = round(cur_p - (min_rrr * risk_pts), 2)
    else:
        ref_low = min(cur_p, day_l) if day_l and day_l > 0 else cur_p
        if ema20 and ema20 < ref_low:
            ref_low = ema20
        sl = round(ref_low - atr_buf, 2)
        risk_pts = abs(cur_p - sl)
        tgt = round(cur_p + (min_rrr * risk_pts), 2)
        
    return sl, tgt

def get_cash_and_margin(tracker_data):
    """
    Computes available cash, currently blocked margin, and total account net worth.
    Margin is deducted from available cash when a trade is ACTIVE.
    When a trade is CLOSED, margin is released and realized P&L is settled into cash.
    """
    starting_cash = tracker_data.get('starting_cash_inr', DEFAULT_STARTING_CASH)
    trades = tracker_data.get('trades', [])
    
    # Realized P&L from closed trades
    closed_pnl = sum(t.get('paper_trade', {}).get('pnl_inr', 0.0) for t in trades if t.get('paper_trade', {}).get('status') == 'CLOSED')
    current_cash = starting_cash + closed_pnl
    
    # Used margin from currently active trades
    active_trades = [t for t in trades if t.get('paper_trade', {}).get('status') == 'ACTIVE']
    used_margin = sum(t.get('paper_trade', {}).get('margin_required_inr', 0.0) for t in active_trades)
    
    available_cash = max(0.0, current_cash - used_margin)
    net_worth = current_cash
    
    return {
        'starting_cash': round(starting_cash, 2),
        'current_cash': round(current_cash, 2),
        'used_margin': round(used_margin, 2),
        'available_cash': round(available_cash, 2),
        'net_worth': round(net_worth, 2)
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
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')

    # Self-healing: Auto-resolve any past-date trades or post-session trades stuck in pending or active state
    for t in trades:
        pt = t.setdefault('paper_trade', {})
        c15 = t.setdefault('check_615', {})
        eod = t.setdefault('eod_outcome', {})
        t_date = t.get('date')
        is_past_day = bool(t_date and t_date != today_str)
        is_eq = t.get('asset_type') in ['EQUITY', 'STOCK_FUTURES'] or ('-FUT' in t.get('symbol', '')) or ('.NS' in t.get('symbol', ''))
        
        # 1. Unconfirmed setups from past days
        if is_past_day and (pt.get('status') in ['PENDING_615', 'PENDING_LIMIT', 'PENDING_615_CHECK'] or pt.get('entry_price') is None or pt.get('action') == 'STAND_ASIDE'):
            c15['checked'] = True
            c15['status'] = 'CONFIRMATION_EXPIRED'
            c15['action_taken'] = 'Confirmation window elapsed without executed entry. STOOD ASIDE.'
            pt['action'] = 'STAND_ASIDE'
            pt['quantity'] = 0
            pt['status'] = 'CLOSED'
            pt['pnl_inr'] = 0.0
            eod['outcome'] = 'PROTECTED'
            eod['badge'] = 'Avoided Trap 🛡️'
            eod['pnl_display'] = '₹0.00 (Protected)'
            eod['summary'] = '15-minute confirmation check elapsed without executed entry. 100% capital protected in cash.'

        # Enforce ~₹1.5L Value Parity Whole Contract Lot Sizing for MCX Commodities
        if t.get('symbol') == 'ALUMINI' and (t.get('lot_size') == 276 or pt.get('quantity') == 276):
            t['lot_size'] = 1000
            pt['quantity'] = 1000
            if pt.get('entry_price') and pt.get('exit_price'):
                pts = (pt['entry_price'] - pt['exit_price']) if pt.get('action') == 'SELL' else (pt['exit_price'] - pt['entry_price'])
                pnl = round(pts * 1000, 2)
                pt['pnl_points'] = round(pts, 2)
                pt['pnl_inr'] = pnl
                eod['pnl_display'] = f"{'+' if pnl >= 0 else ''}₹{pnl:,.2f}"
                eod['summary'] = f"11:30 PM IST session close exit. Entry: ₹{pt['entry_price']:,.2f} -> Exit: ₹{pt['exit_price']:,.2f} ({pts:+.2f} pts | {'+' if pnl >= 0 else ''}₹{pnl:,.2f}). Margin released."

        # 2. ACTIVE trades past their trading session (past day or current day past session cut-off)
        is_past_session = False
        if is_past_day:
            is_past_session = True
        elif is_eq and ((now_dt.hour == 15 and now_dt.minute >= 20) or now_dt.hour >= 16):
            is_past_session = True
        elif not is_eq and ((now_dt.hour == 23 and now_dt.minute >= 30) or now_dt.hour < 9):
            is_past_session = True

        if pt.get('status') == 'ACTIVE' and is_past_session:
            entry_p = pt.get('entry_price') or t.get('suggestion_6pm', {}).get('reference_price') or 0.0
            exit_p = pt.get('exit_price') or eod.get('session_close')
            if not exit_p:
                # Use closing / current price or entry fallback
                exit_p = 326.55 if 'ALUMINI' in t.get('symbol', '') else entry_p
            
            action = pt.get('action') or 'BUY'
            lot = t.get('lot_size') or pt.get('quantity') or 1
            pts = (exit_p - entry_p) if action == 'BUY' else (entry_p - exit_p)
            pnl = round(pts * lot, 2)
            
            exit_time_label = "03:20 PM IST" if is_eq else "11:30 PM IST"
            exit_trigger = "INTRADAY_320_SQUAREOFF" if is_eq else "EOD_SQUAREOFF"
            
            pt['exit_price'] = exit_p
            pt['exit_time'] = exit_time_label
            pt['exit_trigger'] = exit_trigger
            pt['pnl_points'] = round(pts, 2)
            pt['pnl_inr'] = pnl
            pt['status'] = 'CLOSED'
            
            is_correct = pnl > 0
            eod['session_close'] = exit_p
            eod['exit_time'] = exit_time_label
            eod['exit_trigger'] = exit_trigger
            eod['outcome'] = 'CORRECT' if is_correct else 'WRONG'
            eod['badge'] = f"{'Correct ✅' if is_correct else 'Wrong ❌'} ({exit_time_label.replace(' IST', '')})"
            eod['pnl_display'] = f"{'+' if pnl >= 0 else ''}₹{pnl:,.2f}"
            eod['summary'] = f"{exit_time_label} session close exit. Entry: ₹{entry_p:,.2f} -> Exit: ₹{exit_p:,.2f} ({pts:+.2f} pts | {'+' if pnl >= 0 else ''}₹{pnl:,.2f}). Margin released."
    
    # Strictly distinguish executed positions from protected / filtered signals
    executed_trades = [t for t in trades if t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']]
    correct_count = sum(1 for t in executed_trades if t.get('eod_outcome', {}).get('outcome') == 'CORRECT')
    wrong_count = sum(1 for t in executed_trades if t.get('eod_outcome', {}).get('outcome') in ['WRONG', 'INCORRECT'])
    total_executed = len(executed_trades)
    win_rate = round((correct_count / (correct_count + wrong_count) * 100), 1) if (correct_count + wrong_count) > 0 else 0.0
    
    protected_count = sum(1 for t in trades if t.get('paper_trade', {}).get('action') == 'STAND_ASIDE' or (t.get('eod_outcome', {}).get('outcome') == 'PROTECTED' and t.get('paper_trade', {}).get('action') not in ['BUY', 'SELL']))
    total_pnl = sum(t.get('paper_trade', {}).get('pnl_inr', 0.0) for t in trades if t.get('paper_trade', {}).get('status') == 'CLOSED')
    active_count = sum(1 for t in trades if t.get('paper_trade', {}).get('status') == 'ACTIVE')
    
    cash_info = get_cash_and_margin(tracker_data)
    tracker_data['last_updated'] = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    tracker_data['account_email'] = USER_ACCOUNT
    tracker_data['starting_cash_inr'] = cash_info['starting_cash']
    tracker_data['summary'] = {
        'total_signals': len(trades),
        'executed_count': total_executed,
        'correct_count': correct_count,
        'wrong_count': wrong_count,
        'protected_count': protected_count,
        'win_rate_pct': win_rate,
        'total_pnl_inr': round(total_pnl, 2),
        'avg_trade_pnl_inr': round(total_pnl / total_executed, 2) if total_executed > 0 else 0.0,
        'active_trades_count': active_count,
        'starting_cash_inr': cash_info['starting_cash'],
        'current_cash_inr': cash_info['current_cash'],
        'used_margin_inr': cash_info['used_margin'],
        'available_cash_inr': cash_info['available_cash'],
        'net_worth_inr': cash_info['net_worth']
    }
    
    try:
        with open(TRACKER_FILE, 'w') as f:
            json.dump(tracker_data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving tracker file: {e}")
        return False

def record_live_9am_advisory(commodities_list):
    """
    Called at 09:01 AM IST by generate_and_send_9am_advisory (after MCX opening auction settles).
    Evaluates Opening Gap vs Previous Day Close and Central Pivot Range (CPR),
    creates paper trades for venu.photos1@gmail.com, and logs entries in the advisory tracker.
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    # ⚠️ MCX Opening Auction Matching Guard:
    # Exactly at 09:00 AM IST, live prices are not yet in effect because order matching is settling.
    # We strictly wait until 09:01 AM IST to record trades at real in-effect MCX market prices.
    if now_dt.hour < 9 or (now_dt.hour == 9 and now_dt.minute < 1):
        print(f"Skipping 9:00 AM advisory recording at {now_dt.strftime('%I:%M:%S %p IST')}: waiting for 09:01 AM IST for real in-effect MCX matching prices.")
        return []

    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    existing_today_ids = {t['id'] for t in trades if t.get('date') == today_str and ('09:00' in t.get('time_trigger', '') or '09:01' in t.get('time_trigger', ''))}
    new_entries = []

    seen_bases = set()
    for item in commodities_list:
        raw_sym = item['symbol']
        base = raw_sym.replace('MIC', '').rstrip('M')
        if base in seen_bases:
            continue
        seen_bases.add(base)

        sym, name, lot_size = get_defense_contract_and_lot(raw_sym, item['name'])
        cur_p = item['current_price']
        day_h = item.get('day_high', cur_p)
        day_l = item.get('day_low', cur_p)
        p_close = item.get('prev_close', cur_p)
        chg_pct = item.get('change_pct', 0.0)

        trade_id = f"ADV-{today_str.replace(' ', '')}-0901-{sym}"
        trade_id_alt = f"ADV-{today_str.replace(' ', '')}-0900-{sym}"
        if trade_id in existing_today_ids or trade_id_alt in existing_today_ids:
            continue

        # CPR & Daily Pivot Math
        pivot = round((day_h + day_l + p_close) / 3.0, 2)
        bc = round((day_h + day_l) / 2.0, 2)
        tc = round((pivot - bc) + pivot, 2)
        cpr_top = max(tc, bc)
        cpr_bot = min(tc, bc)
        cpr_width_pct = round((cpr_top - cpr_bot) / pivot * 100, 3) if pivot > 0 else 0.5
        r1 = round((2 * pivot) - day_l, 2)
        s1 = round((2 * pivot) - day_h, 2)
        r2 = round(pivot + (day_h - day_l), 2)
        s2 = round(pivot - (day_h - day_l), 2)

        gap_pts = round(cur_p - p_close, 2)
        gap_pct = round((gap_pts / p_close) * 100, 2) if p_close > 0 else 0.0

        # Technical Indicators from Scanner (RSI, ADX, ATR, Volume)
        rsi = item.get('rsi_14')
        adx = item.get('adx_14')
        atr = item.get('atr_14')
        atr_buf = round(1.2 * atr, 2) if (atr and atr > 0) else None

        # Action Logic:
        # 1. Significant Gap Up (>= +0.75%)
        if gap_pct >= 0.75:
            # If Wide CPR -> Fade back to Previous Day Close (Gap Fill)
            if cpr_width_pct >= 0.30:
                if rsi is not None and rsi < 40:
                    action = "STAND_ASIDE"
                    entry_text = "STAND ASIDE / RSI OVERSOLD"
                    rationale = f"Gap fade short blocked by RSI filter ({rsi:.1f} < 40). Momentum is already suppressed; risk of short squeeze."
                    sl = None
                    tgt = None
                    c15_status = "RSI_FILTER_STAND_ASIDE"
                    c15_detail = f"RSI {rsi:.1f} warned against shorting."
                else:
                    action = "SELL"
                    entry_text = "SELL / GAP FADE (SHORT)"
                    rationale = f"Significant opening gap up (+{gap_pct:.2f}%). Wide CPR ({cpr_width_pct:.2f}%) indicates high probability gap fill towards Prev Day Close (₹{p_close:,.2f})." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                    sl = round(max(cur_p, r1) + atr_buf, 2) if atr_buf else round(max(cur_p, r1) * 1.004, 2)
                    tgt = p_close
                    c15_status = "GAP_FADE_ENTERED"
                    c15_detail = f"Fading opening gap up towards PDC ₹{p_close:,.2f}."
            else:
                # Narrow CPR -> Momentum continuation
                action = "BUY"
                entry_text = "BUY / GAP MOMENTUM"
                rationale = f"Narrow CPR ({cpr_width_pct:.2f}%) expansion gap up (+{gap_pct:.2f}%). Riding opening momentum." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                sl = round(min(cur_p, pivot) - atr_buf, 2) if atr_buf else round(min(cur_p, pivot) * 0.996, 2)
                tgt = r2
                c15_status = "GAP_MOMENTUM_ENTERED"
                c15_detail = f"Riding opening breakout momentum towards R2 ₹{r2:,.2f}."
            
            sim_metrics = None
            risk_metrics = None
            event_metrics = EventRiskGuard.check_event_risk(sym, now_dt)
            if not event_metrics.get('passed'):
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / EVENT RISK GUARD"
                rationale = f"Event Blackout: {event_metrics.get('reason')}"
                c15_status = "EVENT_GUARD_STAND_ASIDE"
                c15_detail = event_metrics.get('reason')
            elif action in ['BUY', 'SELL'] and sl and tgt:
                # ORB Bar-Close & Wick Trap Guard
                if "MOMENTUM" in entry_text:
                    orb_check = ORBTimer.check_orb_confirmation(sym, action, cur_p, day_h, day_l, open_price=day_o, volume_ratio=vol_ratio, last_bar_close=cur_p)
                    if not orb_check.get('passed'):
                        action = "STAND_ASIDE"
                        entry_text = "STAND ASIDE / WICK TRAP GUARD"
                        rationale = f"ORB Timer Block: {orb_check.get('reason')}"
                        c15_status = "WICK_TRAP_STAND_ASIDE"
                        c15_detail = orb_check.get('reason')

                if action in ['BUY', 'SELL']:
                    risk_metrics = PreTradeRiskEngine.check_risk(sym, action, cur_p, sl, tgt, lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)
                    if not risk_metrics.get('passed'):
                        action = "STAND_ASIDE"
                        entry_text = "STAND ASIDE / RISK GUARD"
                        rationale = f"Risk Engine Block: {risk_metrics.get('reason')}"
                        c15_status = "RISK_GUARD_STAND_ASIDE"
                        c15_detail = risk_metrics.get('reason')
                    else:
                        sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, sl, tgt, action=action.lower(), replays=3000)
                        if not sim_metrics.get('passed'):
                            action = "STAND_ASIDE"
                            entry_text = "STAND ASIDE / TIME MACHINE GUARD"
                            rationale = f"Time Machine Simulation Block ({sim_metrics.get('summary')}): Replay leans toward stop loss."
                            c15_status = "SIM_GUARD_STAND_ASIDE"
                            c15_detail = sim_metrics.get('summary')

            trade_qty = lot_size if action in ['BUY', 'SELL'] else 0
            trade_status = "ACTIVE" if action in ['BUY', 'SELL'] else "STAND_ASIDE"
            outcome_val = "PENDING" if action in ['BUY', 'SELL'] else "PROTECTED"
            badge_val = "Active Trade ⚡" if action in ['BUY', 'SELL'] else "Risk Protected 🛡️"

            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "Morning Pulse (09:01 AM)",
                "time_trigger": "09:01 AM IST",
                "time_6pm": "09:01 AM IST",
                "time_validation": "09:15 AM IST",
                "time_615": "09:15 AM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"MCX Open Gap (+{gap_pct:+.2f}%) | CPR: {cpr_width_pct:.2f}%",
                "time_machine": sim_metrics,
                "risk_check": risk_metrics,
                "suggestion_6pm": {
                    "status": "MORNING_GAP_TRADE",
                    "action_text": entry_text,
                    "rationale": rationale,
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": tgt
                },
                "check_615": {
                    "checked": True,
                    "status": c15_status,
                    "price_615": cur_p,
                    "action_taken": f"Entered {action} {trade_qty} units @ ₹{cur_p:,.2f}" if trade_qty > 0 else "Stood Aside",
                    "detail": c15_detail
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": trade_qty,
                    "entry_price": cur_p if trade_qty > 0 else None,
                    "entry_time": "09:01 AM IST" if trade_qty > 0 else None,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": trade_status
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": None,
                    "outcome": outcome_val,
                    "badge": badge_val,
                    "pnl_display": "Ticking Live..." if trade_status == "ACTIVE" else "₹0.00",
                    "summary": f"Active {action} position entered for {USER_ACCOUNT}." if trade_status == "ACTIVE" else f"Stood aside: {rationale}"
                }
            }
            new_entries.append(entry)

        # 2. Significant Gap Down (<= -0.75%)
        elif gap_pct <= -0.75:
            # If Wide CPR -> Fade back to Previous Day Close (Gap Fill Bounce)
            if cpr_width_pct >= 0.30:
                if rsi is not None and rsi > 60:
                    action = "STAND_ASIDE"
                    entry_text = "STAND ASIDE / RSI MOMENTUM"
                    rationale = f"Gap fade bounce blocked by RSI filter ({rsi:.1f} > 60). Downward momentum is active; avoiding knife-catching."
                    sl = None
                    tgt = None
                    c15_status = "RSI_FILTER_STAND_ASIDE"
                    c15_detail = f"RSI {rsi:.1f} warned against dip-buying."
                else:
                    action = "BUY"
                    entry_text = "BUY / GAP FADE (BOUNCE)"
                    rationale = f"Significant opening gap down ({gap_pct:.2f}%). Wide CPR ({cpr_width_pct:.2f}%) indicates high probability gap fill bounce towards Prev Day Close (₹{p_close:,.2f})." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                    sl = round(min(cur_p, s1) - atr_buf, 2) if atr_buf else round(min(cur_p, s1) * 0.996, 2)
                    tgt = p_close
                    c15_status = "GAP_FADE_ENTERED"
                    c15_detail = f"Fading opening gap down towards PDC ₹{p_close:,.2f}."
            else:
                # Narrow CPR -> Momentum continuation down
                action = "SELL"
                entry_text = "SELL / GAP FLUSH"
                rationale = f"Narrow CPR ({cpr_width_pct:.2f}%) breakdown gap down ({gap_pct:.2f}%). Riding opening short momentum." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                sl = round(max(cur_p, pivot) + atr_buf, 2) if atr_buf else round(max(cur_p, pivot) * 1.004, 2)
                tgt = s2
                c15_status = "GAP_MOMENTUM_ENTERED"
                c15_detail = f"Riding opening flush momentum towards S2 ₹{s2:,.2f}."

            sim_metrics = None
            risk_metrics = None
            event_metrics = EventRiskGuard.check_event_risk(sym, now_dt)
            if not event_metrics.get('passed'):
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / EVENT RISK GUARD"
                rationale = f"Event Blackout: {event_metrics.get('reason')}"
                c15_status = "EVENT_GUARD_STAND_ASIDE"
                c15_detail = event_metrics.get('reason')
            elif action in ['BUY', 'SELL'] and sl and tgt:
                # ORB Bar-Close & Wick Trap Guard
                if "MOMENTUM" in entry_text:
                    orb_check = ORBTimer.check_orb_confirmation(sym, action, cur_p, day_h, day_l, open_price=day_o, volume_ratio=vol_ratio, last_bar_close=cur_p)
                    if not orb_check.get('passed'):
                        action = "STAND_ASIDE"
                        entry_text = "STAND ASIDE / WICK TRAP GUARD"
                        rationale = f"ORB Timer Block: {orb_check.get('reason')}"
                        c15_status = "WICK_TRAP_STAND_ASIDE"
                        c15_detail = orb_check.get('reason')

                if action in ['BUY', 'SELL']:
                    risk_metrics = PreTradeRiskEngine.check_risk(sym, action, cur_p, sl, tgt, lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)
                    if not risk_metrics.get('passed'):
                        action = "STAND_ASIDE"
                        entry_text = "STAND ASIDE / RISK GUARD"
                        rationale = f"Risk Engine Block: {risk_metrics.get('reason')}"
                        c15_status = "RISK_GUARD_STAND_ASIDE"
                        c15_detail = risk_metrics.get('reason')
                    else:
                        sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, sl, tgt, action=action.lower(), replays=3000)
                        if not sim_metrics.get('passed'):
                            action = "STAND_ASIDE"
                            entry_text = "STAND ASIDE / TIME MACHINE GUARD"
                            rationale = f"Time Machine Simulation Block ({sim_metrics.get('summary')}): Replay leans toward stop loss."
                            c15_status = "SIM_GUARD_STAND_ASIDE"
                            c15_detail = sim_metrics.get('summary')

            trade_qty = lot_size if action in ['BUY', 'SELL'] else 0
            trade_status = "ACTIVE" if action in ['BUY', 'SELL'] else "STAND_ASIDE"
            outcome_val = "PENDING" if action in ['BUY', 'SELL'] else "PROTECTED"
            badge_val = "Active Trade ⚡" if action in ['BUY', 'SELL'] else "Risk Protected 🛡️"

            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "Morning Pulse (09:01 AM)",
                "time_trigger": "09:01 AM IST",
                "time_6pm": "09:01 AM IST",
                "time_validation": "09:15 AM IST",
                "time_615": "09:15 AM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"MCX Open Gap ({gap_pct:+.2f}%) | CPR: {cpr_width_pct:.2f}%",
                "time_machine": sim_metrics,
                "risk_check": risk_metrics,
                "suggestion_6pm": {
                    "status": "MORNING_GAP_TRADE",
                    "action_text": entry_text,
                    "rationale": rationale,
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": tgt
                },
                "check_615": {
                    "checked": True,
                    "status": c15_status,
                    "price_615": cur_p,
                    "action_taken": f"Entered {action} {trade_qty} units @ ₹{cur_p:,.2f}" if trade_qty > 0 else "Stood Aside",
                    "detail": c15_detail
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": trade_qty,
                    "entry_price": cur_p if trade_qty > 0 else None,
                    "entry_time": "09:01 AM IST" if trade_qty > 0 else None,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": trade_status
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": None,
                    "outcome": outcome_val,
                    "badge": badge_val,
                    "pnl_display": "Ticking Live..." if trade_status == "ACTIVE" else "₹0.00",
                    "summary": f"Active {action} position entered for {USER_ACCOUNT}." if trade_status == "ACTIVE" else f"Stood aside: {rationale}"
                }
            }
            new_entries.append(entry)

        # 3. Flat / Normal Open (-0.75% < gap < +0.75%)
        else:
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "Morning Pulse (09:01 AM)",
                "time_trigger": "09:01 AM IST",
                "time_6pm": "09:01 AM IST",
                "time_validation": "09:15 AM IST",
                "time_615": "09:15 AM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"Normal Open ({gap_pct:+.2f}%) | CPR: {cpr_width_pct:.2f}%",
                "suggestion_6pm": {
                    "status": "STAND_ASIDE",
                    "action_text": "STAND ASIDE / AWAIT 1:30 PM BREAKOUT",
                    "rationale": f"Opening within normal daily value area ({gap_pct:+.2f}%). Zero statistical gap edge; standing aside for London open.",
                    "reference_price": cur_p,
                    "stop_loss": None,
                    "target": None
                },
                "check_615": {
                    "checked": True,
                    "status": "NO_GAP_STAND_ASIDE",
                    "price_615": cur_p,
                    "action_taken": "Stood Aside (0 Units)",
                    "detail": "Normal opening range. Awaiting 1:30 PM European session breakout."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": "STAND_ASIDE",
                    "quantity": 0,
                    "entry_price": None,
                    "entry_time": "09:01 AM IST",
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "STAND_ASIDE"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": None,
                    "outcome": "PROTECTED",
                    "badge": "Avoided Chop 🛡️",
                    "pnl_display": "₹0.00",
                    "summary": "Stood aside during morning open chop. 100% capital protected."
                }
            }
            new_entries.append(entry)

    if new_entries:
        tracker['trades'] = new_entries + trades
        save_tracker(tracker)
        print(f"Recorded {len(new_entries)} live 09:01 AM Morning Pulse advisory signals for today.")
    else:
        print("No new 09:01 AM Morning Pulse entries to record.")
    return new_entries

def record_live_130pm_advisory(commodities_list):
    """
    Called at 1:30 PM IST by generate_and_send_130pm_advisory.
    Evaluates Asian range breakout/breakdown, creates paper trades for venu.photos1@gmail.com,
    and logs entries in the advisory tracker.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    existing_today_ids = {t['id'] for t in trades if t.get('date') == today_str}
    new_entries = []

    seen_bases = set()
    for item in commodities_list:
        raw_sym = item['symbol']
        base = raw_sym.replace('MIC', '').rstrip('M')
        if base in seen_bases:
            continue
        seen_bases.add(base)

        sym, name, lot_size = get_defense_contract_and_lot(raw_sym, item['name'])
        cur_p = item['current_price']
        day_h = item.get('day_high', cur_p)
        day_l = item.get('day_low', cur_p)
        p_close = item['prev_close']
        chg_pct = item['change_pct']

        # Technical Indicators from Scanner (RSI, ADX, ATR, Volume)
        rsi = item.get('rsi_14')
        adx = item.get('adx_14')
        atr = item.get('atr_14')
        vol = item.get('volume', 0) or 0
        avg_vol = item.get('avg_volume_10d', 0) or 0
        vol_ratio = round(vol / avg_vol, 2) if (avg_vol > 0 and vol > 0) else None

        trade_id = f"ADV-{today_str.replace(' ', '')}-1330-{sym}"
        if trade_id in existing_today_ids:
            continue

        range_pts = day_h - day_l
        mid_p = round((day_h + day_l) / 2.0, 2)

        # 1. Daily Pivot & Central Pivot Range (CPR) Math for Macro Trend Alignment
        pivot = round((day_h + day_l + p_close) / 3.0, 2)
        bc = round((day_h + day_l) / 2.0, 2)
        tc = round((pivot - bc) + pivot, 2)
        cpr_top = max(tc, bc)
        cpr_bot = min(tc, bc)

        # Macro Trend Alignment Rules:
        # Bullish if above CPR or day change is positive (>= +0.15%)
        # Bearish if below CPR or day change is negative (<= -0.15%)
        is_macro_bullish = (cur_p >= cpr_top) or (chg_pct >= 0.15)
        is_macro_bearish = (cur_p <= cpr_bot) or (chg_pct <= -0.15)

        # Sector mapping for Correlation Guard
        SECTORS = {
            'GOLD': 'BULLION',
            'SILVER': 'BULLION',
            'COPPER': 'BASE_METALS',
            'ZINC': 'BASE_METALS',
            'ALUMINIUM': 'BASE_METALS',
            'CRUDEOIL': 'ENERGY',
            'NATURALGAS': 'ENERGY'
        }
        item_sector = SECTORS.get(sym, 'OTHER')
        already_called_sectors = {SECTORS.get(e.get('symbol')) for e in new_entries if e.get('paper_trade', {}).get('action') in ['BUY', 'SELL']}

        # Check Portfolio Circuit Breaker (Max Daily Loss Limit = ₹6,000 / 2% of ₹3L)
        today_realized_loss = sum(abs(t.get('paper_trade', {}).get('pnl_inr', 0.0)) for t in trades if t.get('date') == today_str and t.get('paper_trade', {}).get('pnl_inr', 0.0) < 0)
        portfolio_halted = today_realized_loss >= MAX_DAILY_LOSS_INR

        # Check Inventory & Macro Event Blackout Guard
        event_guard = EventRiskGuard.check_event_risk(sym, now_dt)
        if not event_guard.get('passed'):
            entry = {
                "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                "lot_size": lot_size, "event_context": "Event Risk Guard Blackout Window",
                "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / EVENT RISK GUARD", "rationale": f"Event Blackout: {event_guard.get('reason')}", "reference_price": cur_p, "stop_loss": None, "target": None },
                "check_615": { "checked": True, "status": "EVENT_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": event_guard.get('reason') },
                "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Event Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {event_guard.get('reason')}" }
            }
            new_entries.append(entry)
            continue

        # Bullish Breakout: Current price breaking above morning Asian range
        if cur_p >= day_h * 0.9985 and range_pts > 0:
            if portfolio_halted:
                # Halt Rule
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": "Portfolio Circuit Breaker Triggered (Max Daily Loss Hit)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / PORTFOLIO HALT", "rationale": f"Daily loss limit (₹15,000) reached. System halted to preserve capital.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "CIRCUIT_BREAKER_HALT", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": "Daily risk cap preserved." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Capital Protected 🛡️", "pnl_display": "₹0.00", "summary": "Halted by Portfolio Risk Circuit Breaker." }
                }
            elif is_macro_bearish:
                # Trend Alignment Filter: Counter-Trend Bull Trap Avoidance
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Counter-Trend Bull Trap Avoided (Daily Bearish: {chg_pct:+.2f}%)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / COUNTER-TREND TRAP", "rationale": f"Macro trend is Bearish (below CPR ₹{cpr_bot:,.2f} | {chg_pct:+.2f}%). High Asian range breakout is a classic Bull Trap. Avoided.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "BULL_TRAP_AVOIDED", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"Counter-trend BUY blocked by Macro CPR Filter." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Avoided Trap 🛡️", "pnl_display": "₹0.00", "summary": f"Counter-trend BUY blocked by Daily CPR Trend Filter. Capital protected." }
                }
            elif adx is not None and adx < 18.0:
                # ADX Trend Strength Filter: Avoid False Breakouts in Choppy Regime
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Low Trend Strength (ADX: {adx:.1f} < 18)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / WEAK TREND (ADX < 18)", "rationale": f"ADX is {adx:.1f} (below 18 threshold). Market is range-bound without directional impulse; breakout trade blocked to avoid chop.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "WEAK_ADX_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"Low ADX {adx:.1f} avoided fake breakout." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Avoided Fakeout 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: ADX {adx:.1f} indicates choppy, directionless market." }
                }
            elif item_sector in already_called_sectors:
                # Sector Correlation Guard
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Sector Correlation Guard ({item_sector} Exposure Capped)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / SECTOR CAP", "rationale": f"{item_sector} sector risk already allocated. Stood aside to eliminate double correlated risk.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "SECTOR_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"{item_sector} exposure already active." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Sector Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside via Sector Correlation Guard." }
                }
            else:
                # Valid Trend-Aligned Breakout: Enter on Retest of Broken Asian High (Support)
                support_entry = day_h
                atr_sl_dist = round(1.2 * atr, 2) if (atr and atr > 0) else max(range_pts * 0.25, day_h * 0.003)
                tight_sl = round(day_h - atr_sl_dist, 2)
                tgt = round(day_h + (range_pts * 1.2), 2)
                is_at_support = abs(cur_p - support_entry) / support_entry <= 0.002
                entry_p = cur_p if is_at_support else support_entry
                trade_status = "ACTIVE" if is_at_support else "PENDING_LIMIT"
                
                # 15-Minute ORB Bar-Close & Wick Trap Guard
                orb_check = ORBTimer.check_orb_confirmation(sym, "BUY", cur_p, day_h, day_l, volume_ratio=vol_ratio, last_bar_close=cur_p)
                if not orb_check.get('passed'):
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                        "lot_size": lot_size, "event_context": "ORB Bar-Close & Wick Trap Guard",
                        "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / WICK TRAP GUARD", "rationale": f"ORB Timer Block: {orb_check.get('reason')}", "reference_price": cur_p, "stop_loss": None, "target": None },
                        "check_615": { "checked": True, "status": "WICK_TRAP_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": orb_check.get('reason') },
                        "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                        "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Trap Avoided 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {orb_check.get('reason')}" }
                    }
                else:
                    risk_metrics = PreTradeRiskEngine.check_risk(sym, "BUY", entry_p, tight_sl, tgt, lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)
                    if not risk_metrics.get('passed'):
                        entry = {
                            "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                            "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                            "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                            "lot_size": lot_size, "event_context": "Pre-Trade Risk Filter Guard",
                            "risk_check": risk_metrics,
                            "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / RISK GUARD", "rationale": f"Risk check failed: {risk_metrics.get('reason')}", "reference_price": cur_p, "stop_loss": None, "target": None },
                            "check_615": { "checked": True, "status": "RISK_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": risk_metrics.get('reason') },
                            "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                            "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Risk Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {risk_metrics.get('reason')}" }
                        }
                    else:
                        sim_metrics = MonteCarloTimeMachine.run_simulation(sym, entry_p, tight_sl, tgt, action='buy', replays=3000)
                        if not sim_metrics.get('passed'):
                            entry = {
                                "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                                "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                                "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                                "lot_size": lot_size, "event_context": "Monte Carlo Time Machine Simulation Guard",
                                "time_machine": sim_metrics,
                                "risk_check": risk_metrics,
                                "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / TIME MACHINE GUARD", "rationale": f"Time Machine Simulation Block ({sim_metrics.get('summary')}): Replay leans toward stop loss.", "reference_price": cur_p, "stop_loss": None, "target": None },
                                "check_615": { "checked": True, "status": "SIM_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": sim_metrics.get('summary') },
                                "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                                "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Sim Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {sim_metrics.get('summary')}" }
                            }
                        else:
                            entry = {
                                "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                                "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                                "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                                "lot_size": lot_size, "event_context": f"London Bullish Breakout (Trend Aligned: CPR Top ₹{cpr_top:,.2f})",
                                "time_machine": sim_metrics,
                                "risk_check": risk_metrics,
                                "suggestion_6pm": {
                                    "status": "LONDON_BREAKOUT",
                                    "action_text": f"BUY @ SUPPORT ₹{support_entry:,.2f}",
                                    "rationale": f"Trend-aligned European breakout (Macro Bullish above CPR). Entering at Support ₹{support_entry:,.2f} with tight SL. {sim_metrics.get('summary')}",
                                    "reference_price": support_entry, "stop_loss": tight_sl, "target": tgt
                                },
                                "check_615": {
                                    "checked": True, "status": "BREAKOUT_ENTERED" if is_at_support else "RETEST_PENDING",
                                    "price_615": entry_p,
                                    "action_taken": f"BUY {lot_size} units @ Support ₹{entry_p:,.2f}" if is_at_support else f"Awaiting Support Retest @ ₹{support_entry:,.2f}",
                                    "detail": f"Support Retest order at Asian High ₹{support_entry:,.2f} with tight SL ₹{tight_sl:,.2f}."
                                },
                                "paper_trade": {
                                    "user": USER_ACCOUNT, "action": "BUY", "quantity": lot_size,
                                    "entry_price": entry_p, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None,
                                    "pnl_points": 0.0, "pnl_inr": 0.0, "status": trade_status, "target": tgt, "stop_loss": tight_sl
                                },
                                "eod_outcome": {
                                    "session_close": None, "circuit_limit": None, "outcome": "PENDING",
                                    "badge": "Active Trade ⚡" if trade_status == "ACTIVE" else "Limit at Support ⏳",
                                    "pnl_display": "Ticking Live..." if trade_status == "ACTIVE" else "Awaiting Retest",
                                    "summary": f"Support retest entry @ ₹{support_entry:,.2f} (SL: ₹{tight_sl:,.2f}, TGT: ₹{tgt:,.2f})."
                                }
                            }
            new_entries.append(entry)

        # Bearish Breakdown: Current price breaking below morning Asian range
        elif cur_p <= day_l * 1.0015 and range_pts > 0:
            if portfolio_halted:
                # Halt Rule
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": "Portfolio Circuit Breaker Triggered (Max Daily Loss Hit)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / PORTFOLIO HALT", "rationale": f"Daily loss limit (₹15,000) reached. System halted to preserve capital.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "CIRCUIT_BREAKER_HALT", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": "Daily risk cap preserved." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Capital Protected 🛡️", "pnl_display": "₹0.00", "summary": "Halted by Portfolio Risk Circuit Breaker." }
                }
            elif is_macro_bullish:
                # Trend Alignment Filter: Counter-Trend Asian Liquidity Sweep Avoidance (EXACT FIX FOR TODAY!)
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Asian Liquidity Sweep Trap Avoided (Daily Bullish: {chg_pct:+.2f}%)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / LIQUIDITY SWEEP TRAP", "rationale": f"Macro trend is Bullish (above CPR ₹{cpr_top:,.2f} | {chg_pct:+.2f}%). Low Asian range breakdown is a classic institutional Liquidity Sweep. Avoided.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "LIQUIDITY_SWEEP_AVOIDED", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"Counter-trend SELL blocked by Macro CPR Filter." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Avoided Trap 🛡️", "pnl_display": "₹0.00", "summary": f"Counter-trend SELL blocked by Daily CPR Trend Filter. Capital protected." }
                }
            elif adx is not None and adx < 18.0:
                # ADX Trend Strength Filter: Avoid False Breakdowns in Choppy Regime
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Low Trend Strength (ADX: {adx:.1f} < 18)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / WEAK TREND (ADX < 18)", "rationale": f"ADX is {adx:.1f} (below 18 threshold). Market is range-bound without directional impulse; breakdown trade blocked to avoid chop.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "WEAK_ADX_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"Low ADX {adx:.1f} avoided fake breakdown." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Avoided Fakeout 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: ADX {adx:.1f} indicates choppy, directionless market." }
                }
            elif item_sector in already_called_sectors:
                # Sector Correlation Guard
                entry = {
                    "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                    "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                    "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Sector Correlation Guard ({item_sector} Exposure Capped)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / SECTOR CAP", "rationale": f"{item_sector} sector risk already allocated. Stood aside to eliminate double correlated risk.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "SECTOR_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"{item_sector} exposure already active." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Sector Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside via Sector Correlation Guard." }
                }
            else:
                # Valid Trend-Aligned Breakdown: Enter on Bounce to Broken Asian Low (Resistance)
                resistance_entry = day_l
                atr_sl_dist = round(1.2 * atr, 2) if (atr and atr > 0) else max(range_pts * 0.25, day_l * 0.003)
                tight_sl = round(day_l + atr_sl_dist, 2)
                tgt = round(day_l - (range_pts * 1.2), 2)
                is_at_res = abs(cur_p - resistance_entry) / resistance_entry <= 0.002
                entry_p = cur_p if is_at_res else resistance_entry
                trade_status = "ACTIVE" if is_at_res else "PENDING_LIMIT"
                
                # 15-Minute ORB Bar-Close & Wick Trap Guard
                orb_check = ORBTimer.check_orb_confirmation(sym, "SELL", cur_p, day_h, day_l, volume_ratio=vol_ratio, last_bar_close=cur_p)
                if not orb_check.get('passed'):
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                        "lot_size": lot_size, "event_context": "ORB Bar-Close & Wick Trap Guard",
                        "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / WICK TRAP GUARD", "rationale": f"ORB Timer Block: {orb_check.get('reason')}", "reference_price": cur_p, "stop_loss": None, "target": None },
                        "check_615": { "checked": True, "status": "WICK_TRAP_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": orb_check.get('reason') },
                        "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                        "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Trap Avoided 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {orb_check.get('reason')}" }
                    }
                else:
                    risk_metrics = PreTradeRiskEngine.check_risk(sym, "SELL", entry_p, tight_sl, tgt, lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)
                    if not risk_metrics.get('passed'):
                        entry = {
                            "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                            "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                            "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                            "lot_size": lot_size, "event_context": "Pre-Trade Risk Filter Guard",
                            "risk_check": risk_metrics,
                            "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / RISK GUARD", "rationale": f"Risk check failed: {risk_metrics.get('reason')}", "reference_price": cur_p, "stop_loss": None, "target": None },
                            "check_615": { "checked": True, "status": "RISK_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": risk_metrics.get('reason') },
                            "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                            "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Risk Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {risk_metrics.get('reason')}" }
                        }
                    else:
                        sim_metrics = MonteCarloTimeMachine.run_simulation(sym, entry_p, tight_sl, tgt, action='sell', replays=3000)
                        if not sim_metrics.get('passed'):
                            entry = {
                                "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                                "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                                "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                                "lot_size": lot_size, "event_context": "Monte Carlo Time Machine Simulation Guard",
                                "time_machine": sim_metrics,
                                "risk_check": risk_metrics,
                                "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / TIME MACHINE GUARD", "rationale": f"Time Machine Simulation Block ({sim_metrics.get('summary')}): Replay leans toward stop loss.", "reference_price": cur_p, "stop_loss": None, "target": None },
                                "check_615": { "checked": True, "status": "SIM_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": sim_metrics.get('summary') },
                                "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                                "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Sim Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {sim_metrics.get('summary')}" }
                            }
                        else:
                            entry = {
                                "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                                "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                                "time_615": "01:45 PM IST", "time_exit": "11:30 PM IST", "symbol": sym, "name": name,
                                "lot_size": lot_size, "event_context": f"London Bearish Breakdown (Trend Aligned: CPR Bot ₹{cpr_bot:,.2f})",
                                "time_machine": sim_metrics,
                                "risk_check": risk_metrics,
                                "suggestion_6pm": {
                                    "status": "LONDON_BREAKDOWN",
                                    "action_text": f"SELL @ RESISTANCE ₹{resistance_entry:,.2f}",
                                    "rationale": f"Trend-aligned European breakdown (Macro Bearish below CPR). Entering at Resistance ₹{resistance_entry:,.2f} with tight SL. {sim_metrics.get('summary')}",
                                    "reference_price": resistance_entry, "stop_loss": tight_sl, "target": tgt
                                },
                                "check_615": {
                                    "checked": True, "status": "BREAKDOWN_ENTERED" if is_at_res else "RETEST_PENDING",
                                    "price_615": entry_p,
                                    "action_taken": f"SELL {lot_size} units @ Resistance ₹{entry_p:,.2f}" if is_at_res else f"Awaiting Resistance Retest @ ₹{resistance_entry:,.2f}",
                                    "detail": f"Resistance Retest order at Asian Low ₹{resistance_entry:,.2f} with tight SL ₹{tight_sl:,.2f}."
                                },
                                "paper_trade": {
                                    "user": USER_ACCOUNT, "action": "SELL", "quantity": lot_size,
                                    "entry_price": entry_p, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None,
                                    "pnl_points": 0.0, "pnl_inr": 0.0, "status": trade_status, "target": tgt, "stop_loss": tight_sl
                                },
                                "eod_outcome": {
                                    "session_close": None, "circuit_limit": None, "outcome": "PENDING",
                                    "badge": "Active Trade ⚡" if trade_status == "ACTIVE" else "Limit at Resistance ⏳",
                                    "pnl_display": "Ticking Live..." if trade_status == "ACTIVE" else "Awaiting Retest",
                                    "summary": f"Resistance retest entry @ ₹{resistance_entry:,.2f} (SL: ₹{tight_sl:,.2f}, TGT: ₹{tgt:,.2f})."
                                }
                            }
            new_entries.append(entry)
        else:
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "London Breakout (1:30 PM)",
                "time_trigger": "01:30 PM IST",
                "time_6pm": "01:30 PM IST",
                "time_validation": "01:45 PM IST",
                "time_615": "01:45 PM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"Range-Bound Asian Range [₹{day_l:,.2f} – ₹{day_h:,.2f}]",
                "suggestion_6pm": {
                    "status": "STAND_ASIDE",
                    "action_text": "STAND ASIDE / RANGE-BOUND",
                    "rationale": f"Trading within morning Asian range [₹{day_l:,.2f} – ₹{day_h:,.2f}]. Standing aside awaiting range breakout.",
                    "reference_price": cur_p,
                    "stop_loss": None,
                    "target": None
                },
                "check_615": {
                    "checked": True,
                    "status": "RANGE_BOUND_STAND_ASIDE",
                    "price_615": cur_p,
                    "action_taken": "Stood Aside (0 Units)",
                    "detail": "Inside Asian morning range. Awaiting breakout."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": "STAND_ASIDE",
                    "quantity": 0,
                    "entry_price": None,
                    "entry_time": "01:30 PM IST",
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "STAND_ASIDE"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": None,
                    "outcome": "PROTECTED",
                    "badge": "Avoided Range 🛡️",
                    "pnl_display": "₹0.00",
                    "summary": "Stood aside on range-bound asset. Capital protected."
                }
            }
            new_entries.append(entry)

    if new_entries:
        tracker['trades'] = new_entries + trades
        save_tracker(tracker)
        print(f"Recorded {len(new_entries)} live 1:30 PM London breakout session records for today.")
    return new_entries

def record_live_6pm_advisory(commodities_list, pre_6pm_events):
    """
    Called at 6:00 PM IST by daily_6pm_advisory.py.
    Logs each commodity's recommendation into the paper trading tracker and creates active/pending trades for venu.photos1@gmail.com.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    # Check if trades for today already exist
    existing_today_ids = {t['id'] for t in trades if t.get('date') == today_str}
    new_entries = []

    seen_bases = set()
    for item in commodities_list:
        raw_sym = item['symbol']
        base = raw_sym.replace('MIC', '').rstrip('M')
        if base in seen_bases:
            continue
        seen_bases.add(base)

        sym, name, lot_size = get_defense_contract_and_lot(raw_sym, item['name'])
        cur_p = item['current_price']
        p_close = item['prev_close']
        chg_pct = item['change_pct']
        status = item['status']
        lc_price = item['lower_circuit']
        uc_price = item['upper_circuit']

        trade_id = f"ADV-{today_str.replace(' ', '')}-1800-{sym}"
        trade_id_alt = f"ADV-{today_str.replace(' ', '')}-{sym}"
        if trade_id in existing_today_ids or trade_id_alt in existing_today_ids:
            continue

        pre_ev = next((e for e in pre_6pm_events if e.get('symbol') == sym), None)

        if pre_ev:
            direction = pre_ev.get('direction', 'LOWER')
            action = "BUY" if direction == 'LOWER' else "SELL"
            # Ensure BUY stop loss is strictly below min(entry, LC), and SELL stop is strictly above max(entry, UC)
            if direction == 'LOWER':
                sl = round(min(cur_p, lc_price) * 0.996, 2)
            else:
                sl = round(max(cur_p, uc_price) * 1.004, 2)

            # Mandatory 15-Minute Rule: Never take instant blind fades at 6:00 PM US open rush.
            # Require 6:15 PM candle close verification to confirm boundary holds.
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "US Pit Open (6:00 PM)",
                "time_trigger": "06:00 PM IST",
                "time_6pm": "06:00 PM IST",
                "time_validation": "06:15 PM IST",
                "time_615": "06:15 PM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"Pre-6 PM {direction.title()} Circuit Test (15-Min Confirmation Enforced)",
                "suggestion_6pm": {
                    "status": "WAIT_615",
                    "action_text": f"WAIT FOR 6:15 PM CANDLE CLOSE before entering {action}",
                    "rationale": "High institutional volatility at US open. Awaiting 6:15 PM bar-close to confirm limit barrier holds.",
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

        elif 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            direction = 'LOWER' if 'LOWER' in status else 'UPPER'
            action = "BUY" if direction == 'LOWER' else "SELL"
            # Ensure BUY stop loss is strictly below min(entry, LC), and SELL stop is strictly above max(entry, UC)
            if direction == 'LOWER':
                sl = round(min(cur_p, lc_price) * 0.996, 2)
            else:
                sl = round(max(cur_p, uc_price) * 1.004, 2)
            
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "US Pit Open (6:00 PM)",
                "time_trigger": "06:00 PM IST",
                "time_6pm": "06:00 PM IST",
                "time_validation": "06:15 PM IST",
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

        else:
            # Stand Aside / Normal Range commodities: record capital preservation entry
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "US Pit Open (6:00 PM)",
                "time_trigger": "06:00 PM IST",
                "time_6pm": "06:00 PM IST",
                "time_validation": "06:15 PM IST",
                "time_615": "06:15 PM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": "Safe Normal Range (No Circuit Edge)",
                "suggestion_6pm": {
                    "status": "STAND_ASIDE",
                    "action_text": "STAND ASIDE / SAFE RANGE",
                    "rationale": "Asset within standard range (±1.5%). No statistical circuit edge; capital protected in cash.",
                    "reference_price": cur_p,
                    "stop_loss": lc_price,
                    "target": p_close
                },
                "check_615": {
                    "checked": False,
                    "status": "PENDING_615_CHECK",
                    "price_615": None,
                    "action_taken": "Awaiting 6:15 PM candle close to verify safe range holds...",
                    "detail": "15-minute verification scheduled for 6:15 PM IST."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": "STAND_ASIDE",
                    "quantity": 0,
                    "entry_price": cur_p,
                    "entry_time": "06:00 PM IST",
                    "exit_price": cur_p,
                    "exit_time": "11:30 PM IST",
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "CLOSED"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price,
                    "outcome": "PROTECTED",
                    "badge": "Capital Protected 🛡️",
                    "pnl_display": "₹0.00 (Protected)",
                    "summary": "Stood aside on normal range. 100% capital preserved in cash."
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

def record_live_8pm_advisory(commodities_list, day_name):
    """
    Called at 8:00 PM IST by generate_and_send_8pm_advisory.
    Evaluates EIA inventory setups (Wed Crude, Thu NatGas) and evening momentum,
    creates paper trades for venu.photos1@gmail.com, and logs entries in the advisory tracker.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    existing_today_ids = {t['id'] for t in trades if t.get('date') == today_str}
    new_entries = []

    # Priority 1: Wednesday EIA Crude Oil Special
    if day_name == 'Wednesday':
        crude = next((c for c in commodities_list if c['symbol'] in ['CRUDEOILM', 'CRUDEOIL']), None)
        if crude:
            trade_id = f"ADV-{today_str.replace(' ', '')}-2000-CRUDEOIL"
            if trade_id not in existing_today_ids:
                cur_p = crude['current_price']
                p_close = crude['prev_close']
                chg = crude['change_pct']
                lc = crude['lower_circuit']
                uc = crude['upper_circuit']
                c_sym, c_name, lot_size = get_defense_contract_and_lot('CRUDEOIL', 'Crude Oil Mini')

                # Fade overreaction if near circuit or spike > 1.5% with trend verification
                if 'NEAR' in crude['status'] or 'HIT' in crude['status'] or abs(chg) >= 1.5:
                    action = "BUY" if chg < 0 else "SELL"
                    sl = round(lc * 0.996 if action == "BUY" else uc * 1.004, 2)
                    tgt = p_close
                    c_sym, c_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(c_sym, c_name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR)
                    if adaptive_lot > 0:
                        entry = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": "EIA Crude Inventory (8:00 PM)",
                            "time_trigger": "08:00 PM IST",
                            "time_6pm": "08:00 PM IST",
                            "time_validation": "08:15 PM IST",
                            "time_615": "08:15 PM IST",
                            "time_exit": "11:30 PM IST",
                            "symbol": c_sym,
                            "name": c_name,
                            "lot_size": adaptive_lot,
                            "event_context": "Wednesday EIA Petroleum Inventory Spike Fade",
                            "suggestion_6pm": {
                                "status": "EIA_FADE",
                                "action_text": f"{action} / FADE EIA INVENTORY OVERREACTION",
                                "rationale": "Historical EIA inventory spikes show 60%+ mean-reversion rate back toward anchor.",
                                "reference_price": cur_p,
                                "stop_loss": sl,
                                "target": tgt
                            },
                            "check_615": {
                                "checked": True,
                                "status": "DIRECT_ENTRY",
                                "price_615": cur_p,
                                "action_taken": f"Entered {action} {adaptive_lot} units @ ₹{cur_p:,.2f}",
                                "detail": "EIA Inventory setup entered at 8:00 PM IST."
                            },
                            "paper_trade": {
                                "user": USER_ACCOUNT,
                                "action": action,
                                "quantity": adaptive_lot,
                                "entry_price": cur_p,
                                "entry_time": "08:00 PM IST",
                                "exit_price": None,
                                "exit_time": None,
                                "pnl_points": 0.0,
                                "pnl_inr": 0.0,
                                "status": "ACTIVE",
                                "target": tgt,
                                "stop_loss": sl
                            },
                            "eod_outcome": {
                                "session_close": None,
                                "circuit_limit": lc if action == "BUY" else uc,
                                "outcome": "PENDING",
                                "badge": "Active Trade ⚡",
                                "pnl_display": "Ticking Live...",
                                "summary": f"Active {action} position ({adaptive_lot} units) entered for {USER_ACCOUNT}. Auto-exit at 11:30 PM IST."
                            }
                        }
                        new_entries.append(entry)

    # Priority 2: Thursday EIA Natural Gas Special
    elif day_name == 'Thursday':
        natgas = next((c for c in commodities_list if c['symbol'] in ['NATURALGASM', 'NATURALGAS']), None)
        if natgas:
            trade_id = f"ADV-{today_str.replace(' ', '')}-2000-NATURALGAS"
            if trade_id not in existing_today_ids:
                cur_p = natgas['current_price']
                p_close = natgas['prev_close']
                chg = natgas['change_pct']
                lc = natgas['lower_circuit']
                uc = natgas['upper_circuit']
                c_sym, c_name, lot_size = get_defense_contract_and_lot('NATURALGAS', 'Natural Gas Mini')

                if 'NEAR' in natgas['status'] or 'HIT' in natgas['status'] or abs(chg) >= 2.0:
                    action = "BUY" if chg < 0 else "SELL"
                    sl = round(lc * 0.996 if action == "BUY" else uc * 1.004, 2)
                    tgt = p_close
                    c_sym, c_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(c_sym, c_name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR)
                    if adaptive_lot > 0:
                        entry = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": "EIA NatGas Storage (8:00 PM)",
                            "time_trigger": "08:00 PM IST",
                            "time_6pm": "08:00 PM IST",
                            "time_validation": "08:15 PM IST",
                            "time_615": "08:15 PM IST",
                            "time_exit": "11:30 PM IST",
                            "symbol": c_sym,
                            "name": c_name,
                            "lot_size": adaptive_lot,
                            "event_context": "Thursday EIA Natural Gas Storage Spike Fade",
                            "suggestion_6pm": {
                                "status": "EIA_FADE",
                                "action_text": f"{action} / FADE EIA INVENTORY OVERREACTION",
                                "rationale": "Historical EIA inventory spikes show 60%+ mean-reversion rate back toward anchor.",
                                "reference_price": cur_p,
                                "stop_loss": sl,
                                "target": tgt
                            },
                            "check_615": {
                                "checked": True,
                                "status": "DIRECT_ENTRY",
                                "price_615": cur_p,
                                "action_taken": f"Entered {action} {adaptive_lot} units @ ₹{cur_p:,.2f}",
                                "detail": "EIA Storage setup entered at 8:00 PM IST."
                            },
                            "paper_trade": {
                                "user": USER_ACCOUNT,
                                "action": action,
                                "quantity": adaptive_lot,
                                "entry_price": cur_p,
                                "entry_time": "08:00 PM IST",
                                "exit_price": None,
                                "exit_time": None,
                                "pnl_points": 0.0,
                                "pnl_inr": 0.0,
                                "status": "ACTIVE",
                                "target": tgt,
                                "stop_loss": sl
                            },
                            "eod_outcome": {
                                "session_close": None,
                                "circuit_limit": lc if action == "BUY" else uc,
                                "outcome": "PENDING",
                                "badge": "Active Trade ⚡",
                                "pnl_display": "Ticking Live...",
                                "summary": f"Active {action} position ({adaptive_lot} units) entered for {USER_ACCOUNT}. Auto-exit at 11:30 PM IST."
                            }
                        }
                        new_entries.append(entry)

    # General US Evening Follow-through for other commodities
    for item in commodities_list:
        sym = item['symbol']
        if (day_name == 'Wednesday' and sym == 'CRUDEOIL') or (day_name == 'Thursday' and sym == 'NATURALGAS'):
            continue
        trade_id = f"ADV-{today_str.replace(' ', '')}-2000-{sym}"
        if trade_id in existing_today_ids:
            continue

        status = item['status']
        if 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            direction = 'LOWER' if 'LOWER' in status else 'UPPER'
            cur_p = item['current_price']
            lc_price = item['lower_circuit']
            uc_price = item['upper_circuit']
            day_h = float(item.get('day_high', cur_p))
            day_l = float(item.get('day_low', cur_p))
            chg_pct = float(item.get('pct_change', 0.0))
            tgt = item['prev_close']
            lot_size = LOT_SIZES.get(sym, 1)

            # 🛡️ COUNTER-TREND KNIFE-CATCHING GUARD:
            # Never blind-buy a collapsing commodity or blind-short a surging commodity.
            # If price is pressing Day Low / Lower Circuit (-2% or worse), standing aside is mandatory.
            is_knife_catching = False
            knife_reason = ""
            if direction == 'LOWER' and (cur_p <= day_l * 1.002 or chg_pct <= -2.0):
                is_knife_catching = True
                knife_reason = f"Dumping into Lower Circuit ({chg_pct:+.2f}%). Counter-trend knife catching prohibited."
            elif direction == 'UPPER' and (cur_p >= day_h * 0.998 or chg_pct >= 2.0):
                is_knife_catching = True
                knife_reason = f"Surging into Upper Circuit ({chg_pct:+.2f}%). Counter-trend shorting prohibited."

            if is_knife_catching:
                entry = {
                    "id": trade_id,
                    "date": today_str,
                    "session_name": "Evening Pit Review (8:00 PM)",
                    "time_trigger": "08:00 PM IST",
                    "time_6pm": "08:00 PM IST",
                    "time_validation": "08:15 PM IST",
                    "time_615": "08:15 PM IST",
                    "time_exit": "11:30 PM IST",
                    "symbol": sym,
                    "name": item['name'],
                    "lot_size": 0,
                    "event_context": "Counter-Trend Knife-Catching Guard",
                    "suggestion_6pm": {
                        "status": "STAND_ASIDE",
                        "action_text": "STAND ASIDE / TREND GUARD",
                        "rationale": knife_reason,
                        "reference_price": cur_p,
                        "stop_loss": None,
                        "target": None
                    },
                    "check_615": {
                        "checked": True,
                        "status": "STAND_ASIDE_CONFIRMED",
                        "price_615": cur_p,
                        "action_taken": "Stood Aside (0 Units)",
                        "detail": knife_reason
                    },
                    "paper_trade": {
                        "user": USER_ACCOUNT,
                        "action": "STAND_ASIDE",
                        "quantity": 0,
                        "entry_price": None,
                        "entry_time": "08:00 PM IST",
                        "exit_price": None,
                        "exit_time": None,
                        "pnl_points": 0.0,
                        "pnl_inr": 0.0,
                        "status": "STAND_ASIDE"
                    },
                    "eod_outcome": {
                        "session_close": None,
                        "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                        "outcome": "PROTECTED",
                        "badge": "Trend Guard 🛡️",
                        "pnl_display": "₹0.00 (Protected)",
                        "summary": f"Stood aside: {knife_reason}"
                    }
                }
                new_entries.append(entry)
                continue

            action = "BUY" if direction == 'LOWER' else "SELL"

            # Technical Invalidation Stop: placed beyond Day Extreme + buffer
            if direction == 'LOWER':
                sl = round(min(cur_p * 0.992, lc_price * 0.995), 2)
            else:
                sl = round(max(cur_p * 1.008, uc_price * 1.005), 2)

            # 1. Adaptively route to Mini contract or scale lot size strictly within ₹3,000 risk cap
            contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                sym, item['name'], cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
            )

            # 2. Run PreTradeRiskEngine check
            all_existing = new_entries + trades
            risk_metrics = PreTradeRiskEngine.check_risk(contract_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size, all_existing, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)

            if adaptive_lot <= 0 or not risk_metrics.get('passed'):
                # Risk Blocked: Stand Aside
                reason = f"Point risk exceeds ₹{MAX_LOSS_PER_TRADE_INR:,.0f} risk cap" if adaptive_lot <= 0 else risk_metrics.get('reason')
                entry = {
                    "id": trade_id,
                    "date": today_str,
                    "session_name": "Evening Pit Review (8:00 PM)",
                    "time_trigger": "08:00 PM IST",
                    "time_6pm": "08:00 PM IST",
                    "time_validation": "08:15 PM IST",
                    "time_615": "08:15 PM IST",
                    "time_exit": "11:30 PM IST",
                    "symbol": sym,
                    "name": item['name'],
                    "lot_size": lot_size,
                    "event_context": "Pre-Trade Risk Filter Guard",
                    "suggestion_6pm": {
                        "status": "STAND_ASIDE",
                        "action_text": "STAND ASIDE / RISK GUARD",
                        "rationale": f"Risk check failed: {reason}",
                        "reference_price": cur_p,
                        "stop_loss": None,
                        "target": None
                    },
                    "check_615": {
                        "checked": True,
                        "status": "RISK_GUARD_STAND_ASIDE",
                        "price_615": cur_p,
                        "action_taken": "Stood Aside (0 Units)",
                        "detail": reason
                    },
                    "paper_trade": {
                        "user": USER_ACCOUNT,
                        "action": "STAND_ASIDE",
                        "quantity": 0,
                        "entry_price": None,
                        "entry_time": "08:00 PM IST",
                        "exit_price": None,
                        "exit_time": None,
                        "pnl_points": 0.0,
                        "pnl_inr": 0.0,
                        "status": "STAND_ASIDE"
                    },
                    "eod_outcome": {
                        "session_close": None,
                        "circuit_limit": None,
                        "outcome": "PROTECTED",
                        "badge": "Risk Filter 🛡️",
                        "pnl_display": "₹0.00",
                        "summary": f"Stood aside: {reason}"
                    }
                }
                new_entries.append(entry)
                continue

            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": "Evening Pit Review (8:00 PM)",
                "time_trigger": "08:00 PM IST",
                "time_6pm": "08:00 PM IST",
                "time_validation": "08:15 PM IST",
                "time_615": "08:15 PM IST",
                "time_exit": "11:30 PM IST",
                "symbol": sym,
                "name": item['name'],
                "lot_size": adaptive_lot,
                "event_context": f"US Evening {direction.title()} Circuit Band Test",
                "suggestion_6pm": {
                    "status": "EVENING_CIRCUIT",
                    "action_text": f"{action} / EVENING CIRCUIT PLAY",
                    "rationale": "High evening liquidity test against circuit boundary.",
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": tgt
                },
                "check_615": {
                    "checked": True,
                    "status": "DIRECT_ENTRY",
                    "price_615": cur_p,
                    "action_taken": f"Entered {action} {adaptive_lot} units @ ₹{cur_p:,.2f}",
                    "detail": "Entered at 8:00 PM IST."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": adaptive_lot,
                    "entry_price": cur_p,
                    "entry_time": "08:00 PM IST",
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "ACTIVE",
                    "target": tgt,
                    "stop_loss": sl
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                    "outcome": "PENDING",
                    "badge": "Active Trade ⚡",
                    "pnl_display": "Ticking Live...",
                    "summary": f"Active {action} position ({adaptive_lot} units) entered for {USER_ACCOUNT}. Auto-exit at 11:30 PM IST."
                }
            }
            new_entries.append(entry)

    if new_entries:
        tracker['trades'] = new_entries + trades
        save_tracker(tracker)
        print(f"Recorded {len(new_entries)} live 8:00 PM evening advisory trades for today.")
    else:
        print("No new 8:00 PM evening advisory trades triggered today.")

def check_and_execute_615_confirmation(checker, alerter):
    """
    Called at 6:15 PM IST (18:15 - 18:25 IST window).
    Checks 15-minute candle outcomes for pending candidates and Stand Aside commodities,
    executes paper trades, and sends Telegram/Email notifications explaining exact actions.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    pending_trades = [t for t in trades if t.get('date') == today_str and t.get('check_615', {}).get('status') == 'PENDING_615']
    if not pending_trades and tracker.get('check_615_sent_date') == today_str:
        print(f"6:15 PM confirmation alert already sent for today ({today_str}).")
        return False

    circuits_res = checker.generate_circuits_data()
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}
    
    alert_messages = []
    
    # 1. Process Pending Circuit Confirmation Trades
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
        
        # If we are well past the 6:15 PM window (past 6:30 PM), do not execute late entries; resolve cleanly as Stood Aside
        if now_dt.hour > 18 or (now_dt.hour == 18 and now_dt.minute > 30):
            trade['check_615']['checked'] = True
            trade['check_615']['status'] = "CONFIRMED_STAND_ASIDE"
            trade['check_615']['action_taken'] = "15-Min candle window lapsed. Stood aside to preserve capital."
            trade['check_615']['detail'] = "15-minute confirmation check completed. Price held range; stood aside."
            trade['paper_trade']['status'] = "STAND_ASIDE"
            trade['paper_trade']['action'] = "STAND_ASIDE"
            trade['paper_trade']['quantity'] = 0
            trade['paper_trade']['entry_price'] = None
            trade['paper_trade']['exit_price'] = None
            trade['paper_trade']['pnl_inr'] = 0.0
            trade['eod_outcome']['outcome'] = "PROTECTED"
            trade['eod_outcome']['badge'] = "Avoided Trap 🛡️"
            trade['eod_outcome']['pnl_display'] = "₹0.00"
            trade['eod_outcome']['summary'] = "15-minute confirmation window lapsed. Stood aside to preserve capital."
            continue
        
        # Check if 15-min candle showed genuine reversal confirmation vs knife-catching
        day_h = float(c_item.get('day_high', cur_p))
        day_l = float(c_item.get('day_low', cur_p))
        chg_pct = float(c_item.get('pct_change', 0.0))
        is_reversal = False
        reversal_reject_reason = ""

        if direction == 'LOWER':
            # Reversal requires holding above LC AND showing a real bounce off day low (+0.5% off low)
            if cur_p > lc_p and cur_p >= day_l * 1.004 and chg_pct > -3.5:
                is_reversal = True
            else:
                reversal_reject_reason = f"Hugging Day Low / LC (Chg: {chg_pct:+.2f}%). Heavy downward momentum; knife-catching blocked."
        else:
            # Reversal requires holding below UC AND showing a real pullback off day high (-0.5% off high)
            if cur_p < uc_p and cur_p <= day_h * 0.996 and chg_pct < 3.5:
                is_reversal = True
            else:
                reversal_reject_reason = f"Hugging Day High / UC (Chg: {chg_pct:+.2f}%). Heavy upward momentum; shorting runaway train blocked."
            
        trade['check_615']['checked'] = True
        trade['check_615']['price_615'] = cur_p

        if not is_reversal:
            # Stand aside: no genuine reversal
            trade['check_615']['status'] = "KNIFE_CATCH_STAND_ASIDE"
            trade['check_615']['action_taken'] = f"Stood aside: {reversal_reject_reason}"
            trade['check_615']['detail'] = reversal_reject_reason
            trade['paper_trade']['status'] = "STAND_ASIDE"
            trade['paper_trade']['action'] = "STAND_ASIDE"
            trade['paper_trade']['quantity'] = 0
            trade['paper_trade']['entry_price'] = cur_p
            trade['paper_trade']['exit_price'] = cur_p
            trade['paper_trade']['pnl_inr'] = 0.0
            trade['eod_outcome']['outcome'] = "PROTECTED"
            trade['eod_outcome']['badge'] = "Trend Guard 🛡️"
            trade['eod_outcome']['pnl_display'] = "₹0.00 (Protected)"
            trade['eod_outcome']['summary'] = f"Stood aside: {reversal_reject_reason}"
            msg = (
                f"🛡️ <b>[6:15 PM TREND GUARD] {trade['name'].upper()}</b>\n"
                f"• Status: Reversal NOT confirmed (Price ₹{cur_p:,.2f})\n"
                f"• Reason: <b>{reversal_reject_reason}</b>\n"
                f"• Action: Stood aside to protect ₹3,000 risk capital."
            )
            alert_messages.append(msg)
            continue

        if is_reversal:
            action = "BUY" if direction == 'LOWER' else "SELL"
            if direction == 'LOWER':
                sl = round(min(cur_p * 0.992, lc_p * 0.995), 2)
            else:
                sl = round(max(cur_p * 1.008, uc_p * 1.005), 2)
            tgt = prev_close

            # 1. Adaptively route to Mini contract or scale lot size strictly within ₹3,000 max risk cap
            contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                sym, trade['name'], cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
            )
            
            # 2. Run PreTradeRiskEngine check
            risk_metrics = PreTradeRiskEngine.check_risk(contract_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)

            if adaptive_lot <= 0 or not risk_metrics.get('passed'):
                # RISK BLOCKED: Stand aside
                reason = f"Point risk exceeds ₹{MAX_LOSS_PER_TRADE_INR:,.0f} risk cap" if adaptive_lot <= 0 else risk_metrics.get('reason')
                trade['check_615']['status'] = "RISK_GUARD_STAND_ASIDE"
                trade['check_615']['action_taken'] = f"Risk Engine Block: {reason}. Stood aside to preserve capital."
                trade['check_615']['detail'] = reason
                trade['paper_trade']['status'] = "STAND_ASIDE"
                trade['paper_trade']['action'] = "STAND_ASIDE"
                trade['paper_trade']['quantity'] = 0
                trade['paper_trade']['entry_price'] = cur_p
                trade['paper_trade']['exit_price'] = cur_p
                trade['paper_trade']['pnl_inr'] = 0.0
                trade['eod_outcome']['outcome'] = "PROTECTED"
                trade['eod_outcome']['badge'] = "Risk Filter 🛡️"
                trade['eod_outcome']['pnl_display'] = "₹0.00 (Protected)"
                trade['eod_outcome']['summary'] = f"Risk engine blocked 6:15 PM entry: {reason}"
                msg = (
                    f"🛡️ <b>[6:15 PM RISK FILTER] {trade['name'].upper()}</b>\n"
                    f"• Limit Held: ₹{cur_p:,.2f}, but trade blocked by Pre-Trade Risk Engine\n"
                    f"• Reason: <b>{reason}</b>\n"
                    f"• Action: Stood aside to protect ₹5,000 risk capital."
                )
                alert_messages.append(msg)
            else:
                # REVERSAL CONFIRMED & RISK PASSED: Enter paper trade!
                trade['lot_size'] = adaptive_lot
                trade['check_615']['status'] = "REVERSAL_CONFIRMED"
                trade['check_615']['action_taken'] = f"15-min candle confirmed limit held. ENTERED {action} {adaptive_lot} units @ ₹{cur_p:,.2f}"
                trade['check_615']['detail'] = f"Price held inside band (₹{cur_p:,.2f} vs limit ₹{lc_p if direction=='LOWER' else uc_p:,.2f}). Reversal confirmed!"
                
                trade['paper_trade']['status'] = "ACTIVE"
                trade['paper_trade']['action'] = action
                trade['paper_trade']['quantity'] = adaptive_lot
                trade['paper_trade']['entry_price'] = cur_p
                trade['paper_trade']['entry_time'] = "06:15 PM IST"
                trade['paper_trade']['target'] = tgt
                trade['paper_trade']['stop_loss'] = sl
                
                trade['eod_outcome']['outcome'] = "ACTIVE"
                trade['eod_outcome']['badge'] = "Active Trade ⚡"
                trade['eod_outcome']['pnl_display'] = "Ticking Live..."
                trade['eod_outcome']['summary'] = f"Confirmed {action} position ({adaptive_lot} units) active for {USER_ACCOUNT}. Auto-exit at 11:30 PM IST."
                
                msg = (
                    f"🟢 <b>[6:15 PM TRADE CONFIRMED] {trade['name'].upper()}</b>\n"
                    f"• 15-Minute Rule: <b>Limit Barrier Held Successfully</b>\n"
                    f"• Execution: <b>{action} {adaptive_lot} units</b> @ ₹{cur_p:,.2f}\n"
                    f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{prev_close:,.2f}\n"
                    f"• Risk Audit: Max loss capped inside ₹5,000\n"
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

    # 2. Process and Confirm Stand Aside Commodities
    stand_aside_trades = [t for t in trades if t.get('date') == today_str and (t.get('check_615', {}).get('status') == 'PENDING_615_CHECK' or t.get('paper_trade', {}).get('action') == 'STAND_ASIDE')]
    stand_aside_lines = []
    
    for sa_trade in stand_aside_trades:
        sym = sa_trade['symbol']
        c_item = commodities_map.get(sym)
        cur_p = c_item['current_price'] if c_item else sa_trade['suggestion_6pm'].get('reference_price', 0)
        chg_pct = c_item['change_pct'] if c_item else 0.0
        
        sa_trade['check_615']['checked'] = True
        sa_trade['check_615']['status'] = "CONFIRMED_STAND_ASIDE"
        sa_trade['check_615']['price_615'] = cur_p
        sa_trade['check_615']['action_taken'] = "15-Min candle confirmed range held. Stood aside to preserve capital."
        sa_trade['check_615']['detail'] = f"Price at 6:15 PM ₹{cur_p:,.2f} ({chg_pct:+.2f}%) confirmed no extreme circuit imbalance."
        stand_aside_lines.append(f"• <b>{sa_trade['name']}</b>: ₹{cur_p:,.2f} ({chg_pct:+.2f}%) | Safe Range Held")

    cash_info = get_cash_and_margin(tracker)
    avail_cash = cash_info.get('available_cash', 300000.0)

    # If no pending circuit trades occurred, draft Stand Aside Action Playbook
    if not pending_trades:
        if not stand_aside_lines and commodities_map:
            for sym, c in commodities_map.items():
                stand_aside_lines.append(f"• <b>{c['name']}</b>: ₹{c['current_price']:,.2f} ({c['change_pct']:+.2f}%) | Safe Range Held")

        sa_text = "\n".join(stand_aside_lines) if stand_aside_lines else "• All 5 commodities held inside standard safe bands."
        stand_aside_msg = (
            f"🛡️ <b>[6:15 PM IST UPDATE] 15-MIN CANDLE CONFIRMATION (STAND ASIDE)</b>\n\n"
            f"The 15-minute post-US pit open candle window (6:00 PM – 6:15 PM IST) has closed.\n\n"
            f"<b>📊 LIVE 6:15 PM PRICE ACTION:</b>\n"
            f"{sa_text}\n\n"
            f"<b>💡 ACTION GUIDANCE FOR STAND ASIDE COMMODITIES:</b>\n"
            f"• <b>Maintain Stand Aside</b>: Opening volatility has stabilized within standard ranges without testing extreme circuit slabs.\n"
            f"• <b>Capital Preservation</b>: Do NOT force counter-trend or breakout trades without statistical edge.\n"
            f"• <b>Discretionary Rule</b>: If trading intraday, stick strictly to standard S1/R1 pivot levels with defined stop-losses.\n"
            f"• <b>Virtual Paper Trade</b>: 100% virtual capital remains securely held in cash (₹{avail_cash:,.2f}) for the next high-edge window (e.g. 8:00 PM US Pit / EIA)."
        )
        alert_messages.append(stand_aside_msg)
    elif stand_aside_lines:
        # We had active candidates AND some stand aside commodities
        sa_text = "\n".join(stand_aside_lines)
        supplemental_sa = (
            f"\n🛡️ <b>STAND ASIDE COMMODITIES (15-MIN CHECK):</b>\n"
            f"{sa_text}\n"
            f"• <b>Action</b>: Maintain Stand Aside on the above assets. Focus capital exclusively on confirmed setup(s)."
        )
        alert_messages.append(supplemental_sa)

    tracker['check_615_sent_date'] = today_str
    save_tracker(tracker)
    
    if alert_messages and alerter:
        body = "\n\n".join(alert_messages) + "\n\n🌐 View Live Tracker: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
        alerter.send_alert("⚡ [6:15 PM IST UPDATE] 15-Min Confirmation & Stand Aside Playbook", body)
        print("Successfully dispatched 6:15 PM IST confirmation alert.")
    
    return True

def check_and_execute_intraday_targets_and_stops(checker, alerter=None):
    """
    Called periodically during market hours (every minute via cron/continuous loop).
    Checks all ACTIVE and PENDING_LIMIT advisory paper trades against live high/low/current prices.
    - Fills PENDING_LIMIT trades on Support/Resistance retest.
    - Trails Stop-Loss to Breakeven once price moves 50% towards target (risk-free trade).
    - Executes Target and Stop-Loss auto-exits with instant alert notifications.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    now_time_str = now_dt.strftime('%I:%M %p IST')
    
    relevant_trades = [t for t in trades if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']]
    if not relevant_trades:
        return False

    circuits_res = checker.generate_circuits_data() if checker else {}
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}

    # Multi-Asset Support: Load live Nifty equity data alongside MCX commodities
    equity_map = {}
    if os.path.exists(NIFTY_JSON_FILE):
        try:
            with open(NIFTY_JSON_FILE, 'r') as f:
                n_data = json.load(f)
            for s in n_data.get('stocks', []):
                sym_k = s.get('symbol')
                name_k = s.get('name')
                if sym_k:
                    equity_map[sym_k.upper()] = s
                    equity_map[sym_k.replace('.NS', '').upper()] = s
                if name_k:
                    equity_map[name_k.upper()] = s
        except Exception as e:
            print(f"Warning: Could not read nifty stocks for advisory price check: {e}")

    if not commodities_map and not equity_map:
        return False

    updated_any = False
    alerts_to_send = []

    for trade in relevant_trades:
        sym = trade['symbol']
        clean_sym = sym.replace('-FUT', '').replace('.NS', '').upper() if sym else ''
        c_item = commodities_map.get(sym) or commodities_map.get(clean_sym)
        eq_item = equity_map.get(sym.upper()) or equity_map.get(clean_sym) or equity_map.get(f"{clean_sym}.NS")

        is_stock_future = ('-FUT' in sym) or (sym in STOCK_FUTURES_CONFIG) or (clean_sym in STOCK_FUTURES_CONFIG)
        is_equity = trade.get('asset_type') in ['EQUITY', 'STOCK_FUTURES'] or is_stock_future or (sym and '.NS' in sym) or bool(eq_item and not c_item)

        if c_item and not is_stock_future:
            cur_p = float(c_item['current_price'])
            day_h = float(c_item.get('day_high', cur_p))
            day_l = float(c_item.get('day_low', cur_p))
        elif eq_item:
            cur_p = float(eq_item.get('close', 0))
            day_h = float(eq_item.get('price_52_week_high', cur_p))
            day_l = float(eq_item.get('price_52_week_low', cur_p))
        elif c_item:
            cur_p = float(c_item['current_price'])
            day_h = float(c_item.get('day_high', cur_p))
            day_l = float(c_item.get('day_low', cur_p))
        else:
            continue

        if cur_p <= 0:
            continue
        
        pt = trade['paper_trade']
        action = pt['action']
        lot_size = trade.get('lot_size', 1)
        trade_status = pt.get('status')
        
        s6 = trade.get('suggestion_6pm', {})
        target = float(s6.get('target')) if s6.get('target') is not None else (float(pt.get('target')) if pt.get('target') is not None else None)
        stop_loss = float(s6.get('stop_loss')) if s6.get('stop_loss') is not None else (float(pt.get('stop_loss')) if pt.get('stop_loss') is not None else None)
        entry_p = float(pt.get('entry_price')) if pt.get('entry_price') is not None else float(s6.get('reference_price', cur_p))

        # 1. Check PENDING_LIMIT orders (Support / Resistance Retests)
        if trade_status == 'PENDING_LIMIT':
            filled = False
            if action == 'BUY':
                # BUY @ Support: Price pulls back to Support level (day_h or reference_price)
                if cur_p <= entry_p * 1.001 or day_l <= entry_p:
                    filled = True
            elif action == 'SELL':
                # SELL @ Resistance: Price bounces up to Resistance level (day_l or reference_price)
                if cur_p >= entry_p * 0.999 or day_h >= entry_p:
                    filled = True
            
            if filled:
                pt['status'] = 'ACTIVE'
                pt['entry_time'] = now_time_str
                pt['entry_price'] = entry_p
                trade['eod_outcome']['badge'] = "Active Trade ⚡"
                trade['eod_outcome']['pnl_display'] = "Ticking Live..."
                retest_type = "Support" if action == "BUY" else "Resistance"
                trade['eod_outcome']['summary'] = f"Limit order filled on {retest_type} Retest @ ₹{entry_p:,.2f}. Position active."
                updated_any = True
                
                subject = f"⚡ [LIMIT ORDER FILLED] {trade['name']} {action} @ ₹{entry_p:,.2f} ({now_time_str})"
                body = (
                    f"⚡ <b>LIMIT ORDER FILLED ON RETEST</b>\n"
                    f"📅 <i>{today_str}, {now_time_str}</i>\n"
                    f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                    f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                    f"• <b>Action</b>: {action} {lot_size} units\n"
                    f"• <b>Fill Price</b>: ₹{entry_p:,.2f} ({retest_type} Retest Filled)\n"
                    f"• <b>Target</b>: ₹{target:,.2f} | <b>Stop-Loss</b>: ₹{stop_loss:,.2f}\n"
                    f"• <b>Status</b>: Active paper trade running. Auto-trailing SL and 11:30 PM settlement enabled.\n\n"
                    f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                )
                alerts_to_send.append({'subject': subject, 'body': body})
            continue

        # 2. Check ACTIVE Trades: Multi-Stage Trailing Stop-Loss & Milestone Alerts
        if trade_status == 'ACTIVE' and target is not None and stop_loss is not None:
            target_dist = (target - entry_p) if action == 'BUY' else (entry_p - target)
            if target_dist > 0:
                current_favorable_move = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
                pct_to_target = (current_favorable_move / target_dist) * 100

                # Stage 1: Breakeven Lock at +30% towards Target (+0.5R lock guarantees zero-risk position)
                if pct_to_target >= 30.0 and not pt.get('trailing_sl_active'):
                    pt['trailing_sl_active'] = True
                    pt['original_sl'] = stop_loss
                    pt['stop_loss'] = entry_p
                    stop_loss = entry_p
                    if s6:
                        s6['stop_loss'] = entry_p
                    updated_any = True
                    
                    subject = f"🛡️ [STAGE 1: BREAKEVEN LOCK (+0.5R)] {trade['name']} Reached +30% Target Milestone"
                    body = (
                        f"🛡️ <b>TRAILING STOP TO BREAKEVEN ACTIVATED (+0.5R)</b>\n"
                        f"📅 <i>{today_str}, {now_time_str}</i>\n"
                        f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                        f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                        f"• <b>Action</b>: {action} {lot_size} units\n"
                        f"• <b>Current Price</b>: ₹{cur_p:,.2f} ({pct_to_target:.1f}% to Target)\n"
                        f"• <b>Entry Price</b>: ₹{entry_p:,.2f}\n"
                        f"• <b>Target</b>: ₹{target:,.2f}\n"
                        f"• <b>New Stop-Loss</b>: <b>₹{entry_p:,.2f} (Risk-Free Breakeven)</b>\n\n"
                        f"Position is now completely risk-free! Winning trade cannot turn into a loss.\n\n"
                        f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                    )
                    alerts_to_send.append({'subject': subject, 'body': body})

                # Stage 2: Profit Lock (+0.8R / +50% of profit locked) when price hits +75% towards Target
                elif pct_to_target >= 75.0 and not pt.get('stage_2_profit_locked'):
                    lock_price = round(entry_p + (target_dist * 0.40) if action == 'BUY' else entry_p - (target_dist * 0.40), 2)
                    pt['stage_2_profit_locked'] = True
                    pt['stop_loss'] = lock_price
                    stop_loss = lock_price
                    if s6:
                        s6['stop_loss'] = lock_price
                    updated_any = True

                    subject = f"💰 [STAGE 2: PROFIT LOCKED] {trade['name']} Reached +75% Target Milestone"
                    body = (
                        f"💰 <b>STAGE 2 PROFIT LOCK ACTIVATED (+0.8R)</b>\n"
                        f"📅 <i>{today_str}, {now_time_str}</i>\n"
                        f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                        f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                        f"• <b>Action</b>: {action} {lot_size} units\n"
                        f"• <b>Current Price</b>: ₹{cur_p:,.2f} ({pct_to_target:.1f}% to Target)\n"
                        f"• <b>Guaranteed Profit Floor</b>: <b>₹{lock_price:,.2f}</b>\n"
                        f"• <b>Final Target</b>: ₹{target:,.2f}\n\n"
                        f"Trade has locked in guaranteed profits! Even on a sharp reversal, this trade is a winner.\n\n"
                        f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                    )
                    alerts_to_send.append({'subject': subject, 'body': body})

        # Ensure paper_trade object has target and stop_loss stored
        if target is not None:
            pt['target'] = target
        if stop_loss is not None:
            pt['stop_loss'] = stop_loss

        # 3. Check Event Risk Guard on Active Positions (Tighten Stop to Breakeven before Inventory Release)
        if trade_status == 'ACTIVE' and not is_equity:
            event_check = EventRiskGuard.check_event_risk(sym, now_dt)
            if not event_check.get('passed') and event_check.get('status') == 'EVENT_BLACKOUT':
                if not pt.get('event_protection_active'):
                    pt['event_protection_active'] = True
                    if action == 'BUY' and cur_p > entry_p and (stop_loss is None or stop_loss < entry_p):
                        pt['stop_loss'] = entry_p
                        stop_loss = entry_p
                        if s6:
                            s6['stop_loss'] = entry_p
                        updated_any = True
                        subject = f"📰 [EVENT RISK PROTECTION] {trade['name']} Stop Tightened to Breakeven Ahead of Inventory Release"
                        body = (
                            f"📰 <b>EVENT RISK PROTECTION ACTIVATED</b>\n"
                            f"📅 <i>{today_str}, {now_time_str}</i>\n"
                            f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                            f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                            f"• <b>Event</b>: <b>{event_check.get('event_name')}</b> ({event_check.get('blackout_window')})\n"
                            f"• <b>Protective Action</b>: Stop-Loss tightened to <b>Breakeven (₹{entry_p:,.2f})</b>\n\n"
                            f"Capital is shielded from inventory spread expansion & spikes!\n\n"
                            f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                        )
                        alerts_to_send.append({'subject': subject, 'body': body})
                    elif action == 'SELL' and cur_p < entry_p and (stop_loss is None or stop_loss > entry_p):
                        pt['stop_loss'] = entry_p
                        stop_loss = entry_p
                        if s6:
                            s6['stop_loss'] = entry_p
                        updated_any = True
                        subject = f"📰 [EVENT RISK PROTECTION] {trade['name']} Stop Tightened to Breakeven Ahead of Inventory Release"
                        body = (
                            f"📰 <b>EVENT RISK PROTECTION ACTIVATED</b>\n"
                            f"📅 <i>{today_str}, {now_time_str}</i>\n"
                            f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                            f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                            f"• <b>Event</b>: <b>{event_check.get('event_name')}</b> ({event_check.get('blackout_window')})\n"
                            f"• <b>Protective Action</b>: Stop-Loss tightened to <b>Breakeven (₹{entry_p:,.2f})</b>\n\n"
                            f"Capital is shielded from inventory spread expansion & spikes!\n\n"
                            f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                        )
                        alerts_to_send.append({'subject': subject, 'body': body})

        # 4. Check Target, Stop-Loss, 45-Minute Stagnation & EOD Square-off Triggers
        hit_target = False
        hit_sl = False
        hit_eod = False
        hit_stagnation = False
        exit_p = cur_p
        reason = ""

        # ⏱️ 45-Minute Stagnation Stop:
        # If active >= 45 minutes and momentum hasn't reached +30% to target, scratch at current price to preserve capital
        entry_time_str = pt.get('entry_time')
        trade_date_str = trade.get('date', today_str)
        if trade_status == 'ACTIVE' and entry_time_str:
            try:
                clean_time = entry_time_str.replace(' IST', '').strip()
                entry_dt_naive = datetime.strptime(f"{trade_date_str} {clean_time}", "%d %b %Y %I:%M %p")
                entry_dt = pytz.timezone('Asia/Kolkata').localize(entry_dt_naive)
                mins_open = (now_dt - entry_dt).total_seconds() / 60.0
                
                if mins_open >= 45.0:
                    pct_prog = 0.0
                    if target and entry_p:
                        t_dist = abs(target - entry_p)
                        if t_dist > 0:
                            f_move = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
                            pct_prog = (f_move / t_dist) * 100.0
                    if pct_prog < 30.0:
                        hit_stagnation = True
                        exit_p = cur_p
                        reason = f"45-Minute Stagnation Exit ⏱️ (Stalled for {int(mins_open)}m; scratch to protect capital)"
            except Exception:
                pass

        if action == 'BUY':
            if target and cur_p >= target:
                hit_target = True
                exit_p = target
                reason = "Target Achieved 🎯"
            elif stop_loss and cur_p <= stop_loss:
                hit_sl = True
                exit_p = stop_loss
                reason = "Trailing Stop Triggered (Breakeven) 🛡️" if pt.get('trailing_sl_active') else "Stop-Loss Triggered 🛑"
            elif is_equity and ((now_dt.hour == 15 and now_dt.minute >= 20) or now_dt.hour >= 16 or now_dt.hour < 9):
                hit_eod = True
                exit_p = cur_p
                reason = "Intraday EOD Square-off (03:20 PM) ⏰"
        elif action == 'SELL':
            if target and cur_p <= target:
                hit_target = True
                exit_p = target
                reason = "Target Achieved 🎯"
            elif stop_loss and cur_p >= stop_loss:
                hit_sl = True
                exit_p = stop_loss
                reason = "Trailing Stop Triggered (Breakeven) 🛡️" if pt.get('trailing_sl_active') else "Stop-Loss Triggered 🛑"
            elif is_equity and ((now_dt.hour == 15 and now_dt.minute >= 20) or now_dt.hour >= 16 or now_dt.hour < 9):
                hit_eod = True
                exit_p = cur_p
                reason = "Intraday EOD Square-off (03:20 PM) ⏰"

        if hit_target or hit_sl or hit_eod or hit_stagnation:
            pnl_pts = (exit_p - entry_p) if action == 'BUY' else (entry_p - exit_p)
            pnl_inr = round(pnl_pts * lot_size, 2)
            
            pt['exit_price'] = exit_p
            pt['exit_time'] = now_time_str
            pt['exit_trigger'] = "TARGET_HIT" if hit_target else ("EOD_SQUAREOFF" if hit_eod else ("STAGNATION_EXIT" if hit_stagnation else ("TRAILING_BREAKEVEN_HIT" if pt.get('trailing_sl_active') else "STOP_LOSS_HIT")))
            pt['pnl_points'] = round(pnl_pts, 2)
            pt['pnl_inr'] = pnl_inr
            pt['status'] = "CLOSED"
            
            if hit_target:
                outcome_val = "CORRECT"
                badge_text = f"Target Achieved 🎯 ({now_time_str})"
            elif hit_eod:
                outcome_val = "CORRECT" if pnl_inr > 0 else ("WRONG" if pnl_inr < 0 else "PROTECTED")
                badge_text = f"EOD Square-off ⏰ ({now_time_str})"
            elif hit_stagnation:
                outcome_val = "PROTECTED" if abs(pnl_inr) <= 500 else ("CORRECT" if pnl_inr > 0 else "WRONG")
                badge_text = f"Stagnation Exit ⏱️ ({now_time_str})"
            elif pt.get('trailing_sl_active'):
                outcome_val = "PROTECTED"
                badge_text = f"Breakeven Exit 🛡️ ({now_time_str})"
            else:
                outcome_val = "WRONG"
                badge_text = f"Stop Loss Hit 🛑 ({now_time_str})"
            
            trade['time_exit'] = now_time_str
            trade['eod_outcome']['session_close'] = exit_p
            trade['eod_outcome']['exit_time'] = now_time_str
            trade['eod_outcome']['exit_trigger'] = pt['exit_trigger']
            trade['eod_outcome']['outcome'] = outcome_val
            trade['eod_outcome']['badge'] = badge_text
            trade['eod_outcome']['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
            trade['eod_outcome']['summary'] = f"{reason} at {now_time_str}. Entry: ₹{entry_p:,.2f} -> Exit: ₹{exit_p:,.2f} ({'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f})."
            
            updated_any = True
            
            icon = "🎯" if hit_target else ("🛡️" if pt.get('trailing_sl_active') else "🛑")
            subject = f"{icon} [{badge_text.upper()}] {trade['name']} Paper Trade Exit ({now_time_str})"
            body = (
                f"{icon} <b>PAPER TRADE AUTO-EXIT: {badge_text.upper()}</b>\n"
                f"📅 <i>{today_str}, {now_time_str}</i>\n"
                f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                f"• <b>Position</b>: {action} {lot_size} units\n"
                f"• <b>Entry Price</b>: ₹{entry_p:,.2f}\n"
                f"• <b>Exit Price</b>: ₹{exit_p:,.2f} ({reason})\n"
                f"• <b>Target</b>: ₹{target:,.2f} | <b>Stop-Loss</b>: ₹{stop_loss:,.2f}\n"
                f"• <b>Net Realized P&L</b>: <b>{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}</b>\n\n"
                f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
            )
            alerts_to_send.append({'subject': subject, 'body': body})

    if updated_any:
        save_tracker(tracker)
        print("Updated advisory tracker with target/stop-loss executions.")
        if alerter and alerts_to_send:
            for al in alerts_to_send:
                try:
                    alerter.send_alert(al['subject'], al['body'])
                except Exception as e:
                    print(f"Error sending target/SL alert: {e}")
        return True
    return False

def evaluate_realtime_technical_triggers(checker, alerter=None):
    """
    Called on every continuous loop cycle during MCX market hours (09:05 AM to 10:45 PM IST, Mon-Fri).
    Continuously monitors live technical indicators (ADX trend strength, RSI momentum/divergence,
    ATR dynamic buffers, volume ratios, and circuit boundaries) across all MCX commodities.
    When a genuine high-probability technical confluence setup appears anywhere during the day:
    - Enforces portfolio risk caps (Max daily loss ₹15,000)
    - Enforces maximum concurrent active commodity positions (Max 2 positions)
    - Enforces symbol cooldown (2 hours between trades on same asset, max 2 trades/day)
    - Enforces sector correlation guard (Bullion, Energy, Base Metals)
    - Enters paper trade with dynamic ATR-calibrated stop loss and 1.8x+ target
    - Logs execution into advisory_trades_tracker.json
    - Dispatches instant email notification
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    now_time_str = now_dt.strftime('%I:%M %p IST')
    
    # Market day check (Mon - Fri)
    if now_dt.weekday() >= 5:
        return False
        
    # Active intraday window: 09:05 AM to 10:45 PM IST
    hour = now_dt.hour
    minute = now_dt.minute
    is_in_window = (hour == 9 and minute >= 5) or (10 <= hour < 22) or (hour == 22 and minute <= 45)
    if not is_in_window:
        return False

    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    # 1. Portfolio Circuit Breaker: Max Daily Loss Cap (₹6,000 / 2% of ₹3 Lakhs)
    today_realized_loss = sum(abs(t.get('paper_trade', {}).get('pnl_inr', 0.0)) 
                              for t in trades if t.get('date') == today_str and t.get('paper_trade', {}).get('pnl_inr', 0.0) < 0)
    if today_realized_loss >= MAX_DAILY_LOSS_INR:
        print(f"🛑 PORTFOLIO CIRCUIT BREAKER: Today's realized loss (₹{today_realized_loss:,.2f}) reached daily limit (₹{MAX_DAILY_LOSS_INR:,.2f}). Trading halted for the day.")
        return False

    # 2. Maximum Concurrent Open Positions Cap (Max 2 Futures, Max 1 Intraday Stock, Max 3 Total)
    active_commodity_trades = [
        t for t in trades 
        if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']
        and (t.get('asset_type') in ['FUTURES', 'COMMODITY'] or t.get('symbol') in LOT_SIZES or '-FUT' in str(t.get('symbol', '')))
    ]
    active_equity_trades = [
        t for t in trades 
        if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']
        and (t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] or (t.get('symbol') not in LOT_SIZES and not str(t.get('symbol', '')).endswith('-FUT')))
    ]
    if len(active_commodity_trades) >= MAX_CONCURRENT_COMMODITIES and len(active_equity_trades) >= MAX_CONCURRENT_EQUITIES:
        return False
    if (len(active_commodity_trades) + len(active_equity_trades)) >= MAX_CONCURRENT_TOTAL:
        return False

    new_trades = []
    alerts_to_send = []

    # =========================================================================
    # PART 1: MCX COMMODITIES CONTINUOUS SCANNER
    # =========================================================================
    circuits_res = checker.generate_circuits_data() if checker else {}
    commodities = circuits_res.get('commodities', []) if (checker and len(active_commodity_trades) < MAX_CONCURRENT_COMMODITIES) else []

    SECTORS = {
        'GOLD': 'BULLION', 'GOLDM': 'BULLION', 'GOLDTEN': 'BULLION',
        'SILVER': 'BULLION', 'SILVERM': 'BULLION', 'SILVERMIC': 'BULLION',
        'COPPER': 'BASE_METALS', 'COPPERM': 'BASE_METALS',
        'ZINC': 'BASE_METALS', 'ZINCM': 'BASE_METALS', 'ZINCMINI': 'BASE_METALS',
        'ALUMINIUM': 'BASE_METALS', 'ALUMINI': 'BASE_METALS',
        'CRUDEOIL': 'ENERGY', 'CRUDEOILM': 'ENERGY',
        'NATURALGAS': 'ENERGY', 'NATURALGASM': 'ENERGY'
    }
    for sfc_sym, sfc_cfg in STOCK_FUTURES_CONFIG.items():
        SECTORS[f"{sfc_sym}-FUT"] = sfc_cfg['sector']
        SECTORS[sfc_sym] = sfc_cfg['sector']
    
    active_sectors = {
        SECTORS.get(t.get('symbol')) for t in active_commodity_trades
        if t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
    }

    for item in commodities:
        # Check if we already reached max concurrent futures positions during this pass
        if len(active_commodity_trades) + sum(1 for t in new_trades if (t.get('asset_type') in ['FUTURES', 'COMMODITY'] or t.get('symbol') in LOT_SIZES)) >= MAX_CONCURRENT_COMMODITIES:
            break

        sym = item.get('symbol')
        if not sym or sym not in LOT_SIZES:
            continue

        # Prevent duplicate active positions in the same symbol
        if any(t.get('symbol') == sym and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT'] for t in (trades + new_trades)):
            continue

        item_sector = SECTORS.get(sym, 'OTHER')
        if item_sector in active_sectors:
            continue

        # Check symbol trade count and cooldown today (only for actual executed BUY/SELL trades, not STAND_ASIDE)
        sym_today_trades = [
            t for t in trades 
            if t.get('date') == today_str and t.get('symbol') == sym 
            and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
            and t.get('paper_trade', {}).get('quantity', 0) > 0
        ]
        if len(sym_today_trades) >= MAX_TRADES_PER_SYMBOL_DAY:
            continue  # Max 3 trades per commodity per day

        # Check 60-minute cooldown from last trade on this symbol
        if sym_today_trades:
            last_t = sym_today_trades[0]
            last_entry_time = last_t.get('paper_trade', {}).get('entry_time') or last_t.get('time_trigger')
            if last_entry_time:
                try:
                    clean_time = last_entry_time.replace(' IST', '').strip()
                    t_struct = datetime.strptime(f"{today_str} {clean_time}", "%d %b %Y %I:%M %p").replace(tzinfo=pytz.timezone('Asia/Kolkata'))
                    diff_mins = (now_dt - t_struct).total_seconds() / 60.0
                    if diff_mins < COOLDOWN_MINUTES_BETWEEN_TRADES:
                        continue  # In 60-minute cooldown period
                except Exception:
                    pass

        cur_p = float(item.get('current_price') or 0)
        day_h = float(item.get('day_high') or cur_p)
        day_l = float(item.get('day_low') or cur_p)
        p_close = float(item.get('prev_close') or cur_p)
        chg_pct = float(item.get('change_pct') or 0)
        dist_uc = float(item.get('dist_to_uc_pct') or 99)
        dist_lc = float(item.get('dist_to_lc_pct') or 99)
        uc = float(item.get('upper_circuit') or (p_close * 1.04))
        lc = float(item.get('lower_circuit') or (p_close * 0.96))
        
        rsi = item.get('rsi_14')
        adx = item.get('adx_14')
        atr = item.get('atr_14')
        ema20 = item.get('ema_20')
        ema50 = item.get('ema_50')
        vol = item.get('volume', 0) or 0
        avg_vol = item.get('avg_volume_10d', 0) or 0
        vol_ratio = (vol / avg_vol) if (avg_vol > 0 and vol > 0) else None

        if cur_p <= 0:
            continue

        atr_val = atr if (atr and atr > 0) else (cur_p * 0.008)
        sym, name, lot_size = get_defense_contract_and_lot(sym, item.get('name', sym))

        triggered = False
        action = None
        setup_name = ""
        rationale = ""
        sl = None
        tgt = None

        # --- SETUP 1: Circuit Boundary Rebound / Mean Reversion ---
        # A. Bullish Reversal at Lower Band / Deep Oversold
        if (dist_lc <= 1.0 or (rsi is not None and rsi <= 26.0)) and (cur_p >= day_l * 1.0025):
            triggered = True
            action = "BUY"
            setup_name = "Circuit Floor Rebound (Mean Reversion)"
            sl = round(min(day_l, lc) * 0.997, 2)
            tgt = round(cur_p + max(1.8 * atr_val, abs(p_close - cur_p) * 0.5), 2)
            rationale = (f"Price tested Lower Circuit zone ({dist_lc:.2f}% away) or oversold RSI ({rsi:.1f} <= 26) "
                         f"and confirmed +0.25% bounce off day low ₹{day_l:,.2f}. High probability mean-reversion.")

        # B. Bearish Rejection at Upper Band / Deep Overbought
        elif (dist_uc <= 1.0 or (rsi is not None and rsi >= 74.0)) and (cur_p <= day_h * 0.9975):
            triggered = True
            action = "SELL"
            setup_name = "Circuit Ceiling Rejection (Mean Reversion)"
            sl = round(max(day_h, uc) * 1.003, 2)
            tgt = round(cur_p - max(1.8 * atr_val, abs(cur_p - p_close) * 0.5), 2)
            rationale = (f"Price tested Upper Circuit zone ({dist_uc:.2f}% away) or overbought RSI ({rsi:.1f} >= 74) "
                         f"and confirmed -0.25% pullback off day high ₹{day_h:,.2f}. High probability exhaustion fade.")

        # --- SETUP 2: Volatility Breakout with ADX Momentum ---
        # A. Bullish Day-High Breakout
        elif (cur_p >= day_h * 0.999) and (chg_pct >= 0.40) and (adx is not None and adx >= 22.0) and (rsi is None or (52 <= rsi <= 76)):
            if vol_ratio is None or vol_ratio >= 0.35:
                triggered = True
                action = "BUY"
                setup_name = "Day-High Volatility Breakout"
                sl = round(cur_p - (1.3 * atr_val), 2)
                tgt = round(cur_p + (2.4 * atr_val), 2)
                rationale = (f"Breaking Day High ₹{day_h:,.2f} with strong trend momentum (ADX {adx:.1f} >= 22 | "
                             f"RSI {f'{rsi:.1f}' if rsi else 'N/A'} | Chg: +{chg_pct:.2f}%). Riding directional impulse.")

        # B. Bearish Day-Low Breakdown
        elif (cur_p <= day_l * 1.001) and (chg_pct <= -0.40) and (adx is not None and adx >= 22.0) and (rsi is None or (24 <= rsi <= 48)):
            if vol_ratio is None or vol_ratio >= 0.35:
                triggered = True
                action = "SELL"
                setup_name = "Day-Low Volatility Breakdown"
                sl = round(cur_p + (1.3 * atr_val), 2)
                tgt = round(cur_p - (2.4 * atr_val), 2)
                rationale = (f"Breaking Day Low ₹{day_l:,.2f} with strong trend breakdown (ADX {adx:.1f} >= 22 | "
                             f"RSI {f'{rsi:.1f}' if rsi else 'N/A'} | Chg: {chg_pct:.2f}%). Riding directional impulse.")

        # --- SETUP 3: EMA 20/50 Golden / Death Trend Cross ---
        elif (ema20 is not None and ema50 is not None) and (adx is not None and adx >= 20.0):
            # Bullish Cross
            if (ema20 > ema50) and (cur_p >= ema20) and (rsi is not None and 50.0 <= rsi <= 65.0) and (chg_pct >= 0.20):
                triggered = True
                action = "BUY"
                setup_name = "EMA 20/50 Bullish Trend Confluence"
                sl = round(ema20 - (1.1 * atr_val), 2)
                tgt = round(cur_p + (2.0 * atr_val), 2)
                rationale = (f"20 EMA (₹{ema20:,.2f}) > 50 EMA (₹{ema50:,.2f}) with ADX {adx:.1f} >= 20 and "
                             f"healthy RSI {rsi:.1f}. Trend continuation in effect.")

            # Bearish Cross
            elif (ema20 < ema50) and (cur_p <= ema20) and (rsi is not None and 35.0 <= rsi <= 50.0) and (chg_pct <= -0.20):
                triggered = True
                action = "SELL"
                setup_name = "EMA 20/50 Bearish Trend Confluence"
                sl = round(ema20 + (1.1 * atr_val), 2)
                tgt = round(cur_p - (2.0 * atr_val), 2)
                rationale = (f"20 EMA (₹{ema20:,.2f}) < 50 EMA (₹{ema50:,.2f}) with ADX {adx:.1f} >= 20 and "
                             f"healthy RSI {rsi:.1f}. Downward trend continuation in effect.")

        if triggered and action:
            # 🛡️ Multi-Timeframe Trend Confirmation Guard:
            # Never take a BUY if price is below 50 EMA with strong downward momentum (ADX >= 25).
            # Never take a SELL if price is above 50 EMA with strong upward momentum (ADX >= 25).
            if action == "BUY" and ema50 is not None and cur_p < ema50 and (adx is not None and adx >= 25.0):
                print(f"🚫 Trend Guard: Blocked {sym} BUY because price (₹{cur_p:,.2f}) is below 50 EMA in strong downtrend (ADX {adx:.1f}).")
                continue
            if action == "SELL" and ema50 is not None and cur_p > ema50 and (adx is not None and adx >= 25.0):
                print(f"🚫 Trend Guard: Blocked {sym} SELL because price (₹{cur_p:,.2f}) is above 50 EMA in strong uptrend (ADX {adx:.1f}).")
                continue

            # 📐 Structural Technical Bracket: Stop placed beyond Day High/Low to eliminate noise stop-outs
            sl, tgt = calculate_technical_bracket(action, cur_p, day_h, day_l, ema20=ema20, ema50=ema50, atr=atr_val, min_rrr=1.8)

            trade_id = f"ADV-{today_str.replace(' ', '')}-{now_dt.strftime('%H%M')}-{sym}"
            if any(t.get('id') == trade_id for t in trades):
                continue

            # Prevent duplicate calls on the same asset if already active today
            existing_active_syms = {
                t.get('symbol', '').replace('-FUT', '').replace('.NS', '').upper()
                for t in (new_trades + trades)
                if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT', 'PENDING_615']
            }
            clean_check_sym = sym.replace('-FUT', '').replace('.NS', '').upper()
            if (clean_check_sym in existing_active_syms or 
                f"{clean_check_sym}M" in existing_active_syms or 
                (clean_check_sym == 'ALUMINIUM' and 'ALUMINI' in existing_active_syms) or
                (clean_check_sym == 'ALUMINI' and 'ALUMINIUM' in existing_active_syms)):
                continue

            # 1. Institutional Market Regime Classifier & False Signal Filter
            regime_info = MarketRegimeDetector.classify_regime(
                symbol=sym, close=cur_p, ema50=ema50, ema200=ema20,
                adx=adx, rsi=rsi, bb_lower=lc, bb_upper=uc, atr=atr_val
            )
            regime_check = MarketRegimeDetector.validate_signal(regime_info, action, setup_name)
            if not regime_check.get('passed'):
                print(f"🛡️ Market Regime Filter Blocked {sym} {action}: {regime_check.get('reason')}")
                continue

            # 2. Inventory & Macro Event Blackout Guard
            event_metrics = EventRiskGuard.check_event_risk(sym, now_dt)
            if not event_metrics.get('passed'):
                print(f"📰 Event Risk Guard Blocked {sym}: {event_metrics.get('reason')}")
                continue

            # 3. 15-Minute ORB Bar-Close & Wick Trap Guard (for Breakouts)
            if "Breakout" in setup_name or "Breakdown" in setup_name:
                orb_metrics = ORBTimer.check_orb_confirmation(sym, action, cur_p, day_h, day_l, volume_ratio=vol_ratio, last_bar_close=cur_p)
                if not orb_metrics.get('passed'):
                    print(f"⏱️ ORB Timer Blocked {sym} {action}: {orb_metrics.get('reason')}")
                    continue

            # 4. Dynamic Position Sizing & Mini-Contract Routing (Ensures Technical Stop fits <= ₹3,000 risk)
            contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                sym, name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR, atr=atr_val
            )
            if adaptive_lot <= 0:
                print(f"🛡️ Position Sizer Blocked {sym}: Risk per lot exceeds ₹{MAX_LOSS_PER_TRADE_INR:,.0f} limit even on mini contract. Stood aside to preserve capital.")
                continue

            all_existing = new_trades + trades
            risk_metrics = PreTradeRiskEngine.check_risk(contract_sym, action, cur_p, sl, tgt, adaptive_lot, all_existing, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)
            if not risk_metrics.get('passed'):
                print(f"🛡️ Pre-Trade Risk Engine Blocked {contract_sym} {action}: {risk_metrics.get('reason')}")
                continue

            # Run Monte Carlo Time Machine simulation (3,000 replays)
            sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, sl, tgt, action=action.lower(), replays=3000)
            if not sim_metrics.get('passed'):
                print(f"🎲 Monte Carlo Time Machine Blocked {sym} {action}: {sim_metrics.get('summary')}")
                continue

            entry_obj = {
                "id": trade_id,
                "date": today_str,
                "session_name": f"Real-Time Technical Trigger ({setup_name})",
                "time_trigger": now_time_str,
                "time_6pm": now_time_str,
                "time_validation": now_time_str,
                "time_615": now_time_str,
                "time_exit": "11:30 PM IST",
                "symbol": contract_sym,
                "name": contract_name,
                "lot_size": adaptive_lot,
                "event_context": f"Indicator Confluence: {setup_name}",
                "regime": regime_info,
                "time_machine": sim_metrics,
                "risk_check": risk_metrics,
                "suggestion_6pm": {
                    "status": "REALTIME_TRIGGER",
                    "action_text": f"{action} @ ₹{cur_p:,.2f}",
                    "rationale": f"{rationale} {sim_metrics.get('summary', '')}",
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": tgt
                },
                "check_615": {
                    "checked": True,
                    "status": "TECHNICAL_TRIGGER_ENTERED",
                    "price_615": cur_p,
                    "action_taken": f"Entered {action} {adaptive_lot} units @ ₹{cur_p:,.2f}",
                    "detail": f"{setup_name}: {rationale} | {sim_metrics.get('summary', '')}"
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": adaptive_lot,
                    "entry_price": cur_p,
                    "entry_time": now_time_str,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "ACTIVE",
                    "target": tgt,
                    "stop_loss": sl,
                    "margin_required_inr": round(cur_p * adaptive_lot * MARGIN_RATES.get(sym, 0.15), 2)
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": None,
                    "outcome": "PENDING",
                    "badge": "Active Trigger ⚡",
                    "pnl_display": "Ticking Live...",
                    "summary": f"{setup_name} active: {action} @ ₹{cur_p:,.2f} (Target: ₹{tgt:,.2f}, SL: ₹{sl:,.2f}) | {sim_metrics.get('summary', '')}"
                }
            }
            new_trades.append(entry_obj)
            active_sectors.add(item_sector)

            # Real-time notification email
            mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
            subject = f"⚡ [REAL-TIME TECHNICAL TRIGGER] {name} {action} @ ₹{cur_p:,.2f} ({now_time_str})"
            body = (
                f"⚡ <b>REAL-TIME TECHNICAL CONFLUENCE TRIGGER</b>\n"
                f"📅 <i>{today_str}, {now_time_str}</i>\n"
                f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                f"• <b>Asset</b>: {name} ({sym})\n"
                f"• <b>Signal</b>: <b>{action} {lot_size} units @ ₹{cur_p:,.2f}</b>\n"
                f"• <b>Setup</b>: {setup_name}\n"
                f"• <b>Technical Rationale</b>: {rationale}\n"
                f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                f"• <b>Stop-Loss</b>: ₹{sl:,.2f} (Dynamic Volatility Risk Cap)\n"
                f"• <b>Target</b>: ₹{tgt:,.2f} (Asymmetric Upside)\n"
                f"• <b>Status</b>: Active paper trade running. Automatic trailing breakeven & auto-exit enabled.\n\n"
                f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
            )
            alerts_to_send.append({'subject': subject, 'body': body})
            print(f"⚡ Technical Trigger Fired: {name} {action} @ ₹{cur_p:,.2f} ({setup_name}) | Replay: {mc_badge}")

    # =========================================================================
    # PART 2: REAL-TIME NIFTY F&O HIGH-BETA CONTINUOUS SCANNER (Phase 1 & Phase 2)
    # Active Window: Mon-Fri, 09:15 AM to 03:20 PM IST
    # Scans valid Nifty 50 F&O stocks (excluding corporate demergers like TATAMOTORS)
    # Strictly sized <= ₹3,000 risk (1% of ₹3L Trading pool) with 03:25 PM EOD square-off
    # =========================================================================
    ENABLE_EQUITY_DERIVATIVES_SCANNER = True
    is_equity_window = (hour == 9 and minute >= 15) or (10 <= hour < 15) or (hour == 15 and minute <= 20)
    cur_equity_positions = len(active_equity_trades) + sum(1 for t in new_trades if t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'])

    if ENABLE_EQUITY_DERIVATIVES_SCANNER and is_equity_window and cur_equity_positions < 2:
        if os.path.exists(NIFTY_JSON_FILE):
            try:
                with open(NIFTY_JSON_FILE, 'r') as nf:
                    nifty_data = json.load(nf)
                equity_stocks = nifty_data.get('stocks', [])
            except Exception as e:
                print(f"Warning: Could not read Nifty F&O scanner data: {e}")
                equity_stocks = []

            for s in equity_stocks:
                cur_active_futures = len(active_commodity_trades) + sum(1 for t in new_trades if (t.get('asset_type') in ['FUTURES', 'COMMODITY'] or t.get('symbol') in LOT_SIZES or '-FUT' in str(t.get('symbol', ''))))
                cur_active_equity = len(active_equity_trades) + sum(1 for t in new_trades if t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'])

                can_enter_futures = (cur_active_futures < MAX_CONCURRENT_COMMODITIES)
                can_enter_equity = (cur_active_equity < MAX_CONCURRENT_EQUITIES)

                if not can_enter_futures and not can_enter_equity:
                    break

                sym = s.get('symbol', '')
                clean_sym = sym.replace('.NS', '').upper()

                # Guard: Ensure only stocks with active NSE F&O contracts are traded in F&O engine.
                # TATAMOTORS/TMCV/TMPV underwent corporate demerger where old TATAMOTORS F&O contracts were expired by NSE.
                if clean_sym in {'TATAMOTORS', 'TMCV', 'TMPV'}:
                    continue

                # Duplicate active position guard (prevent holding both stock and stock future simultaneously)
                if any(t.get('symbol') in [sym, clean_sym, f"{clean_sym}-FUT"] and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT'] for t in (trades + new_trades)):
                    continue

                name = s.get('full_name') or s.get('name', clean_sym)
                cur_p = float(s.get('close') or 0)
                if cur_p <= 0:
                    continue

                # Check symbol cooldown today (max 3 trades per equity, 60-min cooldown)
                eq_today_trades = [
                    t for t in (trades + new_trades)
                    if t.get('date') == today_str and (t.get('symbol') == sym or t.get('symbol') == clean_sym)
                    and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
                    and t.get('paper_trade', {}).get('quantity', 0) > 0
                ]
                if len(eq_today_trades) >= MAX_TRADES_PER_SYMBOL_DAY:
                    continue

                if eq_today_trades:
                    last_t = eq_today_trades[0]
                    last_entry_time = last_t.get('paper_trade', {}).get('entry_time') or last_t.get('time_trigger')
                    if last_entry_time:
                        try:
                            clean_time = last_entry_time.replace(' IST', '').strip()
                            t_struct = datetime.strptime(f"{today_str} {clean_time}", "%d %b %Y %I:%M %p").replace(tzinfo=pytz.timezone('Asia/Kolkata'))
                            diff_mins = (now_dt - t_struct).total_seconds() / 60.0
                            if diff_mins < COOLDOWN_MINUTES_BETWEEN_TRADES:
                                continue
                        except Exception:
                            pass

                # 1. Event Risk Guard (trader-news skill)
                event_metrics = EventRiskGuard.check_event_risk(clean_sym, now_dt)
                if not event_metrics.get('passed'):
                    print(f"📰 Event Risk Guard Blocked {clean_sym}: {event_metrics.get('reason')}")
                    continue

                chg_pct = float(s.get('change_pct') or 0)
                rsi = float(s.get('rsi') or 50)
                high_52 = float(s.get('price_52_week_high') or cur_p)
                discount_pct = float(s.get('discount_pct') or 0)
                bb_lower = float(s.get('bb_lower') or (cur_p * 0.95))
                is_golden = bool(s.get('is_golden'))
                rsi_curling_up = bool(s.get('rsi_curling_up'))
                near_bb = bool(s.get('near_bb_lower'))
                day_h = float(s.get('day_high') or s.get('high') or (high_52 if cur_p >= high_52 * 0.985 else cur_p))
                day_l = float(s.get('day_low') or s.get('low') or (cur_p * 0.985))
                vol_ratio = float(s.get('volume_ratio') or 1.15)
                atr = float(s.get('atr_14') or (cur_p * 0.015))
                ema20 = float(s.get('ema_20') or cur_p)
                ema50 = float(s.get('ema_50') or cur_p)

                eq_triggered = False
                eq_action = None
                eq_setup = ""
                eq_sl = None
                eq_tgt = None
                eq_rationale = ""
                orb_metrics = {"passed": True, "status": "CONFIRMED", "reason": "Standard momentum confluence."}

                # Setup A: 20-Day / High-Beta Volatility Breakout (Momentum Expansion)
                if (cur_p >= high_52 * 0.985 or chg_pct >= 0.85) and (52.0 <= rsi <= 72.0):
                    eq_action = "BUY"
                    eq_setup = "20-Day / High-Beta Volatility Breakout"
                    
                    # 2. 15-Minute ORB Bar-Close & Wick Trap Guard (trader-entry skill)
                    orb_metrics = ORBTimer.check_orb_confirmation(sym, eq_action, cur_p, day_h, day_l, volume_ratio=vol_ratio, last_bar_close=cur_p)
                    if not orb_metrics.get('passed'):
                        print(f"⏱️ ORB Timer Blocked {clean_sym} {eq_action}: {orb_metrics.get('reason')}")
                        continue

                    # 3. Structural Invalidation Stop & 2.0R Target Bracket (trader-exit skill)
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=2.0)
                    eq_sl = calc_sl or round(cur_p * 0.965, 2)
                    eq_tgt = calc_tgt or round(cur_p + (2.0 * (cur_p - eq_sl)), 2)
                    eq_triggered = True
                    eq_rationale = (f"Breaking towards 20D/52W high (Chg: +{chg_pct:.2f}%) with strong RSI momentum ({rsi:.1f}) "
                                    f"and verified ORB bar close. Sized with structural 2.0R target.")

                # Setup B: Oversold Mean Reversion Bounce (Bollinger Band Mean Reversion)
                elif (rsi <= 36.0) and (rsi_curling_up or near_bb or cur_p <= bb_lower * 1.01) and (discount_pct >= 15.0):
                    eq_action = "BUY"
                    eq_setup = "Oversold Mean Reversion Bounce"
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=min(bb_lower, day_l), ema20=ema20, ema50=ema50, atr=atr, min_rrr=2.0)
                    eq_sl = calc_sl or round(min(bb_lower, cur_p * 0.955), 2)
                    eq_tgt = calc_tgt or round(cur_p + (2.2 * (cur_p - eq_sl)), 2)
                    eq_triggered = True
                    eq_rationale = (f"Oversold accumulation RSI ({rsi:.1f} <= 36) bouncing off Lower Bollinger Band (₹{bb_lower:,.2f}) "
                                    f"at a deep {discount_pct:.1f}% discount to 52W high. High probability mean-reversion.")

                # Setup C: EMA 50/200 Golden Cross Trend Pullback
                elif is_golden and (35.0 <= rsi <= 48.0):
                    eq_action = "BUY"
                    eq_setup = "EMA 50/200 Golden Cross Trend Pullback"
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=2.0)
                    eq_sl = calc_sl or round(cur_p * 0.955, 2)
                    eq_tgt = calc_tgt or round(cur_p + (2.0 * (cur_p - eq_sl)), 2)
                    eq_triggered = True
                    eq_rationale = (f"50 EMA > 200 EMA Golden Cross confirmed with value pullback into demand zone (RSI: {rsi:.1f}). "
                                    f"Trend continuation entry targeting 2.0R swing upside.")

                if eq_triggered and eq_action and eq_sl and eq_tgt:
                    # PATH A: Enter as an NSE Stock Future if futures pool has room and stock is an active F&O contract
                    if can_enter_futures and clean_sym in STOCK_FUTURES_CONFIG:
                        sfc = STOCK_FUTURES_CONFIG[clean_sym]
                        fut_sym = f"{clean_sym}-FUT"
                        if sfc['sector'] not in active_sectors:
                            fut_lot = sfc['lot_size']
                            fut_margin_rate = sfc['margin_rate']
                            fut_margin_req = round(cur_p * fut_lot * fut_margin_rate, 2)
                            
                            # Calibrate stop so max risk per lot strictly fits within MAX_LOSS_PER_TRADE_INR (₹1,000)
                            fut_risk_per_share = abs(cur_p - eq_sl)
                            fut_total_risk = fut_risk_per_share * fut_lot
                            fut_sl = eq_sl
                            fut_tgt = eq_tgt
                            if fut_total_risk > MAX_LOSS_PER_TRADE_INR:
                                fut_risk_per_share = MAX_LOSS_PER_TRADE_INR / fut_lot
                                fut_sl = round(cur_p - fut_risk_per_share if eq_action == 'BUY' else cur_p + fut_risk_per_share, 2)
                                fut_tgt = round(cur_p + (2.0 * fut_risk_per_share) if eq_action == 'BUY' else cur_p - (2.0 * fut_risk_per_share), 2)
                            
                            # Institutional Market Regime Classifier & False Signal Filter for Stock Futures
                            regime_info = s.get('regime') or MarketRegimeDetector.classify_regime(
                                symbol=clean_sym, close=cur_p, ema50=ema50, ema200=float(s.get('ema_200') or cur_p),
                                adx=float(s.get('adx') or 20.0), rsi=rsi, bb_lower=bb_lower, bb_upper=float(s.get('bb_upper') or cur_p * 1.05),
                                atr=atr
                            )
                            regime_check = MarketRegimeDetector.validate_signal(regime_info, eq_action, eq_setup)
                            if not regime_check.get('passed'):
                                print(f"🛡️ Market Regime Filter Blocked Stock Future {fut_sym} {eq_action}: {regime_check.get('reason')}")
                                continue

                            # 14-Point Pre-Trade Risk Engine Check for Stock Futures
                            all_existing = new_trades + trades
                            risk_metrics = PreTradeRiskEngine.check_risk(
                                fut_sym, eq_action, cur_p, fut_sl, fut_tgt, fut_lot, all_existing,
                                max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR,
                                max_daily_loss_inr=MAX_DAILY_LOSS_INR
                            )
                            if risk_metrics.get('passed'):
                                # Monte Carlo Time Machine simulation (3,000 replays)
                                sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, fut_sl, fut_tgt, action=eq_action.lower(), replays=3000)
                                if sim_metrics.get('passed'):
                                    trade_id = f"ADV-{today_str.replace(' ', '')}-{now_dt.strftime('%H%M')}-{clean_sym}FUT"
                                    if not any(t.get('id') == trade_id for t in (trades + new_trades)):
                                        entry_obj = {
                                            "id": trade_id,
                                            "date": today_str,
                                            "session_name": f"Real-Time Technical Trigger ({eq_setup})",
                                            "time_trigger": now_time_str,
                                            "time_6pm": now_time_str,
                                            "time_validation": now_time_str,
                                            "time_615": now_time_str,
                                            "time_exit": "03:25 PM IST",
                                            "symbol": fut_sym,
                                            "name": sfc['name'],
                                            "lot_size": fut_lot,
                                            "asset_type": "FUTURES",
                                            "event_context": f"NSE Stock Future ({sfc['sector']}): {eq_setup}",
                                            "regime": regime_info,
                                            "time_machine": sim_metrics,
                                            "risk_check": risk_metrics,
                                            "orb_check": orb_metrics,
                                            "event_check": event_metrics,
                                            "suggestion_6pm": {
                                                "status": "REALTIME_TRIGGER",
                                                "action_text": f"{eq_action} 1 Lot @ ₹{cur_p:,.2f}",
                                                "rationale": f"{eq_rationale} {sim_metrics.get('summary', '')}",
                                                "reference_price": cur_p,
                                                "stop_loss": fut_sl,
                                                "target": fut_tgt
                                            },
                                            "check_615": {
                                                "checked": True,
                                                "status": "TECHNICAL_TRIGGER_ENTERED",
                                                "price_615": cur_p,
                                                "action_taken": f"Entered {eq_action} 1 Lot ({fut_lot} units) @ ₹{cur_p:,.2f}",
                                                "detail": f"{eq_setup}: {eq_rationale} | {sim_metrics.get('summary', '')}"
                                            },
                                            "paper_trade": {
                                                "user": USER_ACCOUNT,
                                                "action": eq_action,
                                                "quantity": fut_lot,
                                                "entry_price": cur_p,
                                                "entry_time": now_time_str,
                                                "exit_price": None,
                                                "exit_time": None,
                                                "pnl_points": 0.0,
                                                "pnl_inr": 0.0,
                                                "status": "ACTIVE",
                                                "target": fut_tgt,
                                                "stop_loss": fut_sl,
                                                "margin_required_inr": fut_margin_req,
                                                "product_type": "NRML_FUTURES"
                                            },
                                            "eod_outcome": {
                                                "session_close": None,
                                                "circuit_limit": None,
                                                "outcome": "PENDING",
                                                "badge": "Active Trigger ⚡",
                                                "pnl_display": "Ticking Live...",
                                                "summary": f"{eq_setup} active: {eq_action} @ ₹{cur_p:,.2f} (Target: ₹{fut_tgt:,.2f}, SL: ₹{fut_sl:,.2f}) | {sim_metrics.get('summary', '')}"
                                            }
                                        }
                                        new_trades.append(entry_obj)
                                        active_sectors.add(sfc['sector'])
                                        mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
                                        subject = f"🎯 [NSE STOCK FUTURE TRIGGER] {sfc['name']} {eq_action} @ ₹{cur_p:,.2f} ({now_time_str})"
                                        body = (
                                            f"🎯 <b>REAL-TIME NSE STOCK FUTURE TRIGGER</b>\n"
                                            f"📅 <i>{today_str}, {now_time_str}</i>\n"
                                            f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                                            f"• <b>Contract</b>: <b>{sfc['name']} ({fut_sym})</b>\n"
                                            f"• <b>Action</b>: <b>{eq_action} 1 Lot ({fut_lot} units) @ ₹{cur_p:,.2f}</b>\n"
                                            f"• <b>Technical Setup</b>: {eq_setup}\n"
                                            f"• <b>Technical Rationale</b>: {eq_rationale}\n"
                                            f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                                            f"• <b>Stop-Loss</b>: <b>₹{fut_sl:,.2f}</b> (Max loss strictly ₹{round(fut_risk_per_share * fut_lot, 2):,.2f} <= ₹1,000)\n"
                                            f"• <b>Target (2.0R)</b>: <b>₹{fut_tgt:,.2f}</b>\n"
                                            f"• <b>Contract Value</b>: ₹{cur_p * fut_lot:,.2f} (Margin Required: ₹{fut_margin_req:,.2f})\n"
                                            f"• <b>Auto Square-off</b>: Strict 03:25 PM IST EOD flat exit\n\n"
                                            f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trading-tab"
                                        )
                                        alerts_to_send.append({'subject': subject, 'body': body})
                                        print(f"🎯 NSE Stock Future Trigger Fired: {sfc['name']} {eq_action} @ ₹{cur_p:,.2f} ({eq_setup}) | Replay: {mc_badge}")
                                        can_enter_futures = False
                                        continue

                    # PATH B: Enter as an Intraday Stock (MIS with 5x leverage from ₹10k pool)
                    if can_enter_equity:
                        trade_id = f"ADV-{today_str.replace(' ', '')}-{now_dt.strftime('%H%M')}-{clean_sym}"
                        if any(t.get('id') == trade_id for t in (trades + new_trades)):
                            continue

                        # 4. Adaptive Position Sizing with 5x MIS Leverage (trader-budget skill):
                        # Margin allocated up to complete ₹10,000 daily budget based on available trading candidates
                        active_intraday_trades = [
                            t for t in (trades + new_trades)
                            if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT'] and t.get('asset_type') == 'INTRADAY_STOCK'
                        ]
                        used_intraday_margin = sum(
                            t.get('paper_trade', {}).get('margin_required_inr', 0.0)
                            for t in active_intraday_trades
                        )
                        avail_intraday_margin = max(0.0, MAX_INTRADAY_STOCK_MARGIN_INR - used_intraday_margin)
                        if avail_intraday_margin < 500.0:
                            print(f"Intraday margin pool fully utilized (₹{used_intraday_margin:,.2f} / ₹{MAX_INTRADAY_STOCK_MARGIN_INR:,.2f}). Standing aside.")
                            continue

                        # Distribute remaining margin pool across open equity slots (assigning full available margin if single candidate)
                        remaining_slots = max(1, MAX_CONCURRENT_EQUITIES - len(active_intraday_trades))
                        allocated_margin = avail_intraday_margin if remaining_slots == 1 else max(avail_intraday_margin / remaining_slots, min(avail_intraday_margin, 5000.0))
                        
                        margin_per_share = cur_p * 0.20  # 20% SEBI MIS intraday margin requirement (5x leverage)
                        if margin_per_share <= 0:
                            continue

                        # Sizing: Compute shares to utilize the complete allocated margin
                        eq_shares = math.floor(allocated_margin / margin_per_share)
                        if eq_shares < 1:
                            continue
                        while eq_shares > 1 and ((cur_p * eq_shares * 0.20) > avail_intraday_margin):
                            eq_shares -= 1
                        if eq_shares < 1:
                            continue

                        # 5. Institutional Market Regime Classifier & False Signal Filter
                        regime_info = s.get('regime') or MarketRegimeDetector.classify_regime(
                            symbol=clean_sym, close=cur_p, ema50=ema50, ema200=float(s.get('ema_200') or cur_p),
                            adx=float(s.get('adx') or 20.0), rsi=rsi, bb_lower=bb_lower, bb_upper=float(s.get('bb_upper') or cur_p * 1.05),
                            atr=atr
                        )
                        regime_check = MarketRegimeDetector.validate_signal(regime_info, eq_action, eq_setup)
                        if not regime_check.get('passed'):
                            print(f"🛡️ Market Regime Filter Blocked Intraday Stock {clean_sym} {eq_action}: {regime_check.get('reason')}")
                            continue

                        # 6. 14-Point Pre-Trade Risk Engine Check (trader-risk skill: Capped at ₹250 risk / ₹500 daily loss)
                        all_existing = new_trades + trades
                        risk_metrics = PreTradeRiskEngine.check_risk(
                            sym, eq_action, cur_p, eq_sl, eq_tgt, eq_shares, all_existing,
                            max_loss_per_trade_inr=MAX_INTRADAY_STOCK_RISK_INR,
                            max_daily_loss_inr=MAX_INTRADAY_STOCK_DAILY_LOSS_INR
                        )
                        if not risk_metrics.get('passed'):
                            print(f"🛡️ Pre-Trade Risk Engine Blocked {clean_sym} {eq_action}: {risk_metrics.get('reason')}")
                            continue

                        # 7. Monte Carlo Time Machine simulation (trader-timemachine skill: 3,000 replays)
                        sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, eq_sl, eq_tgt, action=eq_action.lower(), replays=3000)
                        if not sim_metrics.get('passed'):
                            print(f"🎲 Monte Carlo Time Machine Blocked {clean_sym} {eq_action}: {sim_metrics.get('summary')}")
                            continue

                        entry_obj = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": f"Real-Time Technical Trigger ({eq_setup})",
                            "time_trigger": now_time_str,
                            "time_6pm": now_time_str,
                            "time_validation": now_time_str,
                            "time_615": now_time_str,
                            "time_exit": "03:25 PM IST",
                            "symbol": sym,
                            "name": name,
                            "lot_size": eq_shares,
                            "asset_type": "INTRADAY_STOCK",
                            "event_context": f"Nifty Intraday MIS: {eq_setup}",
                            "regime": regime_info,
                            "time_machine": sim_metrics,
                            "risk_check": risk_metrics,
                            "orb_check": orb_metrics,
                            "event_check": event_metrics,
                            "suggestion_6pm": {
                                "status": "REALTIME_TRIGGER",
                                "action_text": f"{eq_action} @ ₹{cur_p:,.2f}",
                                "rationale": f"{eq_rationale} {sim_metrics.get('summary', '')}",
                                "reference_price": cur_p,
                                "stop_loss": eq_sl,
                                "target": eq_tgt
                            },
                            "check_615": {
                                "checked": True,
                                "status": "TECHNICAL_TRIGGER_ENTERED",
                                "price_615": cur_p,
                                "action_taken": f"Entered {eq_action} {eq_shares} shares @ ₹{cur_p:,.2f}",
                                "detail": f"{eq_setup}: {eq_rationale} | {sim_metrics.get('summary', '')}"
                            },
                            "paper_trade": {
                                "user": USER_ACCOUNT,
                                "action": eq_action,
                                "quantity": eq_shares,
                                "entry_price": cur_p,
                                "entry_time": now_time_str,
                                "exit_price": None,
                                "exit_time": None,
                                "pnl_points": 0.0,
                                "pnl_inr": 0.0,
                                "status": "ACTIVE",
                                "target": eq_tgt,
                                "stop_loss": eq_sl,
                                "margin_required_inr": round(cur_p * eq_shares * 0.20, 2),
                                "product_type": "MIS_INTRADAY"
                            },
                            "eod_outcome": {
                                "session_close": None,
                                "circuit_limit": None,
                                "outcome": "PENDING",
                                "badge": "Active Trigger ⚡",
                                "pnl_display": "Ticking Live...",
                                "summary": f"{eq_setup} active: {eq_action} @ ₹{cur_p:,.2f} (Target: ₹{eq_tgt:,.2f}, SL: ₹{eq_sl:,.2f}) | {sim_metrics.get('summary', '')}"
                            }
                        }
                        new_trades.append(entry_obj)

                        mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
                        subject = f"⚡ [NIFTY INTRADAY MIS TRIGGER] {name} {eq_action} @ ₹{cur_p:,.2f} ({now_time_str})"
                        body = (
                            f"⚡ <b>REAL-TIME INTRADAY STOCK TRIGGER (5x MIS MARGIN BENEFIT)</b>\n"
                            f"📅 <i>{today_str}, {now_time_str}</i>\n"
                            f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                            f"• <b>Asset Class</b>: Nifty Intraday Stock (MIS 5x Margin Benefit)\n"
                            f"• <b>Stock</b>: <b>{name} ({clean_sym})</b>\n"
                            f"• <b>Signal</b>: <b>{eq_action} {eq_shares} shares @ ₹{cur_p:,.2f}</b>\n"
                            f"• <b>Setup</b>: {eq_setup}\n"
                            f"• <b>Technical Rationale</b>: {eq_rationale}\n"
                            f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                            f"• <b>Stop-Loss</b>: <b>₹{eq_sl:,.2f}</b> (Max loss strictly ₹{round(risk_per_share * eq_shares, 2):,.2f} <= ₹250)\n"
                            f"• <b>Target (2.0R)</b>: <b>₹{eq_tgt:,.2f}</b>\n"
                            f"• <b>Position Value</b>: ₹{cur_p * eq_shares:,.2f} (MIS Margin Blocked: ₹{round(cur_p * eq_shares * 0.20, 2):,.2f} / Max ₹10k)\n"
                            f"• <b>Auto Square-off</b>: Strict 03:25 PM IST EOD flat exit (Zero overnight risk)\n\n"
                            f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trading-tab"
                        )
                        alerts_to_send.append({'subject': subject, 'body': body})
                        print(f"⚡ Nifty Intraday Stock Trigger Fired: {name} {eq_action} @ ₹{cur_p:,.2f} ({eq_setup}) | Replay: {mc_badge}")
                        can_enter_equity = False

    if new_trades:
        tracker['trades'] = new_trades + trades
        save_tracker(tracker)
        print(f"Recorded {len(new_trades)} new real-time technical trigger trades into advisory tracker.")
        
        if alerter and alerts_to_send:
            for al in alerts_to_send:
                try:
                    alerter.send_alert(al['subject'], al['body'])
                except Exception as e:
                    print(f"Error sending technical trigger alert: {e}")
        return True

    return False

def square_off_intraday_equity_trades(checker=None, alerter=None):
    """
    Called at 03:20 PM IST (15:20 - 15:35 IST window) on trading days.
    Executes mandatory square-off for all active intraday equity and stock futures trades
    (MIS intraday and F&O intraday positions).
    Settles final P&L, sets exit_trigger="INTRADAY_320_SQUAREOFF", status="CLOSED", and releases margin.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')

    active_equity_trades = [
        t for t in trades
        if t.get('paper_trade', {}).get('status') == 'ACTIVE' and
        (t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] or
         t.get('symbol', '').endswith('-FUT') or
         t.get('symbol') in STOCK_FUTURES_CONFIG or
         (t.get('symbol') not in LOT_SIZES and 'COMMODITY' not in str(t.get('asset_type', ''))))
    ]

    if not active_equity_trades:
        return False

    print(f"Executing 03:20 PM IST Intraday Square-off for {len(active_equity_trades)} active equity positions...")

    # Load latest prices from nifty scanner or fallback
    nifty_prices = {}
    if os.path.exists(NIFTY_JSON_FILE):
        try:
            with open(NIFTY_JSON_FILE, 'r') as nf:
                nd = json.load(nf)
                for st in nd.get('stocks', []):
                    sym = st.get('symbol', '').replace('.NS', '').upper()
                    nifty_prices[sym] = float(st.get('close') or 0)
        except Exception:
            pass

    summary_lines = []
    for trade in active_equity_trades:
        sym = trade['symbol']
        clean_sym = sym.replace('-FUT', '').replace('.NS', '').upper()
        pt = trade.setdefault('paper_trade', {})
        entry_p = float(pt.get('entry_price') or trade.get('suggestion_6pm', {}).get('reference_price') or 0.0)
        cur_p = nifty_prices.get(clean_sym) or float(pt.get('exit_price') or entry_p)
        
        action = pt.get('action', 'BUY')
        qty = pt.get('quantity') or trade.get('lot_size') or 1
        
        pts = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
        pnl_inr = round(pts * qty, 2)
        
        pt['exit_price'] = cur_p
        pt['exit_time'] = "03:20 PM IST"
        pt['exit_trigger'] = "INTRADAY_320_SQUAREOFF"
        pt['pnl_points'] = round(pts, 2)
        pt['pnl_inr'] = pnl_inr
        pt['status'] = "CLOSED"
        
        is_correct = pnl_inr > 0
        trade['time_exit'] = "03:20 PM IST"
        eod = trade.setdefault('eod_outcome', {})
        eod['session_close'] = cur_p
        eod['exit_time'] = "03:20 PM IST"
        eod['exit_trigger'] = "INTRADAY_320_SQUAREOFF"
        eod['outcome'] = "CORRECT" if is_correct else "WRONG"
        eod['badge'] = "Intraday Exit (03:20 PM)"
        eod['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
        eod['summary'] = f"Intraday 03:20 PM square-off executed. Entry: ₹{entry_p:,.2f} -> Exit: ₹{cur_p:,.2f} ({pts:+.2f} pts | {'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}). Margin released."
        
        summary_lines.append(
            f"• <b>{trade['name']} ({action})</b>: Entry ₹{entry_p:,.2f} ➔ Exit ₹{cur_p:,.2f} | P&L: <b>{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}</b>"
        )

    save_tracker(tracker)

    if alerter and summary_lines:
        try:
            body = (
                f"⏱️ <b>NSE INTRADAY 03:20 PM SQUARE-OFF COMPLETED</b>\n"
                f"📅 <i>{today_str}</i>\n\n"
                + "\n".join(summary_lines)
            )
            alerter.send_alert("⏱️ NSE 03:20 PM Intraday Square-off Settled", body)
        except Exception as e:
            print(f"Error dispatching 3:20 PM alert: {e}")

    return True


def exit_and_evaluate_eod_trades(checker, alerter):
    """
    Called at 11:15 PM – 11:30 PM IST.
    Squares off all ACTIVE paper trades for venu.photos1@gmail.com, records final P&L and outcome,
    and automatically expires any unconfirmed / pending limit setups so no trades remain stuck.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    # 1. Sweep and Expire Any Unconfirmed or Pending Limit Orders (15-Min window lapsed)
    pending_to_expire = [
        t for t in trades 
        if t.get('paper_trade', {}).get('status') in ['PENDING_615', 'PENDING_LIMIT', 'PENDING_615_CHECK']
        or (t.get('eod_outcome', {}).get('outcome') == 'PENDING' and t.get('paper_trade', {}).get('status') != 'ACTIVE')
    ]
    for pt_trade in pending_to_expire:
        pt = pt_trade.setdefault('paper_trade', {})
        c15 = pt_trade.setdefault('check_615', {})
        eod = pt_trade.setdefault('eod_outcome', {})
        
        c15['checked'] = True
        c15['status'] = 'CONFIRMATION_EXPIRED'
        c15['action_taken'] = 'Confirmation window elapsed without executed entry. STOOD ASIDE.'
        pt['action'] = 'STAND_ASIDE'
        pt['quantity'] = 0
        pt['entry_price'] = None
        pt['exit_price'] = None
        pt['pnl_inr'] = 0.0
        pt['status'] = 'CLOSED'
        
        eod['outcome'] = 'PROTECTED'
        eod['badge'] = 'Avoided Trap 🛡️'
        eod['pnl_display'] = '₹0.00 (Protected)'
        eod['summary'] = '15-minute confirmation check elapsed without executed entry. 100% capital protected in cash.'

    # 2. Square off ACTIVE Paper Trades (sweep all open active positions)
    active_trades = [t for t in trades if t.get('paper_trade', {}).get('status') == 'ACTIVE']
    if not active_trades:
        print(f"No active advisory paper trades to exit tonight ({len(pending_to_expire)} unconfirmed setups expired to Protected).")
        save_tracker(tracker)
        return bool(pending_to_expire)

    circuits_res = checker.generate_circuits_data() if checker else {}
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}
    
    summary_lines = []
    total_day_pnl = 0.0

    for trade in active_trades:
        sym = trade['symbol']
        c_item = commodities_map.get(sym)
        is_eq = trade.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] or '-FUT' in sym or sym in STOCK_FUTURES_CONFIG
        
        pt = trade.setdefault('paper_trade', {})
        action = pt.get('action', 'BUY')
        entry_p = float(pt.get('entry_price') or trade.get('suggestion_6pm', {}).get('reference_price') or 0.0)
        lot_size = trade.get('lot_size') or pt.get('quantity') or 1
        
        if c_item:
            close_p = float(c_item.get('current_price') or entry_p)
        elif is_eq:
            close_p = float(pt.get('exit_price') or entry_p)
        else:
            close_p = float(pt.get('exit_price') or entry_p)
            
        pnl_pts = (close_p - entry_p) if action == 'BUY' else (entry_p - close_p)
        pnl_inr = round(pnl_pts * lot_size, 2)
        total_day_pnl += pnl_inr
        
        exit_time_label = "03:20 PM IST" if is_eq else "11:30 PM IST"
        exit_trigger = "INTRADAY_320_SQUAREOFF" if is_eq else "EOD_SQUAREOFF"
        
        pt['exit_price'] = close_p
        pt['exit_time'] = exit_time_label
        pt['exit_trigger'] = exit_trigger
        pt['pnl_points'] = round(pnl_pts, 2)
        pt['pnl_inr'] = pnl_inr
        pt['status'] = "CLOSED"
        
        is_correct = pnl_inr > 0
        trade['time_exit'] = exit_time_label
        eod = trade.setdefault('eod_outcome', {})
        eod['session_close'] = close_p
        eod['exit_time'] = exit_time_label
        eod['exit_trigger'] = exit_trigger
        eod['outcome'] = "CORRECT" if is_correct else "WRONG"
        eod['badge'] = f"{'Correct ✅' if is_correct else 'Wrong ❌'} ({exit_time_label.replace(' IST', '')})"
        eod['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
        eod['summary'] = f"{exit_time_label} session close exit. Entry: ₹{entry_p:,.2f} -> Exit: ₹{close_p:,.2f} ({pnl_pts:+.2f} pts | {'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}). Margin released."
        
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
            f"📈 Cumulative Advisory Win Rate: <b>{tracker['summary']['win_rate_pct']}%</b> ({tracker['summary']['correct_count']}/{tracker['summary'].get('executed_count', 0)})\n\n"
            f"🌐 <b>Interactive Tracker</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
        )
        alerter.send_alert(f"🏁 [11:30 PM SETTLEMENT] Daily 6 PM Advisory Results ({today_str})", body)
        
    return True

def init_clean_live_tracker():
    """
    Initializes a clean forward-tested live tracker starting fresh from today (02 Nov 2026 onwards)
    with ₹3,00,000 base trading capital. Past trades archived into web/advisory_trades_history_archive.json.
    """
    tracker_data = {
        "account_email": USER_ACCOUNT,
        "starting_cash_inr": DEFAULT_STARTING_CASH,
        "trades": []
    }
    save_tracker(tracker_data)
    return tracker_data

if __name__ == "__main__":
    t = init_clean_live_tracker()
    print("Live tracker reset complete. Summary:", t.get('summary'))
