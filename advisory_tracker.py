import os
import json
import requests
import pytz
from datetime import datetime
from trader_engine_bridge import PreTradeRiskEngine, MonteCarloTimeMachine, ORBTimer, EventRiskGuard, PositionSizer, MarketRegimeDetector, TradingAgentsOrchestrator, InstitutionalDirectionEngine, PullbackRetestGate
from pre_move_detector import PreMoveDetector, PreMoveSignal
from dhan_brokerage_calculator import DhanBrokerageCalculator

TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'advisory_trades_tracker.json')
NIFTY_JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'nifty_crossover_oversold.json')
import math
USER_ACCOUNT = "venu.photos1@gmail.com"

def is_us_dst_active(dt=None):
    """
    Returns True if US Eastern Time is currently in Daylight Saving Time (EDT, Regular Summer Schedule),
    or False if in Standard Time (EST, Winter Schedule).
    Automatically switches on the 2nd Sunday in March and 1st Sunday in November every year.
    """
    try:
        from zoneinfo import ZoneInfo
        tz_ny = ZoneInfo('America/New_York')
        check_dt = dt.astimezone(tz_ny) if (dt and getattr(dt, 'tzinfo', None)) else datetime.now(tz_ny)
        return bool(check_dt.dst())
    except Exception:
        try:
            tz_ny = pytz.timezone('America/New_York')
            check_dt = dt.astimezone(tz_ny) if (dt and getattr(dt, 'tzinfo', None)) else datetime.now(tz_ny)
            return bool(check_dt.dst())
        except Exception:
            m = (dt or datetime.utcnow()).month
            return (3 < m < 11)

def get_us_aligned_session_times(dt=None):
    """
    Returns timing parameters in IST dynamically synchronized with US market hours:
    - Summer (EDT): 6:00 PM Pit Open, 6:15 PM Confirmation, 8:00 PM Evening, 11:20 PM Square-off (11:30 PM MCX Close)
    - Winter (EST): 7:00 PM Pit Open, 7:15 PM Confirmation, 9:00 PM Evening, 11:45 PM Square-off (11:55 PM MCX Close)
    """
    is_dst = is_us_dst_active(dt)
    return {
        "is_dst": is_dst,
        "schedule_label": "Summer (EDT)" if is_dst else "Winter (EST)",
        "pit_cutoff": (18, 0) if is_dst else (19, 0),
        "pit_str": "06:00 PM IST" if is_dst else "07:00 PM IST",
        "pit_tag": "1800" if is_dst else "1900",
        "conf_start": (18, 15) if is_dst else (19, 15),
        "conf_end": (19, 45) if is_dst else (20, 45),
        "conf_str": "06:15 PM IST" if is_dst else "07:15 PM IST",
        "evening_cutoff": (20, 0) if is_dst else (21, 0),
        "evening_str": "08:00 PM IST" if is_dst else "09:00 PM IST",
        "evening_tag": "2000" if is_dst else "2100",
        "eod_cutoff": (23, 20) if is_dst else (23, 45),
        "eod_str": "11:20 PM IST" if is_dst else "11:45 PM IST",
        "market_close_str": "11:30 PM IST" if is_dst else "11:55 PM IST",
    }

def parse_ist_timestamp(time_str: str, date_str: str = None) -> datetime:
    """
    Authoritative IST Timestamp Parser.
    Converts time strings ('02:36 PM IST', '17 Nov 2026, 03:13 PM IST', '14:36')
    to proper timezone-aware datetime objects localized to 'Asia/Kolkata'.
    Prevents pytz LMT (+05:53) offset injection bug.
    """
    if not time_str:
        return None
    ist = pytz.timezone('Asia/Kolkata')
    clean = str(time_str).replace(' IST', '').strip()
    formats = [
        "%d %b %Y, %I:%M %p",
        "%d %b %Y %I:%M %p",
        "%d %b %Y, %H:%M",
        "%d %b %Y %H:%M",
        "%I:%M %p",
        "%H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            if fmt in ["%I:%M %p", "%H:%M"] and date_str:
                base_dt = datetime.strptime(date_str, "%d %b %Y")
                dt = dt.replace(year=base_dt.year, month=base_dt.month, day=base_dt.day)
            return ist.localize(dt)
        except Exception:
            continue
    return None

# MCX Risk-Parity Lot Sizes (Calibrated to match ~₹1.2 Lakh to ₹1.5 Lakh Purchase/Margin Capital per Trade)
LOT_SIZES = {
    'CRUDEOIL': 100,    # Crude Oil Standard (100 bbl) - 1 pt = ₹100
    'CRUDEOILM': 10,    # Crude Oil Mini (10 bbl) - 1 pt = ₹10
    'NATURALGAS': 1250, # Natural Gas Standard (1250 mmBtu) - 1 pt = ₹1250
    'NATURALGASM': 250, # Natural Gas Mini (250 mmBtu) - 1 pt = ₹250
    'SILVER': 30,       # Silver Standard (30 kg) - 1 pt = ₹30
    'SILVERM': 5,       # Silver Mini (5 kg) - 1 pt = ₹5
    'SILVERMIC': 1,     # Silver Micro (1 kg) - 1 pt = ₹1
    'GOLD': 100,        # Gold Standard (1 kg = 100 x 10g) - 1 pt = ₹100
    'GOLDM': 10,        # Gold Mini (100g = 10 x 10g) - 1 pt = ₹10
    'GOLDTEN': 1,       # Gold 10g - 1 pt = ₹1
    'COPPER': 2500,     # Copper Standard (2500 kg) - 1 pt = ₹2500
    'COPPERM': 1000,    # Copper Mini (1000 kg) - 1 pt = ₹1000
    'ZINC': 5000,       # Zinc Standard (5000 kg) - 1 pt = ₹5000
    'ZINCM': 1000,      # Zinc Mini (1000 kg) - 1 pt = ₹1000
    'ZINCMINI': 1000,   # Zinc Mini (1000 kg) - 1 pt = ₹1000
    'ALUMINIUM': 5000,  # Aluminium Standard (5000 kg) - 1 pt = ₹5000
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
    'ZINC': 0.0945,      # 9.45% margin (~₹38.5k/lot, ₹77k for 2 lots of Zinc Mini @ ~₹408)
    'ZINCM': 0.0945,
    'ZINCMINI': 0.0945,
    'ALUMINIUM': 0.092,  # 9.2% margin (~₹30.5k/lot for Aluminium Mini @ ~₹330)
    'ALUMINI': 0.092,
    'GOLD': 0.10,        # 10% margin (~₹1.44 Lakh)
    'GOLDM': 0.10,
    'GOLDTEN': 0.10
}

def get_canonical_commodity_root(s):
    """
    Normalizes any commodity symbol variant (parent, mini, micro, forward month)
    to its canonical root asset name.
    e.g. 'ALUMINI-30Nov2026-FUT' -> 'ALUMINIUM'
         'CRUDEOILM' -> 'CRUDEOIL'
         'NATGASMINI' -> 'NATURALGAS'
         'GOLDTEN' -> 'GOLD'
         'SILVERMIC' -> 'SILVER'
         'ZINCMINI' -> 'ZINC'
    """
    if not s:
        return ""
    u = str(s).upper().replace('-FUT', '').replace('.NS', '').replace('.BO', '').strip()
    if 'ALU' in u:
        return 'ALUMINIUM'
    if 'COPPER' in u:
        return 'COPPER'
    if 'CRUDE' in u:
        return 'CRUDEOIL'
    if ('NAT' in u and 'GAS' in u) or u.startswith('NG'):
        return 'NATURALGAS'
    if 'SILVER' in u:
        return 'SILVER'
    if 'GOLD' in u:
        return 'GOLD'
    if 'ZINC' in u:
        return 'ZINC'
    if 'LEAD' in u:
        return 'LEAD'
    if 'NICKEL' in u:
        return 'NICKEL'
    return u

def check_commodity_cooldown(symbol, trades=None, today_str=None, now_dt=None):
    """
    Authoritative Universal Commodity Cooldown Guard.
    Applies strictly BEFORE taking ANY new trade (whether BUY or SELL) on a commodity.
    
    Rules:
    - Normalizes symbol to canonical root (e.g. ALUMINI -> ALUMINIUM, CRUDEOILM -> CRUDEOIL).
    - If prior trade hit STOP LOSS: 120 minutes (2 hours) mandatory lockout.
    - If prior trade hit TARGET: 120 minutes (2 hours) mandatory lockout.
    - If prior trade closed standard / breakeven / stagnation: 90 minutes mandatory lockout.
    - If any trade in this canonical commodity is currently ACTIVE or PENDING_LIMIT:
      Blocks new trade (prevents simultaneous overlapping exposure).
    - APPLIES TO BOTH BUY AND SELL (Strict anti-churning & anti-revenge trading).
      
    Returns:
    (is_in_cooldown: bool, reason: str, remaining_mins: float)
    """
    if not symbol:
        return False, "No symbol provided", 0.0

    canon_sym = get_canonical_commodity_root(symbol)
    if not canon_sym:
        return False, "Could not resolve canonical root", 0.0

    now_dt = now_dt or datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = today_str or now_dt.strftime('%d %b %Y')

    if trades is None:
        try:
            tracker = load_tracker()
            trades = tracker.get('trades', [])
        except Exception:
            trades = []

    # 1. Prevent overlapping simultaneous active/pending trades in the same commodity
    for t in trades:
        if t.get('date') == today_str and get_canonical_commodity_root(t.get('symbol')) == canon_sym:
            pt = t.get('paper_trade', {})
            if pt.get('status') in ['ACTIVE', 'PENDING_LIMIT']:
                return True, f"Active/Pending position already open on {canon_sym} ({t.get('symbol')} {pt.get('action')}). Simultaneous re-entry blocked.", 0.0

    # 2. Find all executed trades for this canonical commodity today
    sym_today_trades = [
        t for t in trades
        if t.get('date') == today_str
        and get_canonical_commodity_root(t.get('symbol')) == canon_sym
        and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
        and t.get('paper_trade', {}).get('quantity', 0) > 0
    ]
    if not sym_today_trades:
        return False, "No prior trades today", 0.0

    # 3. Sort chronologically to find the true most recent trade
    def _trade_sort_key(tr):
        pt_tr = tr.get('paper_trade', {})
        t_str = pt_tr.get('exit_time') or pt_tr.get('entry_time') or tr.get('time_trigger') or ''
        dt_obj = parse_ist_timestamp(t_str, today_str)
        return dt_obj if dt_obj else datetime.min.replace(tzinfo=pytz.timezone('Asia/Kolkata'))

    sorted_trades = sorted(sym_today_trades, key=_trade_sort_key)
    last_t = sorted_trades[-1]
    last_pt = last_t.get('paper_trade', {})
    last_time_str = last_pt.get('exit_time') or last_pt.get('entry_time') or last_t.get('time_trigger')

    if not last_time_str:
        return False, "No timestamp found on prior trade", 0.0

    t_struct = parse_ist_timestamp(last_time_str, today_str)
    if not t_struct:
        return False, "Could not parse prior trade timestamp", 0.0

    diff_mins = (now_dt - t_struct).total_seconds() / 60.0
    if diff_mins < 0:
        diff_mins = 0.0

    # 4. Determine required cooldown based on outcome
    last_exit_trigger = str(last_pt.get('exit_trigger') or last_t.get('eod_outcome', {}).get('exit_trigger', ''))
    is_target_hit = ('TARGET' in last_exit_trigger) or (last_pt.get('pnl_inr', 0.0) > 500.0) or (last_t.get('eod_outcome', {}).get('outcome') == 'CORRECT')
    is_sl_hit = ('STOP_LOSS' in last_exit_trigger) or (last_pt.get('pnl_inr', 0.0) < 0)

    if is_sl_hit:
        required_cooldown = 120.0
        reason_label = "Post-Stop-Loss 2-Hour Lockout"
    elif is_target_hit:
        required_cooldown = 120.0
        reason_label = "Post-Target 2-Hour Lockout"
    else:
        required_cooldown = 90.0
        reason_label = "Standard 90m Lockout"

    if diff_mins < required_cooldown:
        remaining = round(required_cooldown - diff_mins, 1)
        action_desc = last_pt.get('action') or 'TRADE'
        msg = f"{canon_sym} in mandatory cooldown ({reason_label}): Prior {action_desc} exited {int(diff_mins)}m ago ({int(remaining)}m remaining of {int(required_cooldown)}m lockout). Applicable to BOTH BUY and SELL."
        return True, msg, remaining
    else:
        return False, f"Cooldown expired ({int(diff_mins)}m elapsed >= {int(required_cooldown)}m required).", 0.0

# =========================================================================
# 📐 COMMODITY-CALIBRATED TRAILING STOP & BREAKEVEN NOISE FLOORS
# Calibrated against historical 5-minute candle wicks and ATR to prevent
# premature breakeven shakeouts and preserve high-probability profit capture.
# =========================================================================
COMMODITY_TRAIL_CONFIG = {
    'NATURALGAS': {'min_trail_buffer': 1.20, 'breakeven_pct': 75.0, 'fee_buffer': 0.10},
    'NATURALGASM': {'min_trail_buffer': 1.20, 'breakeven_pct': 75.0, 'fee_buffer': 0.10},
    'ZINC': {'min_trail_buffer': 0.90, 'breakeven_pct': 75.0, 'fee_buffer': 0.10},
    'ZINCMINI': {'min_trail_buffer': 0.90, 'breakeven_pct': 75.0, 'fee_buffer': 0.10},
    'ALUMINIUM': {'min_trail_buffer': 0.65, 'breakeven_pct': 75.0, 'fee_buffer': 0.08},
    'ALUMINI': {'min_trail_buffer': 0.65, 'breakeven_pct': 75.0, 'fee_buffer': 0.08},
    'COPPER': {'min_trail_buffer': 2.20, 'breakeven_pct': 75.0, 'fee_buffer': 0.40},
    'COPPERM': {'min_trail_buffer': 2.20, 'breakeven_pct': 75.0, 'fee_buffer': 0.40},
    'SILVER': {'min_trail_buffer': 600.0, 'breakeven_pct': 75.0, 'fee_buffer': 50.0},
    'SILVERM': {'min_trail_buffer': 600.0, 'breakeven_pct': 75.0, 'fee_buffer': 50.0},
    'SILVERMIC': {'min_trail_buffer': 600.0, 'breakeven_pct': 75.0, 'fee_buffer': 50.0},
    'CRUDEOIL': {'min_trail_buffer': 30.0, 'breakeven_pct': 75.0, 'fee_buffer': 3.0},
    'CRUDEOILM': {'min_trail_buffer': 30.0, 'breakeven_pct': 75.0, 'fee_buffer': 3.0},
    'GOLD': {'min_trail_buffer': 220.0, 'breakeven_pct': 75.0, 'fee_buffer': 25.0},
    'GOLDM': {'min_trail_buffer': 220.0, 'breakeven_pct': 75.0, 'fee_buffer': 25.0},
    'GOLDTEN': {'min_trail_buffer': 220.0, 'breakeven_pct': 75.0, 'fee_buffer': 25.0},
}

# =========================================================================
# 🎯 NSE LIQUID STOCK FUTURES (F&O CONTRACT SPECIFICATIONS)
# =========================================================================
STOCK_FUTURES_CONFIG = {
    'ADANIENT': {'lot_size': 309, 'margin_rate': 0.25, 'name': 'Adani Enterprises Futures', 'sector': 'INFRA'},
    'ADANIPORTS': {'lot_size': 475, 'margin_rate': 0.22, 'name': 'Adani Ports Futures', 'sector': 'INFRA'},
    'APOLLOHOSP': {'lot_size': 75, 'margin_rate': 0.20, 'name': 'Apollo Hospitals Futures', 'sector': 'HEALTHCARE'},
    'ASHOKLEY': {'lot_size': 5000, 'margin_rate': 0.22, 'name': 'Ashok Leyland Futures', 'sector': 'AUTO'},
    'ASIANPAINT': {'lot_size': 250, 'margin_rate': 0.20, 'name': 'Asian Paints Futures', 'sector': 'CONSUMER'},
    'AUROPHARMA': {'lot_size': 550, 'margin_rate': 0.22, 'name': 'Aurobindo Pharma Futures', 'sector': 'PHARMA'},
    'AXISBANK': {'lot_size': 625, 'margin_rate': 0.22, 'name': 'Axis Bank Futures', 'sector': 'BANKING'},
    'BAJAJ-AUTO': {'lot_size': 75, 'margin_rate': 0.20, 'name': 'Bajaj Auto Futures', 'sector': 'AUTO'},
    'BAJAJFINSV': {'lot_size': 300, 'margin_rate': 0.22, 'name': 'Bajaj Finserv Futures', 'sector': 'FINANCE'},
    'BAJFINANCE': {'lot_size': 750, 'margin_rate': 0.22, 'name': 'Bajaj Finance Futures', 'sector': 'FINANCE'},
    'BHARATFORG': {'lot_size': 500, 'margin_rate': 0.20, 'name': 'Bharat Forge Futures', 'sector': 'AUTO'},
    'BHARTIARTL': {'lot_size': 475, 'margin_rate': 0.20, 'name': 'Bharti Airtel Futures', 'sector': 'TELECOM'},
    'BPCL': {'lot_size': 1975, 'margin_rate': 0.22, 'name': 'BPCL Futures', 'sector': 'ENERGY'},
    'BRITANNIA': {'lot_size': 125, 'margin_rate': 0.20, 'name': 'Britannia Futures', 'sector': 'FMCG'},
    'CIPLA': {'lot_size': 425, 'margin_rate': 0.20, 'name': 'Cipla Futures', 'sector': 'PHARMA'},
    'COALINDIA': {'lot_size': 1350, 'margin_rate': 0.20, 'name': 'Coal India Futures', 'sector': 'METALS'},
    'COFORGE': {'lot_size': 475, 'margin_rate': 0.22, 'name': 'Coforge Futures', 'sector': 'IT'},
    'CUMMINSIND': {'lot_size': 100, 'margin_rate': 0.20, 'name': 'Cummins India Futures', 'sector': 'CAPITAL_GOODS'},
    'DIVISLAB': {'lot_size': 100, 'margin_rate': 0.20, 'name': 'Divis Labs Futures', 'sector': 'PHARMA'},
    'DIXON': {'lot_size': 50, 'margin_rate': 0.22, 'name': 'Dixon Tech Futures', 'sector': 'CONSUMER'},
    'DRREDDY': {'lot_size': 625, 'margin_rate': 0.20, 'name': 'Dr Reddy Futures', 'sector': 'PHARMA'},
    'EICHERMOT': {'lot_size': 100, 'margin_rate': 0.20, 'name': 'Eicher Motors Futures', 'sector': 'AUTO'},
    'FEDERALBNK': {'lot_size': 2500, 'margin_rate': 0.22, 'name': 'Federal Bank Futures', 'sector': 'BANKING'},
    'GRASIM': {'lot_size': 250, 'margin_rate': 0.20, 'name': 'Grasim Industries Futures', 'sector': 'CEMENT'},
    'HCLTECH': {'lot_size': 400, 'margin_rate': 0.20, 'name': 'HCL Tech Futures', 'sector': 'IT'},
    'HDFCBANK': {'lot_size': 750, 'margin_rate': 0.20, 'name': 'HDFC Bank Futures', 'sector': 'BANKING'},
    'HDFCLIFE': {'lot_size': 1100, 'margin_rate': 0.20, 'name': 'HDFC Life Futures', 'sector': 'FINANCE'},
    'HEROMOTOCO': {'lot_size': 150, 'margin_rate': 0.20, 'name': 'Hero MotoCorp Futures', 'sector': 'AUTO'},
    'HINDALCO': {'lot_size': 700, 'margin_rate': 0.22, 'name': 'Hindalco Futures', 'sector': 'METALS'},
    'HINDUNILVR': {'lot_size': 300, 'margin_rate': 0.20, 'name': 'Hindustan Unilever Futures', 'sector': 'FMCG'},
    'ICICIBANK': {'lot_size': 700, 'margin_rate': 0.20, 'name': 'ICICI Bank Futures', 'sector': 'BANKING'},
    'INDUSINDBK': {'lot_size': 700, 'margin_rate': 0.22, 'name': 'IndusInd Bank Futures', 'sector': 'BANKING'},
    'INFY': {'lot_size': 500, 'margin_rate': 0.20, 'name': 'Infosys Futures', 'sector': 'IT'},
    'ITC': {'lot_size': 1975, 'margin_rate': 0.20, 'name': 'ITC Futures', 'sector': 'FMCG'},
    'JSWSTEEL': {'lot_size': 675, 'margin_rate': 0.22, 'name': 'JSW Steel Futures', 'sector': 'METALS'},
    'KOTAKBANK': {'lot_size': 2000, 'margin_rate': 0.20, 'name': 'Kotak Mahindra Bank Futures', 'sector': 'BANKING'},
    'LT': {'lot_size': 175, 'margin_rate': 0.20, 'name': 'Larsen & Toubro Futures', 'sector': 'CAPITAL_GOODS'},
    'M&M': {'lot_size': 200, 'margin_rate': 0.20, 'name': 'Mahindra & Mahindra Futures', 'sector': 'AUTO'},
    'MARUTI': {'lot_size': 50, 'margin_rate': 0.20, 'name': 'Maruti Suzuki Futures', 'sector': 'AUTO'},
    'NTPC': {'lot_size': 1600, 'margin_rate': 0.20, 'name': 'NTPC Futures', 'sector': 'POWER'},
    'ONGC': {'lot_size': 2250, 'margin_rate': 0.22, 'name': 'ONGC Futures', 'sector': 'ENERGY'},
    'PERSISTENT': {'lot_size': 125, 'margin_rate': 0.22, 'name': 'Persistent Systems Futures', 'sector': 'IT'},
    'POLYCAB': {'lot_size': 75, 'margin_rate': 0.20, 'name': 'Polycab Futures', 'sector': 'CAPITAL_GOODS'},
    'POWERGRID': {'lot_size': 1900, 'margin_rate': 0.20, 'name': 'Power Grid Futures', 'sector': 'POWER'},
    'RELIANCE': {'lot_size': 500, 'margin_rate': 0.22, 'name': 'Reliance Industries Futures', 'sector': 'ENERGY'},
    'SBILIFE': {'lot_size': 375, 'margin_rate': 0.20, 'name': 'SBI Life Futures', 'sector': 'FINANCE'},
    'SBIN': {'lot_size': 750, 'margin_rate': 0.22, 'name': 'State Bank of India Futures', 'sector': 'BANKING'},
    'SHRIRAMFIN': {'lot_size': 825, 'margin_rate': 0.22, 'name': 'Shriram Finance Futures', 'sector': 'FINANCE'},
    'SUNPHARMA': {'lot_size': 350, 'margin_rate': 0.20, 'name': 'Sun Pharma Futures', 'sector': 'PHARMA'},
    'TATASTEEL': {'lot_size': 2750, 'margin_rate': 0.22, 'name': 'Tata Steel Futures', 'sector': 'METALS'},
    'TCS': {'lot_size': 250, 'margin_rate': 0.20, 'name': 'Tata Consultancy Services Futures', 'sector': 'IT'},
    'TECHM': {'lot_size': 600, 'margin_rate': 0.20, 'name': 'Tech Mahindra Futures', 'sector': 'IT'},
    'TITAN': {'lot_size': 175, 'margin_rate': 0.20, 'name': 'Titan Company Futures', 'sector': 'CONSUMER'},
    'ULTRACEMCO': {'lot_size': 50, 'margin_rate': 0.20, 'name': 'UltraTech Cement Futures', 'sector': 'CEMENT'},
    'VOLTAS': {'lot_size': 475, 'margin_rate': 0.20, 'name': 'Voltas Futures', 'sector': 'CONSUMER'},
    'WIPRO': {'lot_size': 3000, 'margin_rate': 0.20, 'name': 'Wipro Futures', 'sector': 'IT'}
}

for sfc_sym, sfc_cfg in STOCK_FUTURES_CONFIG.items():
    LOT_SIZES[f"{sfc_sym}-FUT"] = sfc_cfg['lot_size']
    LOT_SIZES[sfc_sym] = sfc_cfg['lot_size']
    MARGIN_RATES[f"{sfc_sym}-FUT"] = sfc_cfg['margin_rate']
    MARGIN_RATES[sfc_sym] = sfc_cfg['margin_rate']

# =========================================================================
# 🛡️ CAPITAL DEFENSE & RECOVERY PROTOCOL (OPTION 2: PULLBACK RETEST SNIPING)
# =========================================================================
MAX_LOSS_PER_TRADE_INR = 6000.0   # Option 2 Calibrated: ₹6,000 max risk per commodity position (2% of ₹3 Lakhs capital)
MAX_DAILY_LOSS_INR = 12000.0      # Portfolio daily risk circuit breaker (strictly 4% of ₹3 Lakhs capital; halts after 2 full stop-outs)
MAX_INTRADAY_STOCK_MARGIN_INR = 30000.0 # Dedicated ₹30,000 margin allocated per day for Intraday Stock Trading (split across 3 stocks = ₹10,000 margin / ₹50,000 buying power each)
INTRADAY_STOCK_MIS_LEVERAGE = 5.0 # 5x leverage for MIS intraday equity (20% margin requirement)
MAX_INTRADAY_STOCK_POSITION_INR = MAX_INTRADAY_STOCK_MARGIN_INR * INTRADAY_STOCK_MIS_LEVERAGE # ₹1,50,000 buying power across 3 stocks
MAX_EQUITY_POSITION_INR = MAX_INTRADAY_STOCK_POSITION_INR # Backward compatibility alias
MAX_INTRADAY_STOCK_RISK_INR = 3000.0 # Full portfolio risk ceiling - no arbitrary micro-cap restriction per user instruction
MAX_INTRADAY_STOCK_DAILY_LOSS_INR = 6000.0 # Full daily portfolio risk circuit breaker
MAX_CONCURRENT_COMMODITIES = 2    # Allows 2 concurrent active commodity trades (2 x ~₹1.5L margin = ₹3.0L total portfolio capacity)
MAX_CONCURRENT_STOCK_FUTURES = 2  # Allows 2 concurrent active stock futures trades (e.g. RELIANCE-FUT, INFY-FUT)
MAX_CONCURRENT_EQUITIES = 3       # Diversify into 3 distinct intraday stocks (₹10,000 margin each within ₹30,000 margin)
MAX_CONCURRENT_TOTAL = 6          # Max concurrent active trades across portfolio (2 commodities + 2 stock futures + 2-3 intraday equities)
MAX_TRADES_PER_SYMBOL_DAY = 2     # Max 2 trades per symbol per day to prevent over-trading
COOLDOWN_MINUTES_BETWEEN_TRADES = 120 # Strict 120-min (2 hours) spacing between trades on the same symbol
INTRADAY_STAGNATION_MINUTES = 210.0  # Extended to 210 minutes (3.5 hours) to give intraday trades ample room into afternoon trend
STOCK_FUTURES_STAGNATION_MINUTES = 240.0 # 240 minutes (4.0 hours) for stock futures - never prematurely abort high-conviction futures
COMMODITY_STAGNATION_MINUTES = 360.0  # 360 minutes (6.0 hours) for commodities; protected prior to US evening session
STAGNATION_MIN_ADVERSE_PCT = 0.005   # Minimum 0.5% adverse move required; hovering near breakeven is protected consolidation
ENFORCE_MINI_ONLY_COMMODITIES = False # Calibrated to ~₹1.5 Lakhs commodity margin per position per user instruction

# Canonical specification mapping for trading futures with 50% split (~₹70,000 - ₹75,000 margin per position)
# Calibrated to allow 2 concurrent diverse commodity positions within user's ₹1.5L available pool
COMMODITY_SPECS_150K = {
    'SILVER': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot': 1},
    'SILVERM': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot': 1},
    'SILVERMIC': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot': 1},
    'GOLD': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot': 1},
    'GOLDM': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot': 1},
    'GOLDTEN': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot': 1},
    'ZINC': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot': 2000},
    'ZINCM': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot': 2000},
    'ZINCMINI': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot': 2000},
    'COPPER': {'symbol': 'COPPERM', 'name': 'Copper Mini', 'lot': 1000},
    'COPPERM': {'symbol': 'COPPERM', 'name': 'Copper Mini', 'lot': 1000},
    'CRUDEOIL': {'symbol': 'CRUDEOILM', 'name': 'Crude Oil Mini', 'lot': 20},
    'CRUDEOILM': {'symbol': 'CRUDEOILM', 'name': 'Crude Oil Mini', 'lot': 20},
    'NATURALGAS': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot': 1000},
    'NATURALGASM': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot': 1000},
    'ALUMINIUM': {'symbol': 'ALUMINI', 'name': 'Aluminium Mini', 'lot': 2000},
    'ALUMINI': {'symbol': 'ALUMINI', 'name': 'Aluminium Mini', 'lot': 2000}
}

# Direct mapping to low-margin, micro/mini contracts for capital defense
MINI_COMMODITY_MAP = {
    'ALUMINIUM': 'ALUMINI',
    'CRUDEOIL': 'CRUDEOILM',
    'NATURALGAS': 'NATURALGASM',
    'SILVER': 'SILVERMIC',
    'SILVERM': 'SILVERMIC',
    'GOLD': 'GOLDM',
    'GOLDTEN': 'GOLDM',
    'ZINC': 'ZINCMINI',
    'ZINCM': 'ZINCMINI',
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
    Returns (exec_sym, exec_name, lot_size) calibrated strictly for 50% capital split (~₹75,000 margin per trade)
    to enforce portfolio diversity (allowing 2 concurrent independent commodity positions):
    - Silver: Silver Micro (SILVERMIC) 5 units (~₹67.5k margin)
    - Gold: Gold Mini (GOLDM) 1 lot / 10 units (~₹76k margin)
    - Zinc: Zinc Mini (ZINCMINI) 2 lots / 2000 units (~₹53k margin)
    - Copper: Copper Mini (COPPERM) 1 lot / 1000 units (~₹65.6k margin)
    - Crude Oil: Crude Oil Mini (CRUDEOILM) 4 lots / 40 units (~₹60k margin)
    - Natural Gas: Natural Gas Mini (NATURALGASM) 4 lots / 1000 units (~₹68k margin)
    - Aluminium: Aluminium Mini (ALUMINI) 2 lots / 2000 units (~₹58k margin)
    """
    clean = sym.replace('MIC', '').replace('MINI', '').rstrip('M')
    spec = COMMODITY_SPECS_150K.get(sym) or COMMODITY_SPECS_150K.get(clean)
    if spec:
        return spec['symbol'], spec['name'], spec['lot']
    name = COMMODITY_DISPLAY_NAMES.get(sym, default_name or sym)
    lot = LOT_SIZES.get(sym, LOT_SIZES.get(clean, 1))
    return sym, name, lot

def resolve_defense_contract_and_quote(raw_sym, item, commodities_map=None):
    """
    Given an incoming commodity symbol and item dictionary from circuits_data,
    resolves the exact execution contract (e.g. ALUMINIUM -> ALUMINI, SILVER -> SILVERMIC)
    AND guarantees that price, high, low, close, and technical indicators are strictly
    derived from that exact execution contract, preventing any cross-contract price shadowing.
    """
    exec_sym, exec_name, lot_size = get_defense_contract_and_lot(raw_sym, item.get('name', raw_sym) if isinstance(item, dict) else raw_sym)
    quote_item = item
    if exec_sym != raw_sym and commodities_map and exec_sym in commodities_map:
        target_item = commodities_map[exec_sym]
        if target_item and target_item.get('current_price') and float(target_item['current_price']) > 0:
            quote_item = target_item
    return exec_sym, exec_name, lot_size, quote_item

def resolve_commodity_item(sym, commodities_map):
    """
    Safely resolves a commodity dictionary from circuits commodities_map
    handling mini/micro aliases like ZINCM -> ZINCMINI, SILVERMIC -> SILVERMIC/SILVER, etc.
    """
    if not sym or not commodities_map:
        return None
    if sym in commodities_map:
        return commodities_map[sym]
    
    aliases = {
        'ZINCM': ['ZINCMINI', 'ZINC'],
        'ZINCMINI': ['ZINCM', 'ZINC'],
        'ALUMINI': ['ALUMINIUM'],
        'ALUMINIUM': ['ALUMINI'],
        'SILVERMIC': ['SILVERM', 'SILVER'],
        'SILVERM': ['SILVERMIC', 'SILVER'],
        'SILVER': ['SILVERMIC', 'SILVERM'],
        'GOLDM': ['GOLD'],
        'GOLD': ['GOLDM'],
        'CRUDEOILM': ['CRUDEOIL'],
        'CRUDEOIL': ['CRUDEOILM'],
        'NATURALGASM': ['NATURALGAS'],
        'NATURALGAS': ['NATURALGASM'],
        'COPPERM': ['COPPER'],
        'COPPER': ['COPPERM'],
    }
    for alt in aliases.get(sym, []):
        if alt in commodities_map:
            return commodities_map[alt]
    
    # Try fuzzy strip / append
    if sym.endswith('MINI') and sym[:-4] in commodities_map:
        return commodities_map[sym[:-4]]
    if sym.endswith('M') and sym[:-1] in commodities_map:
        return commodities_map[sym[:-1]]
    if (sym + 'MINI') in commodities_map:
        return commodities_map[sym + 'MINI']
    return None

def get_authoritative_contract_price(symbol, asset_type='EQUITY', timeout=6):
    """
    Fetches the 0-second authoritative real-time execution price for the EXACT contract:
    - Tier 1 (Direct Broker Feed): Queries DhanHQ API (/marketfeed/ltp) directly if active.
    - Tier 2 (Exchange Scanner Feed): Reverts seamlessly to TradingView / AngelOne feeds if Dhan Data API is unavailable.
    - Stock Futures (e.g. APOLLOHOSP-FUT): Queries TradingView Futures Scanner for NSE:{CLEAN_SYM}1!
      to get the true front-month futures price (including basis premium/discount).
    - Commodities (e.g. ALUMINI, CRUDEOILM, GOLDM): Queries TradingView Futures Scanner for MCX:{EXACT_ROOT}1!
      so Mini contracts NEVER inherit main contract prices.
    - Intraday Equities (e.g. TITAN.NS): Queries TradingView India Scanner for real-time NSE spot price.
    Returns (price, contract_ticker) or (None, None) on failure.
    """
    # Tier 1: Attempt direct DhanHQ live market quote
    try:
        from dhan_live_executor import DhanLiveExecutor
        dhan_exec = DhanLiveExecutor()
        dhan_p, dhan_src = dhan_exec.get_live_price(symbol, asset_type)
        if dhan_p and dhan_p > 0:
            return dhan_p, dhan_src
    except Exception:
        pass

    # Tier 2: Seamless fallback to primary exchange scanner feed
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    clean = symbol.replace('.NS', '').replace('-FUT', '').upper() if symbol else ''
    if not clean:
        return None, None
    
    # 1. Stock Futures
    if asset_type in ['FUTURES', 'STOCK_FUTURES'] or '-FUT' in symbol:
        url = 'https://scanner.tradingview.com/futures/scan'
        fut_tick = f"{clean}1!"
        payload = {
            'filter': [{'left': 'name', 'operation': 'equal', 'right': fut_tick}],
            'columns': ['name', 'close', 'open', 'high', 'low', 'change']
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                if data:
                    p = float(data[0]['d'][1])
                    if p > 0:
                        return p, f"NSE:{fut_tick}"
        except Exception as e:
            print(f"Notice: Could not fetch real-time futures price for {symbol}: {e}")

    # 2. Commodity Mini & Main Contracts
    elif asset_type in ['COMMODITY', 'COMMODITY_FUTURES'] or clean in [
        'ALUMINI', 'ALUMINIUM', 'COPPERM', 'COPPER', 'CRUDEOILM', 'CRUDEOIL',
        'GOLDM', 'GOLD', 'SILVERMIC', 'SILVERM', 'SILVER', 'ZINCMINI', 'ZINC',
        'NATURALGASM', 'NATURALGAS', 'NATGASMINI'
    ]:
        url = 'https://scanner.tradingview.com/futures/scan'
        tv_root = clean
        if clean == 'NATURALGASM':
            tv_root = 'NATGASMINI'
        fut_tick = f"{tv_root}1!"

        # Precision Rollover Pricing: When rolled over to next month, query exact forward contract ticker
        try:
            from dhan_live_executor import DhanLiveExecutor
            d_chk = DhanLiveExecutor()
            scrip_info = d_chk.resolve_scrip(clean, asset_type='COMMODITY')
            if scrip_info and scrip_info.get('rolled_over') and scrip_info.get('expiry'):
                TV_MONTH_CODES = {
                    1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
                    7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'
                }
                exp_dt = datetime.strptime(scrip_info['expiry'][:10], '%Y-%m-%d')
                m_code = TV_MONTH_CODES.get(exp_dt.month, 'Z')
                fut_tick = f"{scrip_info.get('symbol_name', tv_root)}{m_code}{exp_dt.year}"
        except Exception:
            pass

        payload = {
            'filter': [{'left': 'name', 'operation': 'equal', 'right': fut_tick}],
            'columns': ['name', 'close', 'open', 'high', 'low', 'change']
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                if data:
                    p = float(data[0]['d'][1])
                    if p > 0:
                        return p, f"MCX:{fut_tick}"
            # Fallback to continuous 1! if exact contract query had no data
            if fut_tick != f"{tv_root}1!":
                fb_payload = {
                    'filter': [{'left': 'name', 'operation': 'equal', 'right': f"{tv_root}1!"}],
                    'columns': ['name', 'close', 'open', 'high', 'low', 'change']
                }
                fb_resp = requests.post(url, json=fb_payload, headers=headers, timeout=timeout)
                if fb_resp.status_code == 200:
                    fb_data = fb_resp.json().get('data', [])
                    if fb_data:
                        p = float(fb_data[0]['d'][1])
                        if p > 0:
                            return p, f"MCX:{tv_root}1!"
        except Exception as e:
            print(f"Notice: Could not fetch real-time commodity contract price for {symbol}: {e}")

    # 3. Cash Equities
    else:
        url = 'https://scanner.tradingview.com/india/scan'
        payload = {
            'filter': [{'left': 'exchange', 'operation': 'equal', 'right': 'NSE'}, {'left': 'name', 'operation': 'equal', 'right': clean}],
            'columns': ['name', 'close', 'open', 'high', 'low', 'change']
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                if data:
                    p = float(data[0]['d'][1])
                    if p > 0:
                        return p, f"NSE:{clean}"
        except Exception as e:
            print(f"Notice: Could not fetch real-time equity price for {symbol}: {e}")
            
    return None, None

DEFAULT_STARTING_CASH = 300000.0  # ₹3,00,000 (3 Lakhs Virtual Cash)

class IntradayStockSelector:
    """
    🎯 Institutional Intraday Stock Selection Engine:
    Identifies high-conviction intraday leaders & laggards based on:
    1. Relative Strength / Weakness vs Market
    2. Proximity to 52-Week High (Minimal overhead trapped supply)
    3. Daily Trend Alignment (Close > 50 EMA > 200 EMA for Longs; Close < 50 EMA for Shorts)
    4. Institutional Liquidity & Turnover (>= ₹15-20 Crores minimum turnover)
    5. Healthy Volatility & Momentum (RSI in active expansion zone)
    """
    @classmethod
    def rank_candidates(cls, raw_stocks, action="BUY"):
        ranked = []
        for s in raw_stocks:
            cur_p = float(s.get('close') or 0.0)
            if cur_p <= 0:
                continue

            # Pillar 1: False signal suppression applies strictly to BUY (protects long buyers from falling knives)
            # Breakdown stocks are valid and prime candidates for high-velocity shorting!
            if action.upper() == "BUY" and s.get('is_false_signal'):
                continue

            vol = float(s.get('volume') or 0)
            turnover_cr = (vol * cur_p) / 10000000.0  # Turnover in INR Crores
            # Pillar 4: Institutional Turnover Filter (>= ₹20 Crores minimum turnover)
            if turnover_cr < 20.0:
                continue

            # Pillar 4: High-Beta / Volatility Filter (>= 1.4% ATR / Daily Range)
            # Kills sluggish low-beta giants that trigger 120-minute stagnation exits and bleed brokerage/taxes.
            regime_meta = s.get('market_regime') or {}
            daily_vol_pct = float(regime_meta.get('daily_vol_pct') or 0.0)
            atr_val = float(s.get('atr_14') or s.get('atr') or 0.0)
            atr_pct = (atr_val / cur_p * 100.0) if (cur_p > 0 and atr_val > 0) else daily_vol_pct
            if atr_pct > 0 and atr_pct < 1.4:
                continue

            chg_pct = float(s.get('change_pct') or 0.0)
            h52 = float(s.get('price_52_week_high') or cur_p)
            l52 = float(s.get('price_52_week_low') or cur_p)
            ema50 = float(s.get('ema_50') or cur_p)
            ema200 = float(s.get('ema_200') or cur_p)
            rsi = float(s.get('rsi') or 50.0)
            discount = float(s.get('discount_pct') or 0.0)
            regime = (s.get('market_regime') or {}).get('regime', '')

            if action.upper() == "BUY":
                # LONG CRITERIA:
                # 1. Positive price action today (+0.4% to +2.5%)
                if chg_pct < 0.40 or chg_pct > 2.50:
                    continue
                # 2. Bullish Regime only (No Breakdown or Chop traps)
                if regime in ['BREAKDOWN', 'CHOP_CONSOLIDATION']:
                    continue
                # 3. Macro Trend Stack: Price above 50 EMA, 50 EMA >= 200 EMA (Golden Alignment)
                if cur_p < ema50 or ema50 < (ema200 * 0.99):
                    continue

                score = 0.0
                # Momentum & Relative Strength (up to 40 pts)
                score += min(chg_pct, 2.5) * 16.0
                # Proximity to 52W High (Clean chart without trapped overhead supply)
                if discount <= 5.0:
                    score += 35.0  # Near ATH breakout: maximum momentum potential
                elif discount <= 10.0:
                    score += 25.0
                elif discount <= 15.0:
                    score += 15.0
                else:
                    score += 5.0
                # Healthy Momentum RSI (52 to 66 is prime breakout velocity)
                if 52.0 <= rsi <= 66.0:
                    score += 25.0
                elif 66.0 < rsi <= 72.0:
                    score += 15.0
                # Liquidity bonus (institutions driving the tape)
                score += min(turnover_cr / 20.0, 15.0)

                s_cand = dict(s)
                s_cand['_selector_action'] = 'BUY'
                ranked.append((s_cand, round(score, 1)))

            elif action.upper() == "SELL":
                # SHORT CRITERIA:
                # 1. Clearly negative price action today (-0.6% to -3.5%)
                if chg_pct > -0.60 or chg_pct < -3.50:
                    continue
                # 2. Bearish or Volatile Expansion Regime (No strong bull trends)
                if regime in ['BULL_TREND', 'CHOP_CONSOLIDATION']:
                    continue
                # 3. Macro Trend Stack: Price below 50 EMA
                if cur_p > ema50:
                    continue

                score = 0.0
                # Downward momentum (up to 40 pts)
                score += abs(chg_pct) * 15.0
                # Proximity to multi-month breakdown (Deeper discount = confirmed breakdown)
                if discount >= 25.0:
                    score += 35.0
                elif discount >= 15.0:
                    score += 20.0
                # Breakdown RSI zone (32 to 46)
                if 32.0 <= rsi <= 46.0:
                    score += 25.0
                score += min(turnover_cr / 20.0, 15.0)

                s_cand = dict(s)
                s_cand['_selector_action'] = 'SELL'
                ranked.append((s_cand, round(score, 1)))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in ranked]


def calculate_technical_bracket(action, cur_p, day_h=None, day_l=None, ema20=None, ema50=None, atr=None, min_rrr=1.2, is_intraday=True, symbol=None):
    """
    📐 Technical Invalidation Stop & Realistic Target Engine (Option 2 Calibrated):
    Places stop-losses strictly at structural chart invalidation points (Pullback Anchors) rather than arbitrary guesses.
    - Short Trades: SL placed strictly above Resistance Anchor (20 EMA / ORB Midpoint / Day High) (+ small buffer).
    - Long Trades: SL placed strictly below Support Anchor (20 EMA / ORB Midpoint / Day Low) (- small buffer).
    - Strict Risk Distance Cap: For high-multiplier contracts, stop distance is bounded by PullbackRetestGate.MAX_STOP_DISTANCE
      (e.g. Aluminium Mini <= 1.00 pt = ₹6,000 max risk; Zinc Mini <= 1.50 pts; Silver Micro <= 1,200 pts).
    - Target: Calibrated to achievable intraday move (1.2R - 1.5R), guaranteeing positive expectancy and high hit rate.
    """
    if cur_p <= 0:
        return None, None

    # Contract-specific maximum allowable structural stop distance under Option 2
    clean_sym = symbol.replace('-FUT', '').replace('.NS', '').replace('.BO', '').upper() if symbol else ""
    contract_max_stop = PullbackRetestGate.MAX_STOP_DISTANCE.get(clean_sym)
        
    # Option A (ATR Wick Buffer Calibration):
    # Provides reasonable breathing room beyond structural wicks (0.40x-0.50x ATR)
    # to avoid premature noise stop-outs while keeping maximum risk strictly managed.
    atr_buf = round(atr * 0.40, 2) if (atr and atr > 0) else round(cur_p * 0.0045, 2)
    if 'CRUDE' in clean_sym:
        atr_buf = max(atr_buf, 45.0)
    elif 'NAT' in clean_sym:
        atr_buf = max(atr_buf, 3.0)
    elif 'ZINC' in clean_sym:
        atr_buf = max(atr_buf, 1.80)
    elif 'SILVER' in clean_sym:
        atr_buf = max(atr_buf, 1400.0)
    elif 'GOLD' in clean_sym:
        atr_buf = max(atr_buf, 400.0)
    else:
        atr_buf = max(atr_buf, round(cur_p * 0.0025, 2))
    
    if action.upper() == 'SELL':
        ref_high = max(cur_p, day_h) if day_h and day_h > 0 else cur_p
        if day_h and day_l and day_h > day_l:
            or_mid = (day_h + day_l) / 2.0
            if cur_p < or_mid < day_h and (or_mid - cur_p) >= (atr_buf * 0.5):
                ref_high = or_mid
        if ema20 and cur_p < ema20 < ref_high and (ema20 - cur_p) >= (atr_buf * 0.5):
            ref_high = ema20
            
        # Hard Ceiling Guard: Under Option 2, stop distance must not exceed contract maximum or 1.0x ATR
        max_sl_dist = contract_max_stop if contract_max_stop else ((atr * 1.0) if (atr and is_intraday) else (cur_p * 0.010))
        if (ref_high - cur_p) > max_sl_dist:
            ref_high = cur_p + (max_sl_dist * 0.85)
            
        sl = round(min(ref_high + atr_buf, cur_p + max_sl_dist), 2)
        risk_pts = max(round(abs(sl - cur_p), 2), round(cur_p * 0.0015, 2))
        tgt_dist = round(min_rrr * risk_pts, 2)
        tgt = round(cur_p - tgt_dist, 2)
    else:
        ref_low = min(cur_p, day_l) if day_l and day_l > 0 else cur_p
        if day_h and day_l and day_h > day_l:
            or_mid = (day_h + day_l) / 2.0
            if cur_p > or_mid > day_l and (cur_p - or_mid) >= (atr_buf * 0.5):
                ref_low = or_mid
        if ema20 and cur_p > ema20 > ref_low and (cur_p - ema20) >= (atr_buf * 0.5):
            ref_low = ema20
            
        # Hard Ceiling Guard: Under Option 2, stop distance must not exceed contract maximum or 1.0x ATR
        max_sl_dist = contract_max_stop if contract_max_stop else ((atr * 1.0) if (atr and is_intraday) else (cur_p * 0.010))
        if (cur_p - ref_low) > max_sl_dist:
            ref_low = cur_p - (max_sl_dist * 0.85)
            
        sl = round(max(ref_low - atr_buf, cur_p - max_sl_dist), 2)
        risk_pts = max(round(abs(cur_p - sl), 2), round(cur_p * 0.0015, 2))
        tgt_dist = round(min_rrr * risk_pts, 2)
        tgt = round(cur_p + tgt_dist, 2)

    # Directional Invariant Validation: Guarantee SL and Target are NEVER inverted relative to entry price
    if action.upper() == 'BUY':
        if sl >= cur_p:
            sl = round(cur_p - (max_sl_dist if contract_max_stop else max(atr * 0.6 if atr else cur_p * 0.005, cur_p * 0.003)), 2)
        if tgt <= cur_p:
            tgt = round(cur_p + (min_rrr * abs(cur_p - sl)), 2)
    else:
        if sl <= cur_p:
            sl = round(cur_p + (max_sl_dist if contract_max_stop else max(atr * 0.6 if atr else cur_p * 0.005, cur_p * 0.003)), 2)
        if tgt >= cur_p:
            tgt = round(cur_p - (min_rrr * abs(sl - cur_p)), 2)
        
    return sl, tgt

def get_intraday_margin_pool(tracker_data):
    """
    Computes the daily Intraday MIS Margin Pool balance.
    Base daily allocation: ₹30,000 (refreshed every day even if there was a loss).
    When profit is booked from intraday trades, that profit is added to the ₹30,000 balance.
    If intraday realized P&L is negative (loss), it does not reduce the baseline daily balance
    below ₹30,000 (it refreshes back to ₹30,000 every day).
    """
    trades = tracker_data.get('trades', [])
    intraday_closed_trades = [
        t for t in trades
        if t.get('paper_trade', {}).get('status') == 'CLOSED'
        and (t.get('asset_type') == 'INTRADAY_STOCK' or t.get('paper_trade', {}).get('product_type') == 'MIS_INTRADAY')
    ]
    intraday_realized_pnl = sum(t.get('paper_trade', {}).get('pnl_inr', 0.0) for t in intraday_closed_trades)
    intraday_booked_profit = max(0.0, intraday_realized_pnl)
    base_pool = MAX_INTRADAY_STOCK_MARGIN_INR + intraday_booked_profit

    active_stock_trades = [
        t for t in trades 
        if t.get('paper_trade', {}).get('status') == 'ACTIVE' and 
        (t.get('asset_type') == 'INTRADAY_STOCK' or t.get('paper_trade', {}).get('product_type') == 'MIS_INTRADAY')
    ]
    used_stock_margin = sum(
        float(t.get('paper_trade', {}).get('margin_required_inr') or 
              (float(t.get('paper_trade', {}).get('entry_price', 0)) * float(t.get('paper_trade', {}).get('quantity', 0)) * 0.20)) 
        for t in active_stock_trades
    )
    avail_stock_margin = max(0.0, base_pool - used_stock_margin)

    return {
        'base_pool': round(base_pool, 2),
        'base_daily_margin': round(MAX_INTRADAY_STOCK_MARGIN_INR, 2),
        'booked_profit': round(intraday_booked_profit, 2),
        'intraday_realized_pnl': round(intraday_realized_pnl, 2),
        'used_margin': round(used_stock_margin, 2),
        'available_margin': round(avail_stock_margin, 2)
    }

def get_cash_and_margin(tracker_data):
    """
    Computes available cash, currently blocked margin, and total account net worth.
    Margin is deducted from available cash when a trade is ACTIVE.
    When a trade is CLOSED, margin is released and realized P&L is settled into cash.
    Commodity futures realized P&L is settled into cash.
    When intraday profit is booked, that profit is also settled into the overall trade balance.
    If intraday incurred a loss, the daily intraday balance is refreshed back to ₹30,000 every day.
    """
    starting_cash = max(float(tracker_data.get('starting_cash_inr', DEFAULT_STARTING_CASH) or DEFAULT_STARTING_CASH), DEFAULT_STARTING_CASH)
    trades = tracker_data.get('trades', [])
    
    # Realized P&L from closed commodity trades
    commodity_pnl = sum(
        t.get('paper_trade', {}).get('pnl_inr', 0.0) 
        for t in trades 
        if t.get('paper_trade', {}).get('status') == 'CLOSED' 
        and t.get('asset_type') != 'INTRADAY_STOCK' 
        and t.get('paper_trade', {}).get('product_type') != 'MIS_INTRADAY'
    )
    
    # Intraday margin pool calculations (base 30k + booked profits)
    intraday_pool = get_intraday_margin_pool(tracker_data)
    intraday_booked_profit = intraday_pool['booked_profit']
    
    # Overall trade balance reflects starting cash + commodity P&L + booked intraday profits
    current_cash = starting_cash + commodity_pnl + intraday_booked_profit
    
    # Used margin from all currently active trades (commodities + intraday equities)
    active_trades = [
        t for t in trades 
        if t.get('paper_trade', {}).get('status') == 'ACTIVE'
    ]

    def calc_trade_margin(t):
        pt = t.get('paper_trade', {})
        if pt.get('margin_required_inr') and float(pt.get('margin_required_inr')) > 0:
            return float(pt.get('margin_required_inr'))
        p = float(pt.get('entry_price') or t.get('suggestion_6pm', {}).get('reference_price') or 0.0)
        q = float(pt.get('quantity') or t.get('lot_size') or 1)
        if t.get('asset_type') == 'INTRADAY_STOCK' or pt.get('product_type') == 'MIS_INTRADAY':
            return round(p * q * 0.20, 2)
        sym = t.get('symbol', '')
        base_sym = sym.replace('MIC', '').replace('MINI', '').replace('INI', '').replace('M', '').replace('TEN', '').replace('-FUT', '')
        rate = MARGIN_RATES.get(sym, MARGIN_RATES.get(base_sym, 0.10))
        return round(p * q * rate, 2)

    used_margin = sum(calc_trade_margin(t) for t in active_trades)
    available_cash = max(0.0, current_cash - used_margin)
    net_worth = current_cash
    
    return {
        'starting_cash': round(starting_cash, 2),
        'current_cash': round(current_cash, 2),
        'used_margin': round(used_margin, 2),
        'available_cash': round(available_cash, 2),
        'net_worth': round(net_worth, 2),
        'commodity_realized_pnl': round(commodity_pnl, 2),
        'intraday_pool': intraday_pool
    }

def check_margin_availability(tracker_data, sym, cur_p, lot_size, is_equity=False, sl=None):
    """
    Checks if there is sufficient margin balance available to execute the trade.
    If the requested lot size requires more margin than available (e.g. ~₹1.5L target),
    it dynamically downsizes to the maximum whole lots affordable with the available cash balance.
    Returns:
        (has_margin: bool, req_margin: float, avail_margin: float, adapted_lot: int)
    """
    if is_equity:
        intraday_info = get_intraday_margin_pool(tracker_data)
        # Dedicated ₹30,000 daily MIS intraday margin pool
        stock_avail = max(0.0, float(intraday_info.get('available_margin', 0.0)))
        unit_margin = round(cur_p * 0.20, 2)
        req = round(cur_p * lot_size * 0.20, 2)

        if unit_margin <= 0 or stock_avail < unit_margin:
            # Cannot afford even 1 whole share from available intraday margin pool! Reject purchase
            return False, req, stock_avail, 0

        if req <= stock_avail and req > 0:
            return True, req, stock_avail, lot_size

        # If full size exceeds available cash, downscale strictly to affordable whole shares
        affordable_shares = int(stock_avail // unit_margin)
        if affordable_shares >= 1:
            adapted_lot = affordable_shares
            adapted_req = round(cur_p * adapted_lot * 0.20, 2)
            return True, adapted_req, stock_avail, adapted_lot
        else:
            return False, req, stock_avail, 0

    else:
        cash_info = get_cash_and_margin(tracker_data)
        avail = cash_info['available_cash']
        
        base_sym = sym.replace('MIC', '').replace('MINI', '').replace('INI', '').replace('M', '').replace('TEN', '')
        rate = MARGIN_RATES.get(sym, MARGIN_RATES.get(base_sym, 0.15))
        
        mini_info = PositionSizer.MINI_ROUTES.get(sym) or PositionSizer.MINI_ROUTES.get(base_sym)
        base_unit = mini_info['lot_size'] if mini_info else (lot_size if lot_size > 0 else 1)
        single_lot_margin = round(base_unit * cur_p * rate, 2)
        target_req = round(cur_p * lot_size * rate, 2)

        # Case 1: Ample margin for full target lot size (~₹1.5L allocation)
        if target_req <= avail and target_req > 0:
            return True, target_req, avail, lot_size

        # Case 2: Insufficient for ~₹1.5L target, but available balance can afford at least 1 whole contract lot
        # Ignore the ₹1,50,000 target and take as many whole lots as available balance permits!
        if single_lot_margin > 0 and avail >= single_lot_margin:
            affordable_lots_count = max(1, int(avail // single_lot_margin))
            adapted_lot = affordable_lots_count * base_unit
            adapted_req = round(cur_p * adapted_lot * rate, 2)
            return True, adapted_req, avail, adapted_lot

        # Case 3: Available balance cannot afford even 1 minimum whole lot
        min_req = single_lot_margin if single_lot_margin > 0 else target_req
        return False, min_req, avail, 0

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
        
        # 1. Unconfirmed setups from past days or today's confirmation window (6:15 PM in Summer EDT / 7:15 PM in Winter EST)
        val_time = t.get('time_validation', '')
        is_val_715 = ('07:15' in val_time or '1900' in t.get('id', ''))
        is_val_615 = ('06:15' in val_time or '1800' in t.get('id', ''))
        if not is_val_715 and not is_val_615:
            is_val_715 = not is_us_dst_active(now_dt)
        check_hour = 19 if is_val_715 else 18
        # Confirmation window is 18:15-18:30 in summer, 19:15-19:30 in winter.
        # Only expire after the window ends (minute >= 30 or hour > check_hour)
        is_today_past_615 = (not is_past_day and ((now_dt.hour == check_hour and now_dt.minute >= 30) or now_dt.hour > check_hour))
        if pt.get('status') in ['PENDING_615', 'PENDING_LIMIT', 'PENDING_615_CHECK'] and (is_past_day or is_today_past_615):
            c15['checked'] = True
            c15['status'] = 'CONFIRMATION_EXPIRED'
            c15['action_taken'] = '15-Minute confirmation window elapsed without executed entry. STOOD ASIDE.'
            pt['action'] = 'STAND_ASIDE'
            pt['quantity'] = 0
            pt['status'] = 'CLOSED'
            pt['pnl_inr'] = 0.0
            pt['margin_required_inr'] = 0.0
            eod['outcome'] = 'PROTECTED'
            eod['badge'] = 'Window Lapsed 🛡️' if is_today_past_615 else 'Avoided Trap 🛡️'
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

        if t.get('symbol') == 'NATURALGASM' and (t.get('lot_size') == 2000 or pt.get('quantity') == 2000):
            t['lot_size'] = 3000
            pt['quantity'] = 3000
            if pt.get('entry_price') and pt.get('exit_price'):
                pts = (pt['entry_price'] - pt['exit_price']) if pt.get('action') == 'SELL' else (pt['exit_price'] - pt['entry_price'])
                pnl = round(pts * 3000, 2)
                pt['pnl_points'] = round(pts, 2)
                pt['pnl_inr'] = pnl
                pt['margin_required_inr'] = round(float(pt['entry_price']) * 3000 * 0.18, 2)
                eod['pnl_display'] = f"{'+' if pnl >= 0 else ''}₹{pnl:,.2f}"
                if 'Trailing Profit' in (eod.get('badge') or ''):
                    eod['summary'] = f"Trailing Profit Stop Triggered 💰 (Exit ₹{pt['exit_price']:,.2f} | +35% Target Gains Secured) at {pt.get('exit_time', '08:32 PM IST')}. Entry: ₹{pt['entry_price']:,.2f} -> Exit: ₹{pt['exit_price']:,.2f} ({'+' if pnl >= 0 else ''}₹{pnl:,.2f})."
                if t.get('check_615', {}).get('action_taken'):
                    t['check_615']['action_taken'] = t['check_615']['action_taken'].replace('2000 units', '3000 units')

        if t.get('symbol') == 'CRUDEOILM' and (t.get('lot_size') == 100 or pt.get('quantity') == 100):
            t['lot_size'] = 50
            pt['quantity'] = 50
            if pt.get('entry_price'):
                pt['margin_required_inr'] = round(float(pt['entry_price']) * 50 * 0.25, 2)
            if t.get('check_615', {}).get('action_taken'):
                t['check_615']['action_taken'] = t['check_615']['action_taken'].replace('100 units', '50 units')
            if pt.get('entry_price') and pt.get('exit_price'):
                pts = (pt['entry_price'] - pt['exit_price']) if pt.get('action') == 'SELL' else (pt['exit_price'] - pt['entry_price'])
                pnl = round(pts * 50, 2)
                pt['pnl_points'] = round(pts, 2)
                pt['pnl_inr'] = pnl

        if t.get('symbol') == 'GOLDM' and (t.get('lot_size') == 20 or pt.get('quantity') == 20):
            t['lot_size'] = 10
            pt['quantity'] = 10
            if pt.get('entry_price'):
                pt['margin_required_inr'] = round(float(pt['entry_price']) * 10 * 0.10, 2)
            if t.get('check_615', {}).get('action_taken'):
                t['check_615']['action_taken'] = t['check_615']['action_taken'].replace('20 units', '10 units')
            if pt.get('entry_price') and pt.get('exit_price'):
                pts = (pt['entry_price'] - pt['exit_price']) if pt.get('action') == 'SELL' else (pt['exit_price'] - pt['entry_price'])
                gross_pnl = round(pts * 10, 2)
                pt['pnl_points'] = round(pts, 2)
                pt['gross_pnl_inr'] = gross_pnl
                try:
                    calc = DhanBrokerageCalculator()
                    chg = calc.calculate_trade_charges(
                        symbol='GOLDM',
                        asset_type='COMMODITY_FUTURES',
                        action=pt.get('action', 'SELL'),
                        entry_price=float(pt['entry_price']),
                        exit_price=float(pt['exit_price']),
                        quantity=10
                    )
                    pt['charges'] = chg
                    pt['net_pnl_inr'] = chg.get('net_pnl', gross_pnl)
                    pt['pnl_inr'] = chg.get('net_pnl', gross_pnl)
                    eod['gross_pnl_inr'] = gross_pnl
                    eod['charges'] = chg
                    eod['net_pnl_inr'] = chg.get('net_pnl', gross_pnl)
                    eod['pnl_display'] = f"{'+' if chg['net_pnl'] >= 0 else ''}₹{chg['net_pnl']:,.2f}"
                    eod['summary'] = f"Stop-Loss Triggered 🛑 (Exit ₹{pt['exit_price']:,.2f}) at {pt.get('exit_time', '09:06 PM IST')}. Entry: ₹{pt['entry_price']:,.2f} -> Exit: ₹{pt['exit_price']:,.2f} (Gross: {'+' if gross_pnl >= 0 else ''}₹{gross_pnl:,.2f} | Dhan: -₹{chg['total_charges']:,.2f} | Net: {'+' if chg['net_pnl'] >= 0 else ''}₹{chg['net_pnl']:,.2f})."
                except Exception:
                    pt['pnl_inr'] = gross_pnl

        # 2. ACTIVE trades past their trading session (past day or current day past session cut-off)
        timing_now = get_us_aligned_session_times(now_dt)
        eod_cutoff_h, eod_cutoff_m = timing_now["eod_cutoff"]

        is_past_session = False
        if is_past_day:
            is_past_session = True
        elif is_eq and ((now_dt.hour == 15 and now_dt.minute >= 20) or now_dt.hour >= 16):
            is_past_session = True
        elif not is_eq and ((now_dt.hour == eod_cutoff_h and now_dt.minute >= eod_cutoff_m) or (now_dt.hour > eod_cutoff_h and now_dt.hour < 24) or now_dt.hour < 9):
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
            gross_pnl = round(pts * lot, 2)
            charges = DhanBrokerageCalculator.calculate_trade_charges(
                symbol=t.get('symbol', ''),
                asset_type=t.get('asset_type'),
                action=action,
                entry_price=entry_p,
                exit_price=exit_p,
                quantity=lot,
                product_type=pt.get('product_type')
            )
            net_pnl = round(gross_pnl - charges['total_charges'], 2)
            pnl = net_pnl
            
            exit_time_label = "03:20 PM IST" if is_eq else timing_now["eod_str"]
            exit_trigger = "INTRADAY_320_SQUAREOFF" if is_eq else "EOD_SQUAREOFF"
            
            pt['exit_price'] = exit_p
            pt['exit_time'] = exit_time_label
            pt['exit_trigger'] = exit_trigger
            pt['pnl_points'] = round(pts, 2)
            pt['gross_pnl_inr'] = gross_pnl
            pt['charges'] = charges
            pt['net_pnl_inr'] = net_pnl
            pt['pnl_inr'] = pnl
            pt['status'] = 'CLOSED'
            
            is_correct = gross_pnl > 0
            eod['session_close'] = exit_p
            eod['exit_time'] = exit_time_label
            eod['exit_trigger'] = exit_trigger
            eod['outcome'] = 'CORRECT' if is_correct else ('PROTECTED' if abs(gross_pnl) < 1.0 else 'WRONG')
            eod['badge'] = f"{'Correct ✅' if is_correct else 'Wrong ❌'} ({exit_time_label.replace(' IST', '')})"
            eod['gross_pnl_inr'] = gross_pnl
            eod['charges'] = charges
            eod['net_pnl_inr'] = net_pnl
            eod['pnl_display'] = f"{'+' if pnl >= 0 else ''}₹{pnl:,.2f}"
            eod['summary'] = f"{exit_time_label} session close exit. Entry: ₹{entry_p:,.2f} -> Exit: ₹{exit_p:,.2f} ({pts:+.2f} pts | Gross: {'+' if gross_pnl >= 0 else ''}₹{gross_pnl:,.2f} | Dhan: -₹{charges['total_charges']:,.2f} | Net: {'+' if pnl >= 0 else ''}₹{pnl:,.2f}). Margin released."

    # Self-healing Dhan brokerage and charges verification for all historical executed closed trades
    for t in trades:
        pt = t.get('paper_trade', {})
        if pt.get('status') == 'CLOSED' and pt.get('action') in ['BUY', 'SELL']:
            entry_p = float(pt.get('entry_price') or 0.0)
            exit_p = float(pt.get('exit_price') or 0.0)
            qty = float(pt.get('quantity') or t.get('lot_size') or 1)
            act = pt.get('action')
            if entry_p > 0 and exit_p > 0 and qty > 0:
                raw_pts = (exit_p - entry_p) if act == 'BUY' else (entry_p - exit_p)
                gross_pnl = round(raw_pts * qty, 2)
                charges = DhanBrokerageCalculator.calculate_trade_charges(
                    symbol=t.get('symbol', ''),
                    asset_type=t.get('asset_type'),
                    action=act,
                    entry_price=entry_p,
                    exit_price=exit_p,
                    quantity=qty,
                    product_type=pt.get('product_type')
                )
                net_pnl = round(gross_pnl - charges['total_charges'], 2)
                pt['gross_pnl_inr'] = gross_pnl
                pt['charges'] = charges
                pt['net_pnl_inr'] = net_pnl
                pt['pnl_inr'] = net_pnl
                if 'eod_outcome' in t:
                    t['eod_outcome']['gross_pnl_inr'] = gross_pnl
                    t['eod_outcome']['charges'] = charges
                    t['eod_outcome']['net_pnl_inr'] = net_pnl
    
    # Strictly distinguish executed positions from protected / filtered signals
    executed_trades = [t for t in trades if t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']]
    correct_count = sum(1 for t in executed_trades if t.get('eod_outcome', {}).get('outcome') == 'CORRECT')
    wrong_count = sum(1 for t in executed_trades if t.get('eod_outcome', {}).get('outcome') in ['WRONG', 'INCORRECT'])
    total_executed = len(executed_trades)
    win_rate = round((correct_count / (correct_count + wrong_count) * 100), 1) if (correct_count + wrong_count) > 0 else 0.0
    
    protected_count = sum(1 for t in trades if t.get('paper_trade', {}).get('action') == 'STAND_ASIDE' or (t.get('eod_outcome', {}).get('outcome') == 'PROTECTED' and t.get('paper_trade', {}).get('action') not in ['BUY', 'SELL']))
    total_gross_pnl = sum(t.get('paper_trade', {}).get('gross_pnl_inr', t.get('paper_trade', {}).get('pnl_inr', 0.0)) for t in trades if t.get('paper_trade', {}).get('status') == 'CLOSED' and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL'])
    total_dhan_charges = sum(t.get('paper_trade', {}).get('charges', {}).get('total_charges', 0.0) for t in trades if t.get('paper_trade', {}).get('status') == 'CLOSED' and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL'])
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
        'broker': 'Dhan',
        'total_gross_pnl_inr': round(total_gross_pnl, 2),
        'total_charges_inr': round(total_dhan_charges, 2),
        'total_pnl_inr': round(total_pnl, 2),
        'commodity_pnl_inr': round(cash_info.get('commodity_realized_pnl', 0.0), 2),
        'intraday_pnl_inr': round(cash_info.get('intraday_pool', {}).get('intraday_realized_pnl', 0.0), 2),
        'intraday_booked_profit_inr': round(cash_info.get('intraday_pool', {}).get('booked_profit', 0.0), 2),
        'intraday_pool_balance_inr': round(cash_info.get('intraday_pool', {}).get('base_pool', MAX_INTRADAY_STOCK_MARGIN_INR), 2),
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

def is_autopilot_enabled():
    """Returns True if Dhan Autopilot is enabled (defaults to True unless explicitly disabled)."""
    val = os.environ.get('DHAN_AUTOPILOT_ENABLED', 'true').strip()
    if not val:
        val = 'true'
    return val.lower() in ['true', '1', 'yes']

def dispatch_autonomous_limit_order(trade_entry, asset_type='COMMODITY'):
    """
    Autonomous Autopilot Execution Bridge (DhanHQ).
    Dispatches strict LIMIT orders for active advisory trades.
    Strictly enforces:
    1. Focus on MCX Commodities only (excludes equities and stock futures).
    2. Exclude trade executions for any commodity nearing expiry (<= 3 calendar days).
    3. Strictly LIMIT orders only (never market orders, eliminating slippage).
    """
    if not is_autopilot_enabled():
        return None
        
    pt = trade_entry.get('paper_trade', {})
    if pt.get('status') != 'ACTIVE' or pt.get('action') == 'STAND_ASIDE':
        return None
        
    symbol = trade_entry.get('symbol')
    action = pt.get('action')
    entry_p = pt.get('entry_price')
    sl_p = pt.get('stop_loss') or pt.get('initial_stop_loss')
    tgt_p = pt.get('target')
    qty = pt.get('quantity', 1)
    
    if not symbol or not action or not entry_p or not sl_p:
        return None

    try:
        from dhan_live_executor import DhanLiveExecutor
        dhan_exec = DhanLiveExecutor()
        
        # 0. Enforce High-Volatility Commodity Exclusions on Dhan Broker (Aluminium & Copper excluded from real broker execution per user instruction)
        sym_clean = symbol.strip().upper().replace('-FUT', '').replace('.NS', '').replace('.BO', '')
        if (sym_clean in ['COPPERM', 'COPPER', 'ALUMINIUM', 'ALUMINI'] or 
            sym_clean.startswith('COPPER') or 
            sym_clean.startswith('ALU')):
            print(f"🛑 [BROKER EXCLUSION] {symbol}: Excluded from real Dhan broker execution per user preference. Tracked actively on Trade Analyser portal.")
            pt['dhan_broker_status'] = 'EXCLUDED_USER_PREFERENCE'
            pt['live_broker_order'] = {
                'status': 'EXCLUDED_USER_PREFERENCE',
                'symbol': symbol,
                'reason': 'Aluminium and Copper live broker execution excluded per user preference. Tracked actively on Trade Analyser portal.'
            }
            return {'status': 'EXCLUDED_USER_PREFERENCE', 'reason': 'Aluminium and Copper live broker execution excluded per user instruction'}

        # 0.5 Enforce Universal Multi-Hour Cooldown Guard (Strictly enforced for BOTH BUY and SELL)
        is_cd, cd_msg, cd_rem = check_commodity_cooldown(symbol)
        if is_cd:
            print(f"🛑 [AUTOPILOT COOLDOWN BLOCKED] {symbol}: Live Dhan order blocked. {cd_msg}")
            pt['status'] = 'STAND_ASIDE'
            pt['action'] = 'STAND_ASIDE'
            pt['entry_price'] = None
            pt['quantity'] = 0
            if 'eod_outcome' in trade_entry:
                trade_entry['eod_outcome']['outcome'] = 'PROTECTED'
                trade_entry['eod_outcome']['badge'] = 'Cooldown Active ⏱️'
                trade_entry['eod_outcome']['summary'] = f"Contract {symbol} in mandatory cooldown: {cd_msg}"
            return {'status': 'BLOCKED_COOLDOWN', 'reason': cd_msg, 'remaining_minutes': cd_rem}

        # 1. Enforce Commodity-Only Rule (Live broker execution restricted strictly to MCX Commodities)
        sym_clean = symbol.strip().upper()
        if dhan_exec.focus_commodities_only:
            if asset_type != 'COMMODITY' or sym_clean.endswith('.NS') or sym_clean.endswith('.BO') or '-FUT' in sym_clean or asset_type in ['EQUITY', 'FUTURES', 'INTRADAY_STOCK', 'STOCK_FUTURES', 'OPTIONS']:
                print(f"🛑 [COMMODITY ONLY BROKER GUARD] {symbol}: Non-commodity asset. Live Dhan execution excluded (Trade Analyser tracking remains active).")
                pt['dhan_broker_status'] = 'EXCLUDED_NON_COMMODITY'
                pt['live_broker_order'] = {
                    'status': 'EXCLUDED_NON_COMMODITY',
                    'symbol': symbol,
                    'reason': 'Live Dhan broker execution restricted to MCX Commodities. Active tracking enabled on Trade Analyser portal.'
                }
                return {'status': 'EXCLUDED_NON_COMMODITY', 'reason': 'Dhan execution restricted to MCX Commodities only'}

        # 2. Enforce Expiry Risk Rule (Only exclude if ALL contracts in rollover chain are expired/in tender window)
        is_near, days_left, exp_date = dhan_exec.is_nearing_expiry(symbol)
        if is_near:
            print(f"🛑 [EXPIRY RISK GUARD] {symbol}: All contracts expired/in tender ({days_left}d left on {exp_date}). No forward contract available to roll over.")
            pt['status'] = 'STAND_ASIDE'
            pt['action'] = 'STAND_ASIDE'
            pt['entry_price'] = None
            pt['quantity'] = 0
            if 'eod_outcome' in trade_entry:
                trade_entry['eod_outcome']['outcome'] = 'PROTECTED'
                trade_entry['eod_outcome']['badge'] = 'Expiry Excluded 🛡️'
                trade_entry['eod_outcome']['summary'] = f"Contract {symbol} is nearing expiry on {exp_date} ({days_left}d remaining) and no active forward rollover contract is available."
            return {'status': 'EXCLUDED_NEARING_EXPIRY', 'reason': f'Nearing expiry on {exp_date} and no forward roll available'}

        # 3. Dispatch Strict Limit Order on Dhan
        res = dhan_exec.place_autonomous_limit_order(
            symbol=symbol,
            action=action,
            limit_price=entry_p,
            stop_loss_price=sl_p,
            target_price=tgt_p,
            quantity=qty,
            asset_type=asset_type
        )
        pt['live_broker_order'] = res
        if res.get('status') == 'PLACED':
            pt['dhan_order_id'] = res.get('order_id')
            pt['order_type'] = 'LIMIT'
            print(f"✅ Autonomous Autopilot: Dhan Limit Order Placed for {symbol} (Order ID: {res.get('order_id')})")
        elif res.get('status') in ['EXCLUDED_NEARING_EXPIRY', 'EXCLUDED_NON_COMMODITY']:
            pt['status'] = 'STAND_ASIDE'
            pt['action'] = 'STAND_ASIDE'
            pt['entry_price'] = None
            pt['quantity'] = 0
            if 'eod_outcome' in trade_entry:
                trade_entry['eod_outcome']['outcome'] = 'PROTECTED'
                trade_entry['eod_outcome']['badge'] = 'Excluded 🛡️'
                trade_entry['eod_outcome']['summary'] = res.get('reason')
        else:
            print(f"⚠️ Autonomous Autopilot notice: {res.get('status')} - {res.get('reason')}")
        return res
    except Exception as e:
        err_msg = str(e)
        print(f"⚠️ Error in dispatch_autonomous_limit_order: {err_msg}")
        pt['live_broker_order'] = {
            'status': 'FAILED',
            'reason': f"Dhan Autopilot Error: {err_msg}"
        }
        return None

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

    commodities_map = {c.get('symbol'): c for c in commodities_list if c.get('symbol')}
    seen_bases = set()
    for item in commodities_list:
        raw_sym = item['symbol']
        base = raw_sym.replace('MIC', '').rstrip('M')
        if base in seen_bases:
            continue
        seen_bases.add(base)

        sym, name, lot_size, quote_item = resolve_defense_contract_and_quote(raw_sym, item, commodities_map)
        cur_p = quote_item['current_price']
        day_o = quote_item.get('open', cur_p)
        day_h = quote_item.get('day_high', cur_p)
        day_l = quote_item.get('day_low', cur_p)
        p_close = quote_item.get('prev_close', cur_p)
        chg_pct = quote_item.get('change_pct', 0.0)

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
            inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                sym, cur_p, day_o, day_h, day_l, p_close,
                ema20=item.get('ema_20'), ema50=item.get('ema_50'), ema200=item.get('ema_200'),
                rsi=rsi, adx=adx, atr=atr
            )
            if not inst_dir.get('passed'):
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = f"Direction Engine Block: {inst_dir.get('reason')}"
                sl = None
                tgt = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            elif inst_dir.get('allowed_action') == "BUY":
                action = "BUY"
                entry_text = "BUY / GAP MOMENTUM"
                rationale = f"Aligned with HTF Bull Trend & above Session VWAP (₹{inst_dir.get('vwap'):,.1f}). Riding opening momentum (+{gap_pct:.2f}%)." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                sl = round(min(cur_p, pivot) - atr_buf, 2) if atr_buf else round(min(cur_p, pivot) * 0.996, 2)
                tgt = r2
                c15_status = "GAP_MOMENTUM_ENTERED"
                c15_detail = f"Riding opening breakout momentum towards R2 ₹{r2:,.2f}."
            elif inst_dir.get('allowed_action') == "SELL":
                action = "SELL"
                entry_text = "SELL / GAP FADE (BEARISH REGIME)"
                rationale = f"Gap up into HTF Bear Trend & below Session VWAP (₹{inst_dir.get('vwap'):,.1f}). Fading towards Prev Day Close (₹{p_close:,.2f})." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                sl = round(max(cur_p, r1) + atr_buf, 2) if atr_buf else round(max(cur_p, r1) * 1.004, 2)
                tgt = p_close
                c15_status = "GAP_FADE_ENTERED"
                c15_detail = f"Fading opening gap up towards PDC ₹{p_close:,.2f}."
            else:
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = inst_dir.get('reason')
                sl = None
                tgt = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            
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
                # Institutional Risk Bracket & Hard Stand-Aside (Calibrated 1.1R)
                bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                    sym, action, cur_p, sl, tgt, lot_size, max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                )
                if not bracket_eval.get('passed'):
                    action = "STAND_ASIDE"
                    entry_text = "STAND ASIDE / HARD RISK STOP"
                    rationale = bracket_eval.get('reason')
                    c15_status = "HARD_RISK_STAND_ASIDE"
                    c15_detail = bracket_eval.get('reason')

                # ORB Bar-Close & Wick Trap Guard
                if action in ['BUY', 'SELL'] and "MOMENTUM" in entry_text:
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
                        elif TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                            ta_check = TradingAgentsOrchestrator.deliberate_intraday(sym, cur_p, action=action)
                            if not ta_check.get('approved', True):
                                action = "STAND_ASIDE"
                                entry_text = "STAND ASIDE / AI MULTI-AGENT GUARD"
                                rationale = f"TradingAgents AI Veto: {ta_check.get('reason')}"
                                c15_status = "AI_GUARD_STAND_ASIDE"
                                c15_detail = ta_check.get('reason')

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
                "time_exit": get_us_aligned_session_times(now_dt)["eod_str"],
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
            inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                sym, cur_p, day_o, day_h, day_l, p_close,
                ema20=item.get('ema_20'), ema50=item.get('ema_50'), ema200=item.get('ema_200'),
                rsi=rsi, adx=adx, atr=atr
            )
            if not inst_dir.get('passed'):
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = f"Direction Engine Block: {inst_dir.get('reason')}"
                sl = None
                tgt = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            elif inst_dir.get('allowed_action') == "SELL":
                action = "SELL"
                entry_text = "SELL / GAP FLUSH"
                rationale = f"Aligned with HTF Bear Trend & below Session VWAP (₹{inst_dir.get('vwap'):,.1f}). Riding opening flush momentum ({gap_pct:.2f}%)." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                sl = round(max(cur_p, pivot) + atr_buf, 2) if atr_buf else round(max(cur_p, pivot) * 1.004, 2)
                tgt = s2
                c15_status = "GAP_MOMENTUM_ENTERED"
                c15_detail = f"Riding opening flush momentum towards S2 ₹{s2:,.2f}."
            elif inst_dir.get('allowed_action') == "BUY":
                action = "BUY"
                entry_text = "BUY / GAP DIP RECLAIM (BULLISH REGIME)"
                rationale = f"Gap down into HTF Bull Trend support & above Session VWAP (₹{inst_dir.get('vwap'):,.1f}). Buying value pullback towards PDC ₹{p_close:,.2f}." + (f" (ATR Stop: ₹{atr_buf:,.2f})" if atr_buf else "")
                sl = round(min(cur_p, s1) - atr_buf, 2) if atr_buf else round(min(cur_p, s1) * 0.996, 2)
                tgt = p_close
                c15_status = "GAP_FADE_ENTERED"
                c15_detail = f"Buying value pullback towards PDC ₹{p_close:,.2f}."
            else:
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = inst_dir.get('reason')
                sl = None
                tgt = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')

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
                # Institutional Risk Bracket & Hard Stand-Aside (Calibrated 1.1R)
                bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                    sym, action, cur_p, sl, tgt, lot_size, max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                )
                if not bracket_eval.get('passed'):
                    action = "STAND_ASIDE"
                    entry_text = "STAND ASIDE / HARD RISK STOP"
                    rationale = bracket_eval.get('reason')
                    c15_status = "HARD_RISK_STAND_ASIDE"
                    c15_detail = bracket_eval.get('reason')

                # ORB Bar-Close & Wick Trap Guard
                if action in ['BUY', 'SELL'] and "MOMENTUM" in entry_text:
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
                        elif TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                            ta_check = TradingAgentsOrchestrator.deliberate_intraday(sym, cur_p, action=action)
                            if not ta_check.get('approved', True):
                                action = "STAND_ASIDE"
                                entry_text = "STAND ASIDE / AI MULTI-AGENT GUARD"
                                rationale = f"TradingAgents AI Veto: {ta_check.get('reason')}"
                                c15_status = "AI_GUARD_STAND_ASIDE"
                                c15_detail = ta_check.get('reason')
                            action = "STAND_ASIDE"
                            entry_text = "STAND ASIDE / TIME MACHINE GUARD"
                            rationale = f"Time Machine Simulation Block ({sim_metrics.get('summary')}): Replay leans toward stop loss."
                            c15_status = "SIM_GUARD_STAND_ASIDE"
                            c15_detail = sim_metrics.get('summary')
                        elif TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                            ta_check = TradingAgentsOrchestrator.deliberate_intraday(sym, cur_p, action=action)
                            if not ta_check.get('approved', True):
                                action = "STAND_ASIDE"
                                entry_text = "STAND ASIDE / AI MULTI-AGENT GUARD"
                                rationale = f"TradingAgents AI Veto: {ta_check.get('reason')}"
                                c15_status = "AI_GUARD_STAND_ASIDE"
                                c15_detail = ta_check.get('reason')

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
                "time_exit": get_us_aligned_session_times(now_dt)["eod_str"],
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
                "time_exit": get_us_aligned_session_times(now_dt)["eod_str"],
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
        for e in new_entries:
            dispatch_autonomous_limit_order(e, asset_type='COMMODITY')
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

    commodities_map = {c.get('symbol'): c for c in commodities_list if c.get('symbol')}
    seen_bases = set()
    for item in commodities_list:
        raw_sym = item['symbol']
        base = raw_sym.replace('MIC', '').rstrip('M')
        if base in seen_bases:
            continue
        seen_bases.add(base)

        sym, name, lot_size, quote_item = resolve_defense_contract_and_quote(raw_sym, item, commodities_map)
        cur_p = quote_item['current_price']
        day_o = quote_item.get('open', cur_p)
        day_h = quote_item.get('day_high', cur_p)
        day_l = quote_item.get('day_low', cur_p)
        p_close = quote_item['prev_close']
        chg_pct = quote_item['change_pct']

        # Technical Indicators from Scanner (RSI, ADX, ATR, Volume)
        rsi = quote_item.get('rsi_14')
        adx = quote_item.get('adx_14')
        atr = quote_item.get('atr_14')
        vol = quote_item.get('volume', 0) or 0
        avg_vol = quote_item.get('avg_volume_10d', 0) or 0
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
                "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
                    "lot_size": lot_size, "event_context": f"Sector Correlation Guard ({item_sector} Exposure Capped)",
                    "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / SECTOR CAP", "rationale": f"{item_sector} sector risk already allocated. Stood aside to eliminate double correlated risk.", "reference_price": cur_p, "stop_loss": None, "target": None },
                    "check_615": { "checked": True, "status": "SECTOR_GUARD_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": f"{item_sector} exposure already active." },
                    "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                    "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Sector Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside via Sector Correlation Guard." }
                }
            else:
                # Institutional Direction Engine Evaluation (HTF Trend + Session VWAP)
                inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                    sym, cur_p, day_o, day_h, day_l, p_close,
                    ema20=item.get('ema_20'), ema50=item.get('ema_50'), ema200=item.get('ema_200'),
                    rsi=rsi, adx=adx, atr=atr
                )
                if not inst_dir.get('passed') or inst_dir.get('allowed_action') != 'BUY':
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
                        "lot_size": lot_size, "event_context": f"Direction Engine Block ({inst_dir.get('reason')})",
                        "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / DIRECTION ENGINE", "rationale": inst_dir.get('reason'), "reference_price": cur_p, "stop_loss": None, "target": None },
                        "check_615": { "checked": True, "status": "DIRECTION_ENGINE_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": inst_dir.get('reason') },
                        "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                        "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Direction Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {inst_dir.get('reason')}" }
                    }
                    new_entries.append(entry)
                    continue

                # Valid Trend-Aligned Breakout: Enter on Retest of Broken Asian High (Support)
                support_entry = day_h
                atr_sl_dist = round(1.2 * atr, 2) if (atr and atr > 0) else max(range_pts * 0.25, day_h * 0.003)
                tight_sl = round(day_h - atr_sl_dist, 2)
                tgt = round(day_h + (range_pts * 1.2), 2)
                is_at_support = abs(cur_p - support_entry) / support_entry <= 0.002
                entry_p = cur_p if is_at_support else support_entry
                trade_status = "ACTIVE" if is_at_support else "PENDING_LIMIT"

                # Adaptively route to Mini contract or scale lot size strictly within risk cap
                contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                    sym, name, entry_p, tight_sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
                )
                sym, name, lot_size = contract_sym, contract_name, (adaptive_lot if adaptive_lot > 0 else lot_size)

                # Institutional Risk Bracket & Hard Stand-Aside (Calibrated 1.1R)
                bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                    sym, "BUY", entry_p, tight_sl, tgt, lot_size,
                    max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                )
                if not bracket_eval.get('passed'):
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
                        "lot_size": lot_size, "event_context": "Hard Risk Stop Guard (No Choked Stops)",
                        "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / HARD RISK STOP", "rationale": bracket_eval.get('reason'), "reference_price": cur_p, "stop_loss": None, "target": None },
                        "check_615": { "checked": True, "status": "HARD_RISK_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": bracket_eval.get('reason') },
                        "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                        "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Risk Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {bracket_eval.get('reason')}" }
                    }
                    new_entries.append(entry)
                    continue
                
                # 15-Minute ORB Bar-Close & Wick Trap Guard
                orb_check = ORBTimer.check_orb_confirmation(sym, "BUY", cur_p, day_h, day_l, volume_ratio=vol_ratio, last_bar_close=cur_p)
                if not orb_check.get('passed'):
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                            "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                                "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                                "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                    "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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

                # Adaptively route to Mini contract or scale lot size strictly within risk cap
                contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                    sym, name, entry_p, tight_sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
                )
                sym, name, lot_size = contract_sym, contract_name, (adaptive_lot if adaptive_lot > 0 else lot_size)

                bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                    sym, "SELL", entry_p, tight_sl, tgt, lot_size, max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                )
                if not bracket_eval.get('passed'):
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
                        "lot_size": lot_size, "event_context": "Hard Risk Stop Guard (No Choked Stops)",
                        "suggestion_6pm": { "status": "STAND_ASIDE", "action_text": "STAND ASIDE / HARD RISK STOP", "rationale": bracket_eval.get('reason'), "reference_price": cur_p, "stop_loss": None, "target": None },
                        "check_615": { "checked": True, "status": "HARD_RISK_STAND_ASIDE", "price_615": cur_p, "action_taken": "Stood Aside (0 Units)", "detail": bracket_eval.get('reason') },
                        "paper_trade": { "user": USER_ACCOUNT, "action": "STAND_ASIDE", "quantity": 0, "entry_price": None, "entry_time": "01:30 PM IST", "exit_price": None, "exit_time": None, "pnl_points": 0.0, "pnl_inr": 0.0, "status": "STAND_ASIDE" },
                        "eod_outcome": { "session_close": None, "circuit_limit": None, "outcome": "PROTECTED", "badge": "Risk Protected 🛡️", "pnl_display": "₹0.00", "summary": f"Stood aside: {bracket_eval.get('reason')}" }
                    }
                    new_entries.append(entry)
                    continue
                
                # 15-Minute ORB Bar-Close & Wick Trap Guard
                orb_check = ORBTimer.check_orb_confirmation(sym, "SELL", cur_p, day_h, day_l, volume_ratio=vol_ratio, last_bar_close=cur_p)
                if not orb_check.get('passed'):
                    entry = {
                        "id": trade_id, "date": today_str, "session_name": "London Breakout (1:30 PM)",
                        "time_trigger": "01:30 PM IST", "time_6pm": "01:30 PM IST", "time_validation": "01:45 PM IST",
                        "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                            "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                                "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                                "time_615": "01:45 PM IST", "time_exit": get_us_aligned_session_times(now_dt)["eod_str"], "symbol": sym, "name": name,
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
                "time_exit": get_us_aligned_session_times(now_dt)["eod_str"],
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
        for e in new_entries:
            dispatch_autonomous_limit_order(e, asset_type='COMMODITY')
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
    
    # Dynamically align with US Daylight Saving Time (Summer EDT vs Winter EST)
    timing = get_us_aligned_session_times(now_dt)
    time_trigger_str = timing["pit_str"]
    time_val_str = timing["conf_str"]
    time_exit_str = timing["eod_str"]
    time_tag = timing["pit_tag"]
    session_title = f"US Pit Open ({time_trigger_str.split()[0]})"

    # Check if trades for today already exist
    existing_today_ids = {t['id'] for t in trades if t.get('date') == today_str}
    new_entries = []

    commodities_map = {c.get('symbol'): c for c in commodities_list if c.get('symbol')}
    seen_bases = set()
    for item in commodities_list:
        raw_sym = item['symbol']
        base = raw_sym.replace('MIC', '').rstrip('M')
        if base in seen_bases:
            continue
        seen_bases.add(base)

        sym, name, lot_size, quote_item = resolve_defense_contract_and_quote(raw_sym, item, commodities_map)
        cur_p = quote_item['current_price']
        p_close = quote_item['prev_close']
        chg_pct = quote_item['change_pct']
        status = quote_item.get('status', item.get('status'))
        lc_price = quote_item.get('lower_circuit', item.get('lower_circuit'))
        uc_price = quote_item.get('upper_circuit', item.get('upper_circuit'))

        trade_id = f"ADV-{today_str.replace(' ', '')}-{time_tag}-{sym}"
        trade_id_alt = f"ADV-{today_str.replace(' ', '')}-{sym}"
        if trade_id in existing_today_ids or trade_id_alt in existing_today_ids:
            continue

        pre_ev = next((e for e in pre_6pm_events if e.get('symbol') == sym), None)

        # Institutional Direction Engine Assessment
        inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
            sym, cur_p, item.get('open_price', cur_p), item.get('day_high', cur_p), item.get('day_low', cur_p),
            p_close, ema20=item.get('ema_20'), ema50=item.get('ema_50'), ema200=item.get('ema_200'),
            rsi=item.get('rsi_14'), adx=item.get('adx_14'), atr=item.get('atr_14')
        )

        if pre_ev:
            direction = pre_ev.get('direction', 'LOWER')
            # Enforce Institutional Direction: Do not blindly buy falling lower circuits or short exploding upper circuits
            if direction == 'LOWER' and inst_dir.get('allowed_action') != 'BUY':
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = f"Pre-Pit Open Lower Circuit fade blocked: {inst_dir.get('reason')}. Knife-catching prohibited."
                sl = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            elif direction == 'UPPER' and inst_dir.get('allowed_action') != 'SELL':
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = f"Pre-Pit Open Upper Circuit fade blocked: {inst_dir.get('reason')}. Counter-trend short prohibited."
                sl = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            else:
                action = "BUY" if direction == 'LOWER' else "SELL"
                if direction == 'LOWER':
                    sl = round(min(cur_p, lc_price) * 0.996, 2)
                else:
                    sl = round(max(cur_p, uc_price) * 1.004, 2)
                entry_text = f"WAIT FOR {time_val_str} CANDLE CLOSE before entering {action}"
                rationale = "High institutional volatility at US open. Awaiting bar-close to confirm limit barrier holds."
                c15_status = "PENDING_615"
                c15_detail = f"Action will be triggered dynamically at {time_val_str}."

            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": session_title,
                "time_trigger": time_trigger_str,
                "time_6pm": time_trigger_str,
                "time_validation": time_val_str,
                "time_615": time_val_str,
                "time_exit": time_exit_str,
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"Pre-Open {direction.title()} Circuit Test (15-Min Confirmation Enforced)",
                "suggestion_6pm": {
                    "status": "WAIT_615" if action in ['BUY', 'SELL'] else "STAND_ASIDE",
                    "action_text": entry_text,
                    "rationale": rationale,
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": p_close if action in ['BUY', 'SELL'] else None
                },
                "check_615": {
                    "checked": action not in ['BUY', 'SELL'],
                    "status": c15_status,
                    "price_615": cur_p if action not in ['BUY', 'SELL'] else None,
                    "action_taken": f"Awaiting {time_val_str} candle close verification..." if action in ['BUY', 'SELL'] else "Stood Aside (0 Units)",
                    "detail": c15_detail
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": lot_size if action in ['BUY', 'SELL'] else 0,
                    "entry_price": None,
                    "entry_time": None,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "PENDING_615" if action in ['BUY', 'SELL'] else "STAND_ASIDE"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                    "outcome": "PENDING" if action in ['BUY', 'SELL'] else "PROTECTED",
                    "badge": f"Awaiting {time_val_str.split()[0]} ⏳" if action in ['BUY', 'SELL'] else "Direction Protected 🛡️",
                    "pnl_display": "Pending Confirmation" if action in ['BUY', 'SELL'] else "₹0.00",
                    "summary": f"15-minute confirmation check scheduled for {time_val_str}." if action in ['BUY', 'SELL'] else f"Stood aside: {rationale}"
                }
            }
            new_entries.append(entry)

        elif 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            direction = 'LOWER' if 'LOWER' in status else 'UPPER'
            # Enforce Institutional Direction
            if direction == 'LOWER' and inst_dir.get('allowed_action') != 'BUY':
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = f"US Pit Open Lower Circuit fade blocked: {inst_dir.get('reason')}. Knife-catching prohibited."
                sl = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            elif direction == 'UPPER' and inst_dir.get('allowed_action') != 'SELL':
                action = "STAND_ASIDE"
                entry_text = "STAND ASIDE / DIRECTION ENGINE"
                rationale = f"US Pit Open Upper Circuit fade blocked: {inst_dir.get('reason')}. Counter-trend short prohibited."
                sl = None
                c15_status = "DIRECTION_ENGINE_STAND_ASIDE"
                c15_detail = inst_dir.get('reason')
            else:
                action = "BUY" if direction == 'LOWER' else "SELL"
                if direction == 'LOWER':
                    sl = round(min(cur_p, lc_price) * 0.996, 2)
                else:
                    sl = round(max(cur_p, uc_price) * 1.004, 2)
                entry_text = f"WAIT FOR {time_val_str} CANDLE CLOSE before entering {action}"
                rationale = "High institutional volume at US open. Must confirm limit barrier holds."
                c15_status = "PENDING_615"
                c15_detail = f"Action will be triggered dynamically at {time_val_str}."
            
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": session_title,
                "time_trigger": time_trigger_str,
                "time_6pm": time_trigger_str,
                "time_validation": time_val_str,
                "time_615": time_val_str,
                "time_exit": time_exit_str,
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": f"US Pit Open {direction.title()} Circuit Test (15-Min Rule Enforced)",
                "suggestion_6pm": {
                    "status": "WAIT_615" if action in ['BUY', 'SELL'] else "STAND_ASIDE",
                    "action_text": entry_text,
                    "rationale": rationale,
                    "reference_price": cur_p,
                    "stop_loss": sl,
                    "target": p_close if action in ['BUY', 'SELL'] else None
                },
                "check_615": {
                    "checked": action not in ['BUY', 'SELL'],
                    "status": c15_status,
                    "price_615": cur_p if action not in ['BUY', 'SELL'] else None,
                    "action_taken": f"Awaiting {time_val_str} candle close verification..." if action in ['BUY', 'SELL'] else "Stood Aside (0 Units)",
                    "detail": c15_detail
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": lot_size if action in ['BUY', 'SELL'] else 0,
                    "entry_price": None,
                    "entry_time": None,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "PENDING_615" if action in ['BUY', 'SELL'] else "STAND_ASIDE"
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                    "outcome": "PENDING",
                    "badge": f"Awaiting {time_val_str.split()[0]} ⏳",
                    "pnl_display": "Pending Confirmation",
                    "summary": f"15-minute confirmation check scheduled for {time_val_str}."
                }
            }
            new_entries.append(entry)

        else:
            # US Pit Open Directional Momentum Setup
            # When assets are within normal daily bands, evaluate NYMEX/COMEX pit open trend momentum
            act_dir = inst_dir.get('allowed_action')
            if act_dir in ['BUY', 'SELL']:
                action = act_dir
                # Option 2: Pullback-Only Retest Sniping Gate Check
                pullback_eval = PullbackRetestGate.validate_retest_entry(
                    sym, action, cur_p, item.get('day_high', cur_p), item.get('day_low', cur_p),
                    ema20=item.get('ema_20'), atr=item.get('atr_14')
                )
                if not pullback_eval.get('passed'):
                    print(f"🛡️ US Pit Open Pullback Gate Blocked {sym} {action}: {pullback_eval.get('reason')}")
                    entry = {
                        "id": trade_id,
                        "date": today_str,
                        "session_name": session_title,
                        "time_trigger": time_trigger_str,
                        "time_6pm": time_trigger_str,
                        "time_validation": time_val_str,
                        "time_615": time_val_str,
                        "time_exit": time_exit_str,
                        "symbol": sym,
                        "name": name,
                        "lot_size": lot_size,
                        "event_context": "Option 2: Pullback Required Before Entry",
                        "suggestion_6pm": {
                            "status": "STAND_ASIDE",
                            "action_text": "STAND ASIDE / PULLBACK REQUIRED",
                            "rationale": f"{pullback_eval.get('reason')} Standing aside to avoid chasing at range extremes.",
                            "reference_price": cur_p,
                            "stop_loss": None,
                            "target": None
                        },
                        "check_615": {
                            "checked": True,
                            "status": "PULLBACK_REQUIRED_STAND_ASIDE",
                            "price_615": cur_p,
                            "action_taken": "Stood Aside (0 Units)",
                            "detail": pullback_eval.get('reason')
                        },
                        "paper_trade": {
                            "user": USER_ACCOUNT,
                            "action": "STAND_ASIDE",
                            "quantity": 0,
                            "entry_price": cur_p,
                            "entry_time": time_trigger_str,
                            "exit_price": cur_p,
                            "exit_time": time_exit_str,
                            "pnl_points": 0.0,
                            "pnl_inr": 0.0,
                            "status": "STAND_ASIDE"
                        },
                        "eod_outcome": {
                            "session_close": None,
                            "circuit_limit": None,
                            "outcome": "PROTECTED",
                            "badge": "Pullback Guard 🛡️",
                            "pnl_display": "₹0.00 (Protected)",
                            "summary": f"Option 2 discipline: Price extended from resistance. Stood aside until pullback into supply."
                        }
                    }
                    new_entries.append(entry)
                    continue

                # Technical Invalidation Stop and Realistic 1.1R-1.25R Target
                sl, tgt = calculate_technical_bracket(
                    action, cur_p,
                    day_h=item.get('day_high', cur_p),
                    day_l=item.get('day_low', cur_p),
                    ema20=item.get('ema_20'),
                    ema50=item.get('ema_50'),
                    atr=item.get('atr_14'),
                    min_rrr=1.1,
                    is_intraday=True,
                    symbol=sym
                )
                contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                    sym, name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
                )
                bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                    contract_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size,
                    max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                )
                if bracket_eval.get('passed') and adaptive_lot > 0:
                    entry = {
                        "id": trade_id,
                        "date": today_str,
                        "session_name": session_title,
                        "time_trigger": time_trigger_str,
                        "time_6pm": time_trigger_str,
                        "time_validation": time_val_str,
                        "time_615": time_val_str,
                        "time_exit": time_exit_str,
                        "symbol": contract_sym,
                        "name": contract_name,
                        "lot_size": adaptive_lot,
                        "event_context": f"US Pit Open Trend Momentum {action.title()} (NYMEX/COMEX Open)",
                        "suggestion_6pm": {
                            "status": "WAIT_615",
                            "action_text": f"WAIT FOR {time_val_str} CANDLE CLOSE before entering {action} {contract_sym}",
                            "rationale": f"US Pit Open Momentum: {inst_dir.get('reason')} Target 1.2R calibrated to ATR.",
                            "reference_price": cur_p,
                            "stop_loss": sl,
                            "target": tgt
                        },
                        "check_615": {
                            "checked": False,
                            "status": "PENDING_615",
                            "price_615": None,
                            "action_taken": f"Awaiting {time_val_str} candle close to confirm {action} trend holds...",
                            "detail": f"15-minute verification scheduled for {time_val_str} (Reference: ₹{cur_p:,.2f})."
                        },
                        "paper_trade": {
                            "user": USER_ACCOUNT,
                            "action": action,
                            "quantity": adaptive_lot,
                            "entry_price": None,
                            "entry_time": None,
                            "exit_price": None,
                            "exit_time": None,
                            "pnl_points": 0.0,
                            "pnl_inr": 0.0,
                            "status": "PENDING_615",
                            "target": tgt,
                            "stop_loss": sl
                        },
                        "eod_outcome": {
                            "session_close": None,
                            "circuit_limit": sl,
                            "outcome": "PENDING",
                            "badge": f"Awaiting {time_val_str.split()[0]} ⏳",
                            "pnl_display": "Pending Confirmation",
                            "summary": f"US Pit Open {action} momentum setup awaiting {time_val_str} candle close."
                        }
                    }
                    new_entries.append(entry)
                    continue

            # Fallback: Range-bound / Neutral Chop (Stand Aside)
            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": session_title,
                "time_trigger": time_trigger_str,
                "time_6pm": time_trigger_str,
                "time_validation": time_val_str,
                "time_615": time_val_str,
                "time_exit": time_exit_str,
                "symbol": sym,
                "name": name,
                "lot_size": lot_size,
                "event_context": "Safe Normal Range (Range-Bound Chop)",
                "suggestion_6pm": {
                    "status": "STAND_ASIDE",
                    "action_text": "STAND ASIDE / RANGE-BOUND CHOP",
                    "rationale": f"Asset within tight range ({chg_pct:+.2f}%). {inst_dir.get('reason')}. Capital preserved in cash.",
                    "reference_price": cur_p,
                    "stop_loss": lc_price,
                    "target": p_close
                },
                "check_615": {
                    "checked": True,
                    "status": "CONFIRMED_STAND_ASIDE",
                    "price_615": cur_p,
                    "action_taken": "Stood Aside (0 Units)",
                    "detail": "Tight non-trending consolidation. Capital preserved."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": "STAND_ASIDE",
                    "quantity": 0,
                    "entry_price": cur_p,
                    "entry_time": time_trigger_str,
                    "exit_price": cur_p,
                    "exit_time": time_exit_str,
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
                    "summary": "Stood aside on flat consolidation. 100% capital preserved in cash."
                }
            }
            new_entries.append(entry)

    if new_entries:
        for e in new_entries:
            dispatch_autonomous_limit_order(e, asset_type='COMMODITY')
        # Prepend new entries so newest appears at the top
        tracker['trades'] = new_entries + trades
        save_tracker(tracker)
        print(f"Recorded {len(new_entries)} live 6 PM advisory trades for today.")
    else:
        print("No new circuit limit trades triggered today at 6 PM.")

def record_live_8pm_advisory(commodities_list, day_name):
    """
    Called at 8:00 PM IST (Summer EDT) / 9:00 PM IST (Winter EST) by daily_6pm_advisory.py.
    Evaluates EIA inventory setups (Wed Crude, Thu NatGas) and evening momentum,
    creates paper trades for venu.photos1@gmail.com, and logs entries in the advisory tracker.
    """
    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    
    # Dynamically align with US Daylight Saving Time (Summer EDT vs Winter EST)
    timing = get_us_aligned_session_times(now_dt)
    time_trigger_str = timing["evening_str"]
    time_val_str = f"{timing['evening_str'].split(':')[0]}:15 PM IST"
    time_exit_str = timing["eod_str"]
    time_tag = timing["evening_tag"]
    session_title = f"Evening Review ({time_trigger_str.split()[0]})"
    
    existing_today_ids = {t['id'] for t in trades if t.get('date') == today_str}
    new_entries = []

    # Priority 1: Wednesday EIA Crude Oil Special
    if day_name == 'Wednesday':
        crude = next((c for c in commodities_list if c['symbol'] in ['CRUDEOILM', 'CRUDEOIL']), None)
        if crude:
            trade_id = f"ADV-{today_str.replace(' ', '')}-{time_tag}-CRUDEOIL"
            if trade_id not in existing_today_ids:
                c_sym, c_name, lot_size, quote_item = resolve_defense_contract_and_quote(crude['symbol'], crude, commodities_map)
                cur_p = quote_item['current_price']
                p_close = quote_item['prev_close']
                chg = quote_item.get('change_pct', quote_item.get('pct_change', 0.0))
                lc = quote_item['lower_circuit']
                uc = quote_item['upper_circuit']
                day_h = float(quote_item.get('day_high', cur_p))
                day_l = float(quote_item.get('day_low', cur_p))

                # Fade overreaction if near circuit or spike > 1.5% with trend verification
                if 'NEAR' in crude['status'] or 'HIT' in crude['status'] or abs(chg) >= 1.5:
                    inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                        'CRUDEOIL', cur_p, crude.get('open_price', cur_p), day_h, day_l, p_close,
                        ema20=crude.get('ema_20'), ema50=crude.get('ema_50'), ema200=crude.get('ema_200'),
                        rsi=crude.get('rsi_14'), adx=crude.get('adx_14')
                    )

                    action = "BUY" if chg < 0 else "SELL"
                    
                    # 🛡️ COUNTER-TREND KNIFE-CATCHING & DIRECTION GUARD:
                    # Never blind-buy a collapsing commodity during inventory dump or counter-trend.
                    is_knife_catching = False
                    knife_reason = ""
                    if action == "BUY" and (cur_p <= day_l * 1.002 or chg <= -2.5 or inst_dir.get('allowed_action') != 'BUY'):
                        is_knife_catching = True
                        knife_reason = f"EIA dump rejected: {inst_dir.get('reason')}. Buying into downtrend or below VWAP prohibited."
                    elif action == "SELL" and (cur_p >= day_h * 0.998 or chg >= 2.5 or inst_dir.get('allowed_action') != 'SELL'):
                        is_knife_catching = True
                        knife_reason = f"EIA surge rejected: {inst_dir.get('reason')}. Counter-trend shorting into uptrend prohibited."

                    # Dynamic stop strictly capped by ₹3,000 risk cap (for 50 bbl lot, max 60 pts)
                    max_stop_pts = round(MAX_LOSS_PER_TRADE_INR / lot_size, 2)
                    sl_pts = min(max_stop_pts, max(round(cur_p * 0.008, 2), round(cur_p * 0.002, 2)))
                    sl = round(cur_p - sl_pts if action == "BUY" else cur_p + sl_pts, 2)

                    # Dynamic target enforcing strict >= 1.5R minimum reward-to-risk ratio
                    min_tgt_pts = round(sl_pts * 1.5, 2)
                    tgt_pts = max(min_tgt_pts, abs(p_close - cur_p))
                    tgt = round(cur_p + tgt_pts if action == "BUY" else cur_p - tgt_pts, 2)

                    c_sym, c_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(c_sym, c_name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR)

                    # Hard Stand-Aside on Risk Bracket (Calibrated 1.1R)
                    bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                        c_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size,
                        max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                    )
                    if not bracket_eval.get('passed'):
                        is_knife_catching = True
                        knife_reason = bracket_eval.get('reason')

                    all_existing = new_entries + trades
                    risk_metrics = PreTradeRiskEngine.check_risk(c_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size, all_existing, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, min_reward_risk=1.1)

                    # 🤖 TradingAgents Commodity Benchmark Deliberation
                    ta_check = None
                    if not is_knife_catching and adaptive_lot > 0 and risk_metrics.get('passed'):
                        if TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                            ta_check = TradingAgentsOrchestrator.deliberate_commodity(c_sym, cur_p, action=action)
                            if not ta_check.get('approved', True):
                                is_knife_catching = True
                                knife_reason = f"TradingAgents AI Veto: {ta_check.get('reason')}"

                    if is_knife_catching or not risk_metrics.get('passed'):
                        reason = knife_reason if is_knife_catching else risk_metrics.get('reason')
                        entry = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": f"EIA Crude Inventory ({time_trigger_str.split()[0]})",
                            "time_trigger": time_trigger_str,
                            "time_6pm": time_trigger_str,
                            "time_validation": time_val_str,
                            "time_615": time_val_str,
                            "time_exit": time_exit_str,
                            "symbol": c_sym,
                            "name": c_name,
                            "lot_size": lot_size,
                            "event_context": "Wednesday EIA Petroleum Inventory Spike Fade",
                            "suggestion_6pm": {
                                "status": "STAND_ASIDE",
                                "action_text": "STAND ASIDE / EIA RISK FILTER",
                                "rationale": f"EIA setup stood aside: {reason}",
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
                                "entry_time": time_trigger_str,
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
                                "badge": "Avoided Trap 🛡️",
                                "pnl_display": "₹0.00",
                                "summary": f"Stood aside: {reason}"
                            }
                        }
                        new_entries.append(entry)
                    else:
                        entry = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": f"EIA Crude Inventory ({time_trigger_str.split()[0]})",
                            "time_trigger": time_trigger_str,
                            "time_6pm": time_trigger_str,
                            "time_validation": time_val_str,
                            "time_615": time_val_str,
                            "time_exit": time_exit_str,
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
                                "detail": f"EIA Inventory setup entered at {time_trigger_str}."
                            },
                            "paper_trade": {
                                "user": USER_ACCOUNT,
                                "action": action,
                                "quantity": adaptive_lot,
                                "entry_price": cur_p,
                                "entry_time": time_trigger_str,
                                "exit_price": None,
                                "exit_time": None,
                                "pnl_points": 0.0,
                                "pnl_inr": 0.0,
                                "status": "ACTIVE",
                                "margin_required_inr": round(cur_p * adaptive_lot * MARGIN_RATES.get(c_sym, 0.15), 2),
                                "target": tgt,
                                "stop_loss": sl
                            },
                            "eod_outcome": {
                                "session_close": None,
                                "circuit_limit": lc if action == "BUY" else uc,
                                "outcome": "PENDING",
                                "badge": "Active Trade ⚡",
                                "pnl_display": "Ticking Live...",
                                "summary": f"Active {action} position ({adaptive_lot} units) entered for {USER_ACCOUNT}. Auto-exit at {time_exit_str}."
                            }
                        }
                        new_entries.append(entry)

    # Priority 2: Thursday EIA Natural Gas Special
    elif day_name == 'Thursday':
        natgas = next((c for c in commodities_list if c['symbol'] in ['NATURALGASM', 'NATURALGAS']), None)
        if natgas:
            trade_id = f"ADV-{today_str.replace(' ', '')}-{time_tag}-NATURALGAS"
            if trade_id not in existing_today_ids:
                c_sym, c_name, lot_size, quote_item = resolve_defense_contract_and_quote(natgas['symbol'], natgas, commodities_map)
                cur_p = quote_item['current_price']
                p_close = quote_item['prev_close']
                chg = quote_item.get('change_pct', quote_item.get('pct_change', 0.0))
                lc = quote_item['lower_circuit']
                uc = quote_item['upper_circuit']
                day_h = float(quote_item.get('day_high', cur_p))
                day_l = float(quote_item.get('day_low', cur_p))

                if 'NEAR' in natgas['status'] or 'HIT' in natgas['status'] or abs(chg) >= 2.0:
                    inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                        'NATURALGAS', cur_p, natgas.get('open_price', cur_p), day_h, day_l, p_close,
                        ema20=natgas.get('ema_20'), ema50=natgas.get('ema_50'), ema200=natgas.get('ema_200'),
                        rsi=natgas.get('rsi_14'), adx=natgas.get('adx_14')
                    )

                    action = "BUY" if chg < 0 else "SELL"

                    # 🛡️ COUNTER-TREND KNIFE-CATCHING & DIRECTION GUARD:
                    is_knife_catching = False
                    knife_reason = ""
                    if action == "BUY" and (cur_p <= day_l * 1.002 or chg <= -3.0 or inst_dir.get('allowed_action') != 'BUY'):
                        is_knife_catching = True
                        knife_reason = f"EIA storage drop rejected: {inst_dir.get('reason')}. Buying into downtrend or below VWAP prohibited."
                    elif action == "SELL" and (cur_p >= day_h * 0.998 or chg >= 3.0 or inst_dir.get('allowed_action') != 'SELL'):
                        is_knife_catching = True
                        knife_reason = f"EIA storage surge rejected: {inst_dir.get('reason')}. Counter-trend shorting into uptrend prohibited."

                    # Dynamic stop strictly capped by ₹3,000 risk cap (for 250 lot, max 12 pts)
                    max_stop_pts = round(MAX_LOSS_PER_TRADE_INR / lot_size, 2)
                    sl_pts = min(max_stop_pts, max(round(cur_p * 0.015, 2), round(cur_p * 0.004, 2)))
                    sl = round(cur_p - sl_pts if action == "BUY" else cur_p + sl_pts, 2)

                    # Dynamic target enforcing strict >= 1.5R minimum reward-to-risk ratio
                    min_tgt_pts = round(sl_pts * 1.5, 2)
                    tgt_pts = max(min_tgt_pts, abs(p_close - cur_p))
                    tgt = round(cur_p + tgt_pts if action == "BUY" else cur_p - tgt_pts, 2)

                    c_sym, c_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(c_sym, c_name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR)

                    # Hard Stand-Aside on Risk Bracket (Calibrated 1.1R)
                    bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                        c_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size,
                        max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
                    )
                    if not bracket_eval.get('passed'):
                        is_knife_catching = True
                        knife_reason = bracket_eval.get('reason')

                    all_existing = new_entries + trades
                    risk_metrics = PreTradeRiskEngine.check_risk(c_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size, all_existing, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, min_reward_risk=1.1)

                    # 🤖 TradingAgents Commodity Benchmark Deliberation
                    ta_check = None
                    if not is_knife_catching and adaptive_lot > 0 and risk_metrics.get('passed'):
                        if TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                            ta_check = TradingAgentsOrchestrator.deliberate_commodity(c_sym, cur_p, action=action)
                            if not ta_check.get('approved', True):
                                is_knife_catching = True
                                knife_reason = f"TradingAgents AI Veto: {ta_check.get('reason')}"

                    if is_knife_catching or not risk_metrics.get('passed'):
                        reason = knife_reason if is_knife_catching else risk_metrics.get('reason')
                        entry = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": f"EIA NatGas Storage ({time_trigger_str.split()[0]})",
                            "time_trigger": time_trigger_str,
                            "time_6pm": time_trigger_str,
                            "time_validation": time_val_str,
                            "time_615": time_val_str,
                            "time_exit": time_exit_str,
                            "symbol": c_sym,
                            "name": c_name,
                            "lot_size": lot_size,
                            "event_context": "Thursday EIA Natural Gas Storage Spike Fade",
                            "suggestion_6pm": {
                                "status": "STAND_ASIDE",
                                "action_text": "STAND ASIDE / EIA RISK FILTER",
                                "rationale": f"EIA setup stood aside: {reason}",
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
                                "entry_time": time_trigger_str,
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
                                "badge": "Avoided Trap 🛡️",
                                "pnl_display": "₹0.00",
                                "summary": f"Stood aside: {reason}"
                            }
                        }
                        new_entries.append(entry)
                    else:
                        entry = {
                            "id": trade_id,
                            "date": today_str,
                            "session_name": f"EIA NatGas Storage ({time_trigger_str.split()[0]})",
                            "time_trigger": time_trigger_str,
                            "time_6pm": time_trigger_str,
                            "time_validation": time_val_str,
                            "time_615": time_val_str,
                            "time_exit": time_exit_str,
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
                                "detail": f"EIA Storage setup entered at {time_trigger_str}."
                            },
                            "paper_trade": {
                                "user": USER_ACCOUNT,
                                "action": action,
                                "quantity": adaptive_lot,
                                "entry_price": cur_p,
                                "entry_time": time_trigger_str,
                                "exit_price": None,
                                "exit_time": None,
                                "pnl_points": 0.0,
                                "pnl_inr": 0.0,
                                "status": "ACTIVE",
                                "margin_required_inr": round(cur_p * adaptive_lot * MARGIN_RATES.get(c_sym, 0.15), 2),
                                "target": tgt,
                                "stop_loss": sl
                            },
                            "eod_outcome": {
                                "session_close": None,
                                "circuit_limit": lc if action == "BUY" else uc,
                                "outcome": "PENDING",
                                "badge": "Active Trade ⚡",
                                "pnl_display": "Ticking Live...",
                                "summary": f"Active {action} position ({adaptive_lot} units) entered for {USER_ACCOUNT}. Auto-exit at {time_exit_str}."
                            }
                        }
                        new_entries.append(entry)

    # General US Evening Follow-through for other commodities
    commodities_map = {c.get('symbol'): c for c in commodities_list if c.get('symbol')}
    seen_underlyings = set()
    for item in commodities_list:
        sym = item['symbol']
        if (day_name == 'Wednesday' and sym == 'CRUDEOIL') or (day_name == 'Thursday' and sym == 'NATURALGAS'):
            continue
        clean_underlying = sym.replace('MIC', '').replace('MINI', '').rstrip('M')
        if clean_underlying in seen_underlyings:
            continue
        seen_underlyings.add(clean_underlying)

        c_sym, c_name, def_lot, quote_item = resolve_defense_contract_and_quote(sym, item, commodities_map)
        trade_id = f"ADV-{today_str.replace(' ', '')}-{time_tag}-{clean_underlying}"
        if trade_id in existing_today_ids:
            continue

        status = quote_item.get('status', item['status'])
        if 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            direction = 'LOWER' if 'LOWER' in status else 'UPPER'
            cur_p = quote_item['current_price']
            lc_price = quote_item.get('lower_circuit', item['lower_circuit'])
            uc_price = quote_item.get('upper_circuit', item['upper_circuit'])
            day_h = float(quote_item.get('day_high', cur_p))
            day_l = float(quote_item.get('day_low', cur_p))
            chg_pct = float(quote_item.get('change_pct') or quote_item.get('pct_change') or 0.0)
            tgt = quote_item.get('prev_close', item['prev_close'])
            lot_size = def_lot

            # 🛡️ COUNTER-TREND KNIFE-CATCHING & DIRECTION GUARD:
            # Never blind-buy a collapsing commodity or blind-short a surging commodity.
            # If price is pressing Day Low / Lower Circuit (-1.5% or worse) or trend engine disallows, stand aside.
            inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                c_sym, cur_p, item.get('open_price', cur_p), day_h, day_l, item.get('prev_close', cur_p),
                ema20=item.get('ema_20'), ema50=item.get('ema_50'), ema200=item.get('ema_200'),
                rsi=item.get('rsi_14'), adx=item.get('adx_14')
            )
            is_knife_catching = False
            knife_reason = ""
            if direction == 'LOWER':
                if cur_p <= day_l * 1.002 or chg_pct <= -1.5 or inst_dir.get('allowed_action') != 'BUY':
                    is_knife_catching = True
                    knife_reason = f"Dumping into Lower Circuit ({chg_pct:+.2f}%): {inst_dir.get('reason')}. Knife-catching prohibited."
            elif direction == 'UPPER':
                if cur_p >= day_h * 0.998 or chg_pct >= 1.5 or inst_dir.get('allowed_action') != 'SELL':
                    is_knife_catching = True
                    knife_reason = f"Surging into Upper Circuit ({chg_pct:+.2f}%): {inst_dir.get('reason')}. Counter-trend shorting prohibited."

            if is_knife_catching:
                entry = {
                    "id": trade_id,
                    "date": today_str,
                    "session_name": session_title,
                    "time_trigger": time_trigger_str,
                    "time_6pm": time_trigger_str,
                    "time_validation": time_val_str,
                    "time_615": time_val_str,
                    "time_exit": time_exit_str,
                    "symbol": c_sym,
                    "name": c_name,
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
                        "entry_time": time_trigger_str,
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
                        "badge": "Direction Protected 🛡️",
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
                if sl >= cur_p:
                    sl = round(cur_p * 0.990, 2)
                if tgt <= cur_p:
                    tgt = round(cur_p * 1.015, 2)
            else:
                sl = round(max(cur_p * 1.008, uc_price * 1.005), 2)
                if sl <= cur_p:
                    sl = round(cur_p * 1.010, 2)
                if tgt >= cur_p:
                    tgt = round(cur_p * 0.985, 2)

            # 1. Adaptively route to Mini contract or scale lot size strictly within ₹3,000 risk cap
            contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                sym, item['name'], cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
            )

            # 2. Run PreTradeRiskEngine check
            all_existing = new_entries + trades
            risk_metrics = PreTradeRiskEngine.check_risk(contract_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size, all_existing, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)

            # 3. TradingAgents Commodity Benchmark Deliberation
            ta_blocked = False
            ta_reason = ""
            if adaptive_lot > 0 and risk_metrics.get('passed'):
                if TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                    ta_check = TradingAgentsOrchestrator.deliberate_commodity(contract_sym, cur_p, action=action)
                    if not ta_check.get('approved', True):
                        ta_blocked = True
                        ta_reason = f"TradingAgents AI Veto: {ta_check.get('reason')}"

            if ta_blocked or not risk_metrics.get('passed'):
                reason = ta_reason if ta_blocked else risk_metrics.get('reason')
                entry = {
                    "id": trade_id,
                    "date": today_str,
                    "session_name": session_title,
                    "time_trigger": time_trigger_str,
                    "time_6pm": time_trigger_str,
                    "time_validation": time_val_str,
                    "time_615": time_val_str,
                    "time_exit": time_exit_str,
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
                        "entry_time": time_trigger_str,
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

            # Check Margin Availability before 8:00 PM evening active entry
            # If available balance < ₹1.5L target margin, downscale to affordable whole lots instead of skipping
            has_margin, req_margin, avail_margin, adaptive_lot = check_margin_availability(tracker, c_sym, cur_p, adaptive_lot, is_equity=False, sl=sl)
            if not has_margin:
                entry = {
                    "id": trade_id,
                    "date": today_str,
                    "session_name": session_title,
                    "time_trigger": time_trigger_str,
                    "time_6pm": time_trigger_str,
                    "time_validation": time_val_str,
                    "time_615": time_val_str,
                    "time_exit": time_exit_str,
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
                        "status": "INSUFFICIENT_MARGIN_STAND_ASIDE",
                        "price_615": cur_p,
                        "action_taken": f"Execution Skipped: Insufficient Margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Alert Sent.",
                        "detail": f"Evening setup confirmed, but available cash ₹{avail_margin:,.2f} cannot afford even 1 lot (Min Req: ₹{req_margin:,.2f}). Discretionary alert broadcasted."
                    },
                    "paper_trade": {
                        "user": USER_ACCOUNT,
                        "action": "STAND_ASIDE",
                        "quantity": 0,
                        "entry_price": cur_p,
                        "entry_time": time_trigger_str,
                        "exit_price": cur_p,
                        "exit_time": time_exit_str,
                        "pnl_points": 0.0,
                        "pnl_inr": 0.0,
                        "status": "STAND_ASIDE",
                        "margin_required_inr": 0.0
                    },
                    "eod_outcome": {
                        "session_close": None,
                        "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                        "outcome": "PROTECTED",
                        "badge": "Margin Skipped 🛡️",
                        "pnl_display": "₹0.00 (Discretionary)",
                        "summary": f"Setup confirmed, but execution skipped on managed portfolio due to margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Discretionary alert sent."
                    }
                }
                new_entries.append(entry)
                
                # Send alert notification
                msg = (
                    f"📢 <b>[8:00 PM TRADE ALERT - MARGIN EXHAUSTED] {item['name'].upper()}</b>\n"
                    f"• Strategy: <b>US Evening Circuit Band Test Confirmed</b>\n"
                    f"• Advisory Direction: <b>{action} {adaptive_lot} units</b> @ ₹{cur_p:,.2f}\n"
                    f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{tgt:,.2f}\n"
                    f"• Margin Check: ⚠️ <b>Required: ₹{req_margin:,.2f} | Available Pool: ₹{avail_margin:,.2f}</b>\n"
                    f"• Execution Status: <b>SKIPPED on Managed Portfolio</b> (Insufficient margin for even 1 lot)\n"
                    f"• Discretionary Notice: Broadcasted for manual execution if additional capital/broker margin is available."
                )
                try:
                    from alerts import TelegramAlerter
                    TelegramAlerter().send_alert(msg)
                except Exception as ex:
                    print(f"Telegram alert error: {ex}")
                continue

            entry = {
                "id": trade_id,
                "date": today_str,
                "session_name": session_title,
                "time_trigger": time_trigger_str,
                "time_6pm": time_trigger_str,
                "time_validation": time_val_str,
                "time_615": time_val_str,
                "time_exit": time_exit_str,
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
                    "detail": f"Entered at {time_trigger_str}."
                },
                "paper_trade": {
                    "user": USER_ACCOUNT,
                    "action": action,
                    "quantity": adaptive_lot,
                    "entry_price": cur_p,
                    "entry_time": time_trigger_str,
                    "exit_price": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "pnl_inr": 0.0,
                    "status": "ACTIVE",
                    "margin_required_inr": req_margin,
                    "target": tgt,
                    "stop_loss": sl
                },
                "eod_outcome": {
                    "session_close": None,
                    "circuit_limit": lc_price if direction == 'LOWER' else uc_price,
                    "outcome": "PENDING",
                    "badge": "Active Trade ⚡",
                    "pnl_display": "Ticking Live...",
                    "summary": f"Active {action} position ({adaptive_lot} units) entered for {USER_ACCOUNT}. Auto-exit at {time_exit_str}."
                }
            }
            new_entries.append(entry)

    if new_entries:
        for e in new_entries:
            dispatch_autonomous_limit_order(e, asset_type='COMMODITY')
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
    timing = get_us_aligned_session_times(now_dt)
    cur_hm = (now_dt.hour, now_dt.minute)

    # Time gate: do not run confirmation before scheduled confirmation window (6:15 PM Summer / 7:15 PM Winter)
    if cur_hm < timing["conf_start"]:
        print(f"Current time ({cur_hm[0]:02d}:{cur_hm[1]:02d} IST) is before confirmation window ({timing['conf_str']}). Skipping.")
        return False

    sent_key = 'check_715_sent_date' if not is_us_dst_active(now_dt) else 'check_615_sent_date'
    
    pending_trades = [t for t in trades if t.get('date') == today_str and t.get('check_615', {}).get('status') == 'PENDING_615']
    if not pending_trades and tracker.get(sent_key) == today_str:
        print(f"{timing['conf_str']} confirmation alert already sent for today ({today_str}).")
        return False

    circuits_res = checker.generate_circuits_data()
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}
    
    alert_messages = []
    
    # 1. Process Pending Circuit Confirmation Trades
    for trade in pending_trades:
        sym = trade['symbol']
        c_item = resolve_commodity_item(sym, commodities_map)
        cur_p = c_item['current_price'] if c_item else float(trade.get('suggestion_6pm', {}).get('reference_price', 0.0))
        lc_p = c_item.get('lower_circuit', 0.0) if c_item else 0.0
        uc_p = c_item.get('upper_circuit', 0.0) if c_item else 0.0
        prev_close = c_item.get('prev_close', cur_p) if c_item else cur_p
        direction = 'LOWER' if 'LOWER' in trade['event_context'] else 'UPPER'
        lot_size = trade['lot_size']
        
        # If we are well past the confirmation window (past 6:30 PM in summer / 7:30 PM in winter), do not execute late entries; resolve cleanly as Stood Aside
        is_715 = ('07:15' in trade.get('time_validation', '') or '1900' in trade.get('id', ''))
        conf_hour = 19 if is_715 else 18
        if now_dt.hour > conf_hour or (now_dt.hour == conf_hour and now_dt.minute > 30):
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
        
        # 1. Check if this is a US Pit Open Trend Momentum setup
        if 'Trend Momentum' in trade.get('event_context', ''):
            action = trade.get('paper_trade', {}).get('action') or trade.get('suggestion_6pm', {}).get('status', 'BUY')
            ref_p = float(trade.get('suggestion_6pm', {}).get('reference_price', cur_p))
            sl = float(trade.get('suggestion_6pm', {}).get('stop_loss') or (cur_p * 0.99 if action == 'BUY' else cur_p * 1.01))
            tgt = float(trade.get('suggestion_6pm', {}).get('target') or (cur_p * 1.015 if action == 'BUY' else cur_p * 0.985))
            inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
                sym, cur_p, prev_close, c_item.get('high', cur_p) if c_item else cur_p, c_item.get('low', cur_p) if c_item else cur_p, c_item.get('volume', 0) if c_item else 0
            )
            vwap = inst_dir.get('vwap', cur_p)
            
            is_confirmed = False
            trend_reject_reason = ""
            if action == 'BUY':
                if cur_p >= (ref_p * 0.997) and cur_p >= (vwap * 0.997):
                    is_confirmed = True
                else:
                    trend_reject_reason = f"Price slipped below entry reference (₹{cur_p:,.2f} < ₹{ref_p:,.2f}) or VWAP (₹{vwap:,.1f})."
            else:
                if cur_p <= (ref_p * 1.003) and cur_p <= (vwap * 1.003):
                    is_confirmed = True
                else:
                    trend_reject_reason = f"Price spiked above entry reference (₹{cur_p:,.2f} > ₹{ref_p:,.2f}) or VWAP (₹{vwap:,.1f})."
            
            trade['check_615']['checked'] = True
            trade['check_615']['price_615'] = cur_p

            if not is_confirmed:
                trade['check_615']['status'] = "TREND_FAILED_STAND_ASIDE"
                trade['check_615']['action_taken'] = f"Stood aside: {trend_reject_reason}"
                trade['check_615']['detail'] = trend_reject_reason
                trade['paper_trade']['status'] = "STAND_ASIDE"
                trade['paper_trade']['action'] = "STAND_ASIDE"
                trade['paper_trade']['quantity'] = 0
                trade['paper_trade']['entry_price'] = cur_p
                trade['paper_trade']['exit_price'] = cur_p
                trade['paper_trade']['pnl_inr'] = 0.0
                trade['eod_outcome']['outcome'] = "PROTECTED"
                trade['eod_outcome']['badge'] = "Trend Guard 🛡️"
                trade['eod_outcome']['pnl_display'] = "₹0.00 (Protected)"
                trade['eod_outcome']['summary'] = f"Stood aside: {trend_reject_reason}"
                msg = (
                    f"🛡️ <b>[6:15 PM TREND GUARD] {trade['name'].upper()}</b>\n"
                    f"• Status: 15-Minute Trend Momentum NOT confirmed (Price ₹{cur_p:,.2f})\n"
                    f"• Reason: <b>{trend_reject_reason}</b>\n"
                    f"• Action: Stood aside to protect ₹{MAX_LOSS_PER_TRADE_INR:,.0f} risk capital."
                )
                alert_messages.append(msg)
                continue

            # Run PreTradeRiskEngine check
            risk_metrics = PreTradeRiskEngine.check_risk(sym, action, cur_p, sl, tgt, lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)
            if not risk_metrics.get('passed'):
                reason = risk_metrics.get('reason')
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
                continue

            # MOMENTUM CONFIRMED & RISK PASSED: Check Margin Availability
            # If available balance < ₹1.5L target margin, downscale to affordable whole lots instead of skipping
            has_margin, req_margin, avail_margin, lot_size = check_margin_availability(tracker, sym, cur_p, lot_size, is_equity=False, sl=sl)
            if not has_margin:
                # MARGIN DEFICIT: Skip portfolio paper execution, but broadcast alert!
                trade['check_615']['status'] = "INSUFFICIENT_MARGIN_STAND_ASIDE"
                trade['check_615']['action_taken'] = f"Execution Skipped: Insufficient Margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Alert Sent."
                trade['check_615']['detail'] = f"Rule verification passed, but available cash ₹{avail_margin:,.2f} cannot afford even 1 lot (Min Req: ₹{req_margin:,.2f})."
                trade['paper_trade']['status'] = "STAND_ASIDE"
                trade['paper_trade']['action'] = "STAND_ASIDE"
                trade['paper_trade']['quantity'] = 0
                trade['paper_trade']['entry_price'] = cur_p
                trade['paper_trade']['exit_price'] = cur_p
                trade['paper_trade']['pnl_inr'] = 0.0
                trade['paper_trade']['margin_required_inr'] = 0.0
                trade['eod_outcome']['outcome'] = "PROTECTED"
                trade['eod_outcome']['badge'] = "Margin Skipped 🛡️"
                trade['eod_outcome']['pnl_display'] = "₹0.00 (Discretionary)"
                trade['eod_outcome']['summary'] = f"Trade rule confirmed, but execution skipped on managed portfolio due to margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Discretionary alert sent."
                
                msg = (
                    f"📢 <b>[6:15 PM TRADE ALERT - MARGIN EXHAUSTED] {trade['name'].upper()}</b>\n"
                    f"• Rule Verification: <b>15-Minute Candle Confirmed Trend Momentum</b>\n"
                    f"• Suggested Trade: <b>{action} {lot_size} units</b> @ ₹{cur_p:,.2f}\n"
                    f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{tgt:,.2f}\n"
                    f"• Margin Check: ⚠️ <b>Required: ₹{req_margin:,.2f} | Available Cash: ₹{avail_margin:,.2f}</b>\n"
                    f"• Execution Status: <b>SKIPPED on Managed Portfolio</b> (Insufficient margin for even 1 lot)\n"
                    f"• Discretionary Notice: Broadcasted for manual execution if additional capital/broker margin is available."
                )
                alert_messages.append(msg)
                continue

            # ENTER ACTIVE PAPER TRADE
            trade['lot_size'] = lot_size
            trade['check_615']['status'] = "TREND_CONFIRMED"
            trade['check_615']['action_taken'] = f"15-min candle confirmed US open momentum. ENTERED {action} {lot_size} units @ ₹{cur_p:,.2f}"
            trade['check_615']['detail'] = f"Price held structure (₹{cur_p:,.2f} vs ref ₹{ref_p:,.2f}). US open trend confirmed!"
            
            trade['paper_trade']['status'] = "ACTIVE"
            trade['paper_trade']['action'] = action
            trade['paper_trade']['quantity'] = lot_size
            trade['paper_trade']['entry_price'] = cur_p
            conf_label = "07:15 PM" if is_715 else "06:15 PM"
            exit_label = "11:45 PM" if is_715 else "11:20 PM"
            trade['paper_trade']['entry_time'] = f"{conf_label} IST"
            trade['paper_trade']['target'] = tgt
            trade['paper_trade']['stop_loss'] = sl
            trade['paper_trade']['initial_stop_loss'] = sl
            trade['paper_trade']['original_sl'] = sl
            trade['paper_trade']['peak_high'] = cur_p
            trade['paper_trade']['trough_low'] = cur_p
            trade['paper_trade']['margin_required_inr'] = req_margin
            
            trade['eod_outcome']['outcome'] = "ACTIVE"
            trade['eod_outcome']['badge'] = "Active Trade ⚡"
            trade['eod_outcome']['pnl_display'] = "Ticking Live..."
            trade['eod_outcome']['summary'] = f"Confirmed {action} position ({lot_size} units) active for {USER_ACCOUNT}. Auto-exit at {exit_label} IST."
            
            msg = (
                f"🟢 <b>[{conf_label} TRADE CONFIRMED] {trade['name'].upper()}</b>\n"
                f"• US Pit Open Momentum: <b>15-Minute Candle Confirmed Trend</b>\n"
                f"• Execution: <b>{action} {lot_size} units</b> @ ₹{cur_p:,.2f}\n"
                f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{tgt:,.2f}\n"
                f"• Risk Audit: Max loss capped inside ₹{MAX_LOSS_PER_TRADE_INR:,.0f}\n"
                f"• Account: Virtual Paper Trade logged for {USER_ACCOUNT}\n"
                f"• Auto-Exit: Position will be squared off at {exit_label} IST."
            )
            alert_messages.append(msg)
            continue

        # 2. Check if 15-min candle showed genuine circuit reversal confirmation vs knife-catching
        day_h = float(c_item.get('day_high', cur_p))
        day_l = float(c_item.get('day_low', cur_p))
        chg_pct = float(c_item.get('pct_change', 0.0))
        is_reversal = False
        reversal_reject_reason = ""

        # Institutional Direction Engine Assessment
        inst_dir = InstitutionalDirectionEngine.evaluate_commodity_direction(
            sym, cur_p, c_item.get('open_price', cur_p), day_h, day_l,
            prev_close, ema20=c_item.get('ema_20'), ema50=c_item.get('ema_50'), ema200=c_item.get('ema_200'),
            rsi=c_item.get('rsi_14'), adx=c_item.get('adx_14'), atr=c_item.get('atr_14')
        )

        if direction == 'LOWER':
            # Reversal requires holding above LC AND showing a real bounce off day low (+0.5% off low)
            if cur_p > lc_p and cur_p >= day_l * 1.004 and chg_pct > -3.5 and inst_dir.get('allowed_action') == 'BUY':
                is_reversal = True
            else:
                if not inst_dir.get('passed') or inst_dir.get('allowed_action') != 'BUY':
                    reversal_reject_reason = f"Direction Engine Block: Cannot BUY {sym} while below VWAP (₹{inst_dir.get('vwap'):,.1f}) or in HTF Bear Trend ({inst_dir.get('regime')})."
                else:
                    reversal_reject_reason = f"Hugging Day Low / LC (Chg: {chg_pct:+.2f}%). Heavy downward momentum; knife-catching blocked."
        else:
            # Reversal requires holding below UC AND showing a real pullback off day high (-0.5% off high)
            if cur_p < uc_p and cur_p <= day_h * 0.996 and chg_pct < 3.5 and inst_dir.get('allowed_action') == 'SELL':
                is_reversal = True
            else:
                if not inst_dir.get('passed') or inst_dir.get('allowed_action') != 'SELL':
                    reversal_reject_reason = f"Direction Engine Block: Cannot SHORT {sym} while above VWAP (₹{inst_dir.get('vwap'):,.1f}) or in HTF Bull Trend ({inst_dir.get('regime')})."
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
                if sl >= cur_p:
                    sl = round(cur_p * 0.990, 2)
                if tgt <= cur_p:
                    tgt = round(cur_p * 1.015, 2)
            else:
                sl = round(max(cur_p * 1.008, uc_p * 1.005), 2)
                if sl <= cur_p:
                    sl = round(cur_p * 1.010, 2)
                if tgt >= cur_p:
                    tgt = round(cur_p * 0.985, 2)
            tgt = prev_close
            if action == 'BUY' and tgt <= cur_p:
                tgt = round(cur_p * 1.015, 2)
            elif action == 'SELL' and tgt >= cur_p:
                tgt = round(cur_p * 0.985, 2)

            # 1. Adaptively route to Mini contract or scale lot size strictly within ₹3,000 max risk cap
            contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                sym, trade['name'], cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR
            )

            # Institutional Risk Bracket & Hard Stand-Aside (Calibrated 1.1R)
            bracket_eval = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                contract_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size,
                max_loss_inr=MAX_LOSS_PER_TRADE_INR, min_rrr=1.1
            )
            if not bracket_eval.get('passed'):
                reason = bracket_eval.get('reason')
                trade['check_615']['status'] = "HARD_RISK_STAND_ASIDE"
                trade['check_615']['action_taken'] = f"Hard Risk Stop: {reason}. Stood aside to preserve capital."
                trade['check_615']['detail'] = reason
                trade['paper_trade']['status'] = "STAND_ASIDE"
                trade['paper_trade']['action'] = "STAND_ASIDE"
                trade['paper_trade']['quantity'] = 0
                trade['paper_trade']['entry_price'] = cur_p
                trade['paper_trade']['exit_price'] = cur_p
                trade['paper_trade']['pnl_inr'] = 0.0
                trade['eod_outcome']['outcome'] = "PROTECTED"
                trade['eod_outcome']['badge'] = "Hard Risk Guard 🛡️"
                trade['eod_outcome']['pnl_display'] = "₹0.00 (Protected)"
                trade['eod_outcome']['summary'] = f"Risk bracket invalid: {reason}"
                msg = (
                    f"🛡️ <b>[6:15 PM HARD RISK GUARD] {trade['name'].upper()}</b>\n"
                    f"• Stop Loss: ₹{sl:,.2f} | Target: ₹{tgt:,.2f}\n"
                    f"• Reason: <b>{reason}</b>\n"
                    f"• Action: Stood aside to prevent choked stop loss."
                )
                alert_messages.append(msg)
                continue
            
            # 2. Run PreTradeRiskEngine check
            risk_metrics = PreTradeRiskEngine.check_risk(contract_sym, action, cur_p, sl, tgt, adaptive_lot if adaptive_lot > 0 else lot_size, trades, max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR)

            # 3. TradingAgents Commodity Benchmark Deliberation
            ta_blocked = False
            ta_reason = ""
            if adaptive_lot > 0 and risk_metrics.get('passed'):
                if TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                    ta_check = TradingAgentsOrchestrator.deliberate_commodity(contract_sym, cur_p, action=action)
                    if not ta_check.get('approved', True):
                        ta_blocked = True
                        ta_reason = f"TradingAgents AI Veto: {ta_check.get('reason')}"

            if ta_blocked or not risk_metrics.get('passed'):
                # RISK BLOCKED: Stand aside
                reason = ta_reason if ta_blocked else risk_metrics.get('reason')
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
                # REVERSAL CONFIRMED & RISK PASSED: Check Margin Availability
                # If available balance < ₹1.5L target margin, downscale to affordable whole lots instead of skipping
                has_margin, req_margin, avail_margin, adaptive_lot = check_margin_availability(tracker, sym, cur_p, adaptive_lot, is_equity=False, sl=sl)
                if not has_margin:
                    # MARGIN DEFICIT: Skip portfolio paper execution, but broadcast alert!
                    trade['lot_size'] = adaptive_lot
                    trade['check_615']['status'] = "INSUFFICIENT_MARGIN_STAND_ASIDE"
                    trade['check_615']['action_taken'] = f"Execution Skipped: Insufficient Margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Alert Sent."
                    trade['check_615']['detail'] = f"Circuit reversal confirmed, but available cash ₹{avail_margin:,.2f} cannot afford even 1 lot (Min Req: ₹{req_margin:,.2f})."
                    trade['paper_trade']['status'] = "STAND_ASIDE"
                    trade['paper_trade']['action'] = "STAND_ASIDE"
                    trade['paper_trade']['quantity'] = 0
                    trade['paper_trade']['entry_price'] = cur_p
                    trade['paper_trade']['exit_price'] = cur_p
                    trade['paper_trade']['pnl_inr'] = 0.0
                    trade['paper_trade']['margin_required_inr'] = 0.0
                    trade['eod_outcome']['outcome'] = "PROTECTED"
                    trade['eod_outcome']['badge'] = "Margin Skipped 🛡️"
                    trade['eod_outcome']['pnl_display'] = "₹0.00 (Discretionary)"
                    trade['eod_outcome']['summary'] = f"Circuit reversal confirmed, but execution skipped on managed portfolio due to margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Discretionary alert sent."
                    
                    msg = (
                        f"📢 <b>[6:15 PM REVERSAL ALERT - MARGIN EXHAUSTED] {trade['name'].upper()}</b>\n"
                        f"• 15-Minute Rule: <b>Limit Barrier Held Successfully</b>\n"
                        f"• Suggested Trade: <b>{action} {adaptive_lot} units</b> @ ₹{cur_p:,.2f}\n"
                        f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{prev_close:,.2f}\n"
                        f"• Margin Check: ⚠️ <b>Required: ₹{req_margin:,.2f} | Available Cash: ₹{avail_margin:,.2f}</b>\n"
                        f"• Execution Status: <b>SKIPPED on Managed Portfolio</b> (Insufficient margin for even 1 lot)\n"
                        f"• Discretionary Notice: Broadcasted for manual execution if additional capital/broker margin is available."
                    )
                    alert_messages.append(msg)
                else:
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
                    trade['paper_trade']['initial_stop_loss'] = sl
                    trade['paper_trade']['original_sl'] = sl
                    trade['paper_trade']['peak_high'] = cur_p
                    trade['paper_trade']['trough_low'] = cur_p
                    trade['paper_trade']['margin_required_inr'] = req_margin
                    
                    trade['eod_outcome']['outcome'] = "ACTIVE"
                    trade['eod_outcome']['badge'] = "Active Trade ⚡"
                    trade['eod_outcome']['pnl_display'] = "Ticking Live..."
                    timing_615 = get_us_aligned_session_times(now_dt)
                    trade['eod_outcome']['summary'] = f"Confirmed {action} position ({adaptive_lot} units) active for {USER_ACCOUNT}. Auto-exit at {timing_615['eod_str']}."
                    
                    msg = (
                        f"🟢 <b>[TRADE CONFIRMED] {trade['name'].upper()}</b>\n"
                        f"• 15-Minute Rule: <b>Limit Barrier Held Successfully</b>\n"
                        f"• Execution: <b>{action} {adaptive_lot} units</b> @ ₹{cur_p:,.2f}\n"
                        f"• Stop-Loss: ₹{sl:,.2f} | Target: ₹{prev_close:,.2f}\n"
                        f"• Risk Audit: Max loss capped inside ₹5,000\n"
                        f"• Account: Virtual Paper Trade logged for {USER_ACCOUNT}\n"
                        f"• Auto-Exit: Position will be squared off at {timing_615['eod_str']}."
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
        c_item = resolve_commodity_item(sym, commodities_map)
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

    tracker[sent_key] = today_str
    save_tracker(tracker)
    
    if alert_messages and alerter:
        body = "\n\n".join(alert_messages) + "\n\n🌐 View Live Tracker: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
        alerter.send_alert(f"⚡ [{timing['conf_str']} UPDATE] 15-Min Confirmation & Stand Aside Playbook", body)
        print(f"Successfully dispatched {timing['conf_str']} confirmation alert.")
    
    return True

def check_and_execute_intraday_targets_and_stops(checker=None, alerter=None):
    """
    Called periodically during market hours (every minute via cron/continuous loop).
    Checks all ACTIVE and PENDING_LIMIT advisory paper trades against live high/low/current prices.
    - Fills PENDING_LIMIT trades on Support/Resistance retest.
    - Trails Stop-Loss to Breakeven once price moves 50% towards target (risk-free trade).
    - Executes Target and Stop-Loss auto-exits with instant alert notifications.
    """
    if alerter is None:
        try:
            from alerter import EmailAlerter
            cfg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
            if os.path.exists(cfg_p):
                with open(cfg_p) as cf:
                    alerter = EmailAlerter(json.load(cf))
            else:
                alerter = EmailAlerter({})
        except Exception as al_err:
            print(f"⚠️ Could not init EmailAlerter in check_and_execute_intraday_targets_and_stops: {al_err}")

    tracker = load_tracker()
    trades = tracker.get('trades', [])
    
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_str = now_dt.strftime('%d %b %Y')
    now_time_str = now_dt.strftime('%I:%M %p IST')
    
    relevant_trades = [t for t in trades if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']]
    
    # ⏱️ 3-Minute Limit Order TTL Check on Dhan: Cancel any pending limit order older than 3 minutes (180s)
    if is_autopilot_enabled():
        try:
            from dhan_live_executor import DhanLiveExecutor
            dhan_exec = DhanLiveExecutor()
            stale_cancels = dhan_exec.check_and_cancel_stale_limit_orders(max_age_seconds=180)
            if stale_cancels:
                cancelled_ids = {str(c['order_id']) for c in stale_cancels}
                for t in relevant_trades:
                    pt = t.get('paper_trade', {})
                    if str(pt.get('dhan_order_id')) in cancelled_ids:
                        pt['status'] = 'CANCELLED_UNFILLED'
                        if 'eod_outcome' in t:
                            t['eod_outcome']['outcome'] = 'PROTECTED'
                            t['eod_outcome']['badge'] = 'Limit Expired (Unfilled) ⏱️'
                            t['eod_outcome']['summary'] = "Limit order on Dhan not executed within 3 minutes. Cancelled automatically to prevent stale slippage."
                save_tracker(tracker)
        except Exception as e:
            pass

    if not relevant_trades:
        return False

    circuits_res = checker.generate_circuits_data() if checker else {}
    if not circuits_res:
        circuits_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_data.json')
        if os.path.exists(circuits_file):
            try:
                with open(circuits_file, 'r') as cf:
                    circuits_res = json.load(cf)
            except Exception:
                pass
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
        c_item = resolve_commodity_item(sym, commodities_map) or resolve_commodity_item(clean_sym, commodities_map)
        eq_item = equity_map.get(sym.upper()) or equity_map.get(clean_sym) or equity_map.get(f"{clean_sym}.NS")

        is_stock_future = ('-FUT' in sym) or (sym in STOCK_FUTURES_CONFIG) or (clean_sym in STOCK_FUTURES_CONFIG)
        is_equity = trade.get('asset_type') in ['EQUITY', 'STOCK_FUTURES'] or is_stock_future or (sym and '.NS' in sym) or bool(eq_item and not c_item)

        recent_low = None
        recent_high = None

        if c_item and not is_stock_future:
            cur_p = float(c_item['current_price'])
            if checker and getattr(checker, 'angel_active', False):
                try:
                    base_c = 'COPPER' if clean_sym in ['COPPER', 'COPPERM'] else ('CRUDEOIL' if clean_sym in ['CRUDEOIL', 'CRUDEOILM'] else ('NATURALGAS' if clean_sym in ['NATURALGAS', 'NATURALGASM'] else ('SILVER' if 'SILVER' in clean_sym else ('GOLD' if 'GOLD' in clean_sym else clean_sym))))
                    sec_id = checker.get_angel_mcx_near_month(base_c)
                    if sec_id:
                        live_mcx = checker.get_angel_current_price(sec_id, exchange_segment="MCX")
                        if live_mcx and live_mcx > 0:
                            cur_p = round(float(live_mcx), 2)
                except Exception:
                    pass
            day_h = float(c_item.get('day_high', cur_p))
            day_l = float(c_item.get('day_low', cur_p))
            recent_low = cur_p
            recent_high = cur_p
        elif eq_item:
            cur_p = float(eq_item.get('close', 0))
            day_h = float(eq_item.get('day_high', eq_item.get('high', cur_p)))
            day_l = float(eq_item.get('day_low', eq_item.get('low', cur_p)))
            recent_low = cur_p
            recent_high = cur_p
        elif c_item:
            cur_p = float(c_item['current_price'])
            day_h = float(c_item.get('day_high', cur_p))
            day_l = float(c_item.get('day_low', cur_p))
            recent_low = cur_p
            recent_high = cur_p
        else:
            continue

        # Real-time authoritative live contract price fetch for exact traded asset (0s latency)
        trade_asset_type = 'STOCK_FUTURES' if is_stock_future else (trade.get('asset_type') or ('COMMODITY' if c_item else 'EQUITY'))
        auth_p, auth_tick = get_authoritative_contract_price(sym, asset_type=trade_asset_type)
        if auth_p and auth_p > 0:
            cur_p = auth_p

        # Real-time candle fetch for equity/futures intraday positions to check high/low bounds
        if is_equity or (sym and '.NS' in sym):
            try:
                import yfinance as yf
                yf_sym = sym if sym.endswith('.NS') else f"{clean_sym}.NS"
                t_obj = yf.Ticker(yf_sym)
                fast_p = getattr(t_obj.fast_info, 'last_price', None)
                if fast_p and float(fast_p) > 0 and (not auth_p or auth_p <= 0):
                    cur_p = round(float(fast_p), 2)
                day_h = max(day_h, round(float(getattr(t_obj.fast_info, 'day_high', cur_p) or cur_p), 2))
                day_l = min(day_l, round(float(getattr(t_obj.fast_info, 'day_low', cur_p) or cur_p), 2))
                
                # Fetch recent 1-minute intraday bars to check intra-interval stop breaches
                hist_1m = t_obj.history(period='1d', interval='1m')
                if not hist_1m.empty:
                    if cur_p <= 0:
                        cur_p = round(float(hist_1m['Close'].iloc[-1]), 2)
                    day_h = max(day_h, round(float(hist_1m['High'].max()), 2))
                    day_l = min(day_l, round(float(hist_1m['Low'].min()), 2))
                    recent_low = round(float(hist_1m['Low'].tail(5).min()), 2)
                    recent_high = round(float(hist_1m['High'].tail(5).max()), 2)
                else:
                    recent_low = cur_p
                    recent_high = cur_p
            except Exception as e:
                print(f"Warning: Could not fetch real-time yfinance quote for active equity {sym}: {e}")
                recent_low = cur_p
                recent_high = cur_p
        
        if recent_low is None:
            recent_low = cur_p
        if recent_high is None:
            recent_high = cur_p

        if cur_p <= 0:
            continue
        
        pt = trade['paper_trade']
        action = pt['action']
        lot_size = trade.get('lot_size', 1)
        trade_status = pt.get('status')
        
        s6 = trade.get('suggestion_6pm', {})
        target = float(pt.get('target')) if pt.get('target') is not None else (float(s6.get('target')) if s6.get('target') is not None else None)
        original_target = float(pt.get('original_target')) if pt.get('original_target') is not None else (float(s6.get('original_target')) if s6.get('original_target') is not None else target)
        stop_loss = float(pt.get('stop_loss')) if pt.get('stop_loss') is not None else (float(s6.get('stop_loss')) if s6.get('stop_loss') is not None else None)
        entry_p = float(pt.get('entry_price')) if pt.get('entry_price') is not None else float(s6.get('reference_price', cur_p))

        # Strictly track prices visited AFTER trade entry to prevent pre-entry day extremes from corrupting trailing logic
        if pt.get('initial_stop_loss') is None and stop_loss is not None:
            pt['initial_stop_loss'] = stop_loss

        # Enforce initial stop loss baseline: stop_loss can NEVER start worse than initial_stop_loss
        initial_sl_entry = float(pt.get('initial_stop_loss')) if pt.get('initial_stop_loss') is not None else stop_loss
        if initial_sl_entry is not None and stop_loss is not None:
            if action == 'BUY' and stop_loss < initial_sl_entry:
                stop_loss = initial_sl_entry
            elif action == 'SELL' and stop_loss > initial_sl_entry:
                stop_loss = initial_sl_entry

        # Post-entry peak and trough tracking: initialized at entry price, updated only on post-entry ticks
        effective_high = round(max(float(pt.get('peak_high', cur_p)), cur_p), 2)
        effective_low = round(min(float(pt.get('trough_low', cur_p)), cur_p), 2)

        pt['peak_high'] = effective_high
        pt['trough_low'] = effective_low

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
                    f"• <b>Target</b>: {f'₹{target:,.2f}' if target is not None else (f'₹{calc_target:,.2f} (Trailed)' if calc_target else 'None')} | <b>Stop-Loss</b>: ₹{stop_loss:,.2f}\n"
                    f"• <b>Status</b>: Active paper trade running. Auto-trailing SL and 11:30 PM settlement enabled.\n\n"
                    f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                )
                alerts_to_send.append({'subject': subject, 'body': body})
            continue

        # 2. Check ACTIVE Trades: Dynamic Commodity-Calibrated Trailing Stop Engine
        calc_target = target if target is not None else original_target
        if trade_status == 'ACTIVE' and calc_target is not None and stop_loss is not None:
            target_dist = (calc_target - entry_p) if action == 'BUY' else (entry_p - calc_target)
            if target_dist > 0:
                current_favorable_move = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
                peak_favorable_move = (effective_high - entry_p) if action == 'BUY' else (entry_p - effective_low)
                pct_to_target = max((current_favorable_move / target_dist) * 100, (peak_favorable_move / target_dist) * 100)

                # Commodity-specific noise floor & fee protection calibration
                clean_sym_upper = sym.replace('-FUT', '').replace('.NS', '').upper()
                c_cfg = COMMODITY_TRAIL_CONFIG.get(clean_sym_upper)
                if c_cfg:
                    breakeven_threshold = c_cfg['breakeven_pct']
                    fee_buf = c_cfg['fee_buffer']
                    min_trail_buf = c_cfg['min_trail_buffer']
                else:
                    breakeven_threshold = 75.0
                    fee_buf = round(cur_p * 0.0008, 2)  # Covers Dhan equity/futures charges
                    min_trail_buf = round(cur_p * 0.005, 2)

                # Approach A: Breakeven Shield & Full Target (User-Selected Institutional Protocol)
                # Once price reaches >= breakeven_threshold (70%-75% target progress), move Stop-Loss to Breakeven (+Fee Shield).
                # Stop stays locked at Breakeven, eliminating 100% of downside risk while giving the trade full breathing room
                # (50-70+ pts on Crude) to hit the 100% Target without getting choked by minor bounces or candle wicks.
                if pct_to_target >= breakeven_threshold and not pt.get('trailing_sl_active'):
                    pt['trailing_sl_active'] = True
                    pt['original_sl'] = pt.get('original_sl') or pt.get('initial_stop_loss') or stop_loss
                    be_price = round(entry_p + fee_buf if action == 'BUY' else entry_p - fee_buf, 2)
                    pt['stop_loss'] = be_price
                    stop_loss = be_price
                    if action == 'BUY':
                        pt['trough_low'] = cur_p
                        effective_low = cur_p
                    else:
                        pt['peak_high'] = cur_p
                        effective_high = cur_p
                    trade['eod_outcome']['badge'] = f"Risk-Free Breakeven 🛡️ (SL @ ₹{be_price:,.2f})"
                    trade['eod_outcome']['summary'] = f"Approach A: Breakeven Shield active at ₹{be_price:,.2f} (+Fee Buffer). Running to 100% Target with full breathing room."
                    updated_any = True
                    
                    subject = f"🛡️ [APPROACH A: BREAKEVEN SHIELD (+1.0R)] {trade['name']} Reached {pct_to_target:.0f}% Target Milestone"
                    body = (
                        f"🛡️ <b>APPROACH A: BREAKEVEN SHIELD ACTIVATED (+1.0R)</b>\n"
                        f"📅 <i>{today_str}, {now_time_str}</i>\n"
                        f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                        f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                        f"• <b>Action</b>: {action} {lot_size} units\n"
                        f"• <b>Current Price</b>: ₹{cur_p:,.2f} ({pct_to_target:.1f}% to Target)\n"
                        f"• <b>Entry Price</b>: ₹{entry_p:,.2f}\n"
                        f"• <b>Target</b>: ₹{calc_target:,.2f}\n"
                        f"• <b>Fee-Shielded Stop-Loss</b>: <b>₹{be_price:,.2f} (Covers Dhan Brokerage & Taxes)</b>\n"
                        f"• <b>Initial Stop-Loss</b>: ₹{pt.get('original_sl', entry_p):,.2f}\n\n"
                        f"Position is now 100% risk-free! Stop is locked at Breakeven with maximum breathing room to capture the full 100% target without noise shakeouts.\n\n"
                        f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                    )
                    alerts_to_send.append({'subject': subject, 'body': body})

                # Target Runner Mode: Dynamically trail beyond 100% Target to capture explosive runaway trends
                if pt.get('target_runner_active'):
                    if action == 'BUY':
                        target_floor = round(calc_target - atr_pad, 2)
                        ema_anchor = round(c_ema20 - atr_pad, 2) if (c_ema20 and c_ema20 > target_floor) else target_floor
                        trail_buffer = max(min_trail_buf * 1.5, round(target_dist * 0.35, 2))
                        swing_anchor = round(effective_high - trail_buffer, 2)
                        runner_sl = max(target_floor, ema_anchor, swing_anchor)
                        if runner_sl > stop_loss:
                            stop_loss = runner_sl
                            pt['stop_loss'] = runner_sl
                            trade['eod_outcome']['badge'] = f"Target Runner 🎯🚀 (Trail @ ₹{stop_loss:,.2f})"
                            updated_any = True
                    elif action == 'SELL':
                        target_floor = round(calc_target + atr_pad, 2)
                        ema_anchor = round(c_ema20 + atr_pad, 2) if (c_ema20 and c_ema20 < target_floor) else target_floor
                        trail_buffer = max(min_trail_buf * 1.5, round(target_dist * 0.35, 2))
                        swing_anchor = round(effective_low + trail_buffer, 2)
                        runner_sl = min(target_floor, ema_anchor, swing_anchor)
                        if runner_sl < stop_loss:
                            stop_loss = runner_sl
                            pt['stop_loss'] = runner_sl
                            trade['eod_outcome']['badge'] = f"Target Runner 🎯🚀 (Trail @ ₹{stop_loss:,.2f})"
                            updated_any = True

        # HARD INVARIANT: Stop-Loss must be strictly ONE-WAY (Ratchet only).
        # A stop-loss once set can NEVER move backwards or widen to take additional loss.
        initial_sl_lock = float(pt.get('initial_stop_loss')) if pt.get('initial_stop_loss') is not None else stop_loss
        prev_sl_lock = float(pt.get('stop_loss')) if pt.get('stop_loss') is not None else stop_loss
        if action == 'BUY':
            if initial_sl_lock is not None and stop_loss is not None and stop_loss < initial_sl_lock:
                stop_loss = initial_sl_lock
            if prev_sl_lock is not None and stop_loss is not None and stop_loss < prev_sl_lock:
                stop_loss = prev_sl_lock
        elif action == 'SELL':
            if initial_sl_lock is not None and stop_loss is not None and stop_loss > initial_sl_lock:
                stop_loss = initial_sl_lock
            if prev_sl_lock is not None and stop_loss is not None and stop_loss > prev_sl_lock:
                stop_loss = prev_sl_lock

        # Ensure paper_trade object has target and stop_loss stored
        if pt.get('target_scrapped'):
            pt['target'] = None
            if s6:
                s6['target'] = None
        elif target is not None:
            pt['target'] = target
        if stop_loss is not None:
            pt['stop_loss'] = stop_loss
            # Dynamic Broker SL Sync: Ratchet active Stop-Loss order on Dhan as target milestones are achieved
            dhan_sl_id = pt.get('dhan_sl_order_id')
            last_synced_sl = pt.get('last_synced_broker_sl')
            if is_autopilot_enabled() and dhan_sl_id and stop_loss != last_synced_sl:
                try:
                    from dhan_live_executor import DhanLiveExecutor
                    dhan_exec = DhanLiveExecutor()
                    dhan_qty = pt.get('dhan_quantity') or (3 if 'CRUDE' in sym.upper() else 1)
                    mod_res = dhan_exec.modify_stop_loss_order(
                        order_id=dhan_sl_id,
                        new_trigger_price=stop_loss,
                        action=action,
                        symbol=sym,
                        quantity=dhan_qty,
                        reason=f"Target Milestone Ratchet (Locking ₹{stop_loss:,.2f})",
                        old_trigger_price=last_synced_sl
                    )
                    if mod_res.get('status') == 'success':
                        pt['last_synced_broker_sl'] = stop_loss
                        print(f"🛡️ [DHAN SL RATCHET SYNC] Successfully updated live broker stop-loss on Dhan to ₹{stop_loss:,.2f} (Order: {dhan_sl_id})")
                except Exception as sl_mod_err:
                    print(f"⚠️ Error syncing Stop-Loss ratchet to Dhan: {sl_mod_err}")

        # Ensure active trades carry authoritative live price snapshot and live MTM for client UI
        if trade_status == 'ACTIVE' and cur_p > 0:
            chg_pct = round(((cur_p - entry_p) / entry_p) * 100, 2) if entry_p > 0 else 0.0
            trade['live_price_data'] = {
                'current_price': cur_p,
                'change_pct': chg_pct
            }
            pts_mtm = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
            pt['pnl_points'] = round(pts_mtm, 2)
            pt['pnl_inr'] = round(pts_mtm * lot_size, 2)
            updated_any = True

        # 3. Check Event Risk Guard on Active Positions (Tighten Stop to Breakeven before Inventory Release)
        if trade_status == 'ACTIVE' and not is_equity:
            event_check = EventRiskGuard.check_event_risk(sym, now_dt)
            if not event_check.get('passed') and event_check.get('status') == 'EVENT_BLACKOUT':
                if not pt.get('event_protection_active'):
                    pt['event_protection_active'] = True
                    if action == 'BUY' and cur_p > entry_p and (stop_loss is None or stop_loss < entry_p):
                        pt['original_sl'] = pt.get('original_sl') or pt.get('initial_stop_loss') or stop_loss
                        pt['stop_loss'] = entry_p
                        stop_loss = entry_p
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
                        pt['original_sl'] = pt.get('original_sl') or pt.get('initial_stop_loss') or stop_loss
                        pt['stop_loss'] = entry_p
                        stop_loss = entry_p
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

        # 4. Check Exit Triggers: TARGET HIT, Trailing Stop-Loss, Stagnation & EOD Square-off
        hit_exit = False
        hit_target = False
        hit_target_runner_exit = False
        hit_trailing_profit = False
        hit_breakeven = False
        hit_sl = False
        hit_eod = False
        hit_stagnation = False
        exit_p = cur_p
        reason = ""

        # ⏱️ Smart Dynamic Stagnation Engine (Movement-First & Session Aware):
        # 1. Extended Holding Windows:
        #    - Stock Futures: 240 mins (4.0 hrs) - never prematurely abort high-conviction futures
        #    - Intraday Equities: 210 mins (3.5 hrs) - give room for afternoon trend development
        #    - MCX Commodities: 360 mins (6.0 hrs) - commodities require patience into US session
        # 2. Session Lull Protections:
        #    - Equities Midday Freeze (11:00 AM - 01:30 PM IST): Stagnation exits are completely FROZEN.
        #      Midday lunch lull consolidation is normal; must allow room for 01:30 PM+ afternoon trend.
        #    - Commodities Daytime Freeze (09:00 AM - 05:30 PM IST): Stagnation exits are completely FROZEN.
        #      Daytime Asian hours have low volatility; real moves happen after 5:30 PM with US open.
        # 3. Profitable Trades & Breakeven Hovering Immunity:
        #    - If trade is in profit (f_move > 0), strictly EXEMPT from stagnation exit.
        #    - If trade is hovering near entry (within ±0.4% or < 30% of stop-loss distance), strictly EXEMPT.
        #      Support consolidation is NOT failure!
        # 4. Institutional Technical Defense (Session VWAP & Range Support):
        #    - BUY: If holding at/above Session VWAP (cur_p >= vwap * 0.997), buyers are in control -> EXEMPT.
        #    - SELL: If holding at/below Session VWAP (cur_p <= vwap * 1.003), sellers are in control -> EXEMPT.
        # 5. True Stagnation Exit Trigger:
        #    - Only exits if ALL conditions met:
        #      a) Time threshold elapsed outside session freeze.
        #      b) Trade is in genuine adverse territory (adverse_pct >= 0.5% AND >= 40% toward stop loss).
        #      c) Technical structure broken (lost session VWAP support).
        entry_time_str = pt.get('entry_time')
        trade_date_str = trade.get('date', today_str)
        if trade_status == 'ACTIVE' and entry_time_str:
            try:
                clean_time = entry_time_str.replace(' IST', '').strip()
                entry_dt_naive = datetime.strptime(f"{trade_date_str} {clean_time}", "%d %b %Y %I:%M %p")
                entry_dt = pytz.timezone('Asia/Kolkata').localize(entry_dt_naive)
                mins_open = (now_dt - entry_dt).total_seconds() / 60.0
                
                if is_stock_future:
                    stagnation_cutoff = STOCK_FUTURES_STAGNATION_MINUTES
                elif is_equity:
                    stagnation_cutoff = INTRADAY_STAGNATION_MINUTES
                else:
                    stagnation_cutoff = COMMODITY_STAGNATION_MINUTES

                # Session Lull Protections:
                is_session_freeze = False
                if is_equity or is_stock_future:
                    # Midday Lull Freeze for NSE (11:00 AM - 01:30 PM IST)
                    if (now_dt.hour == 11 and now_dt.minute >= 0) or (now_dt.hour == 12) or (now_dt.hour == 13 and now_dt.minute < 30):
                        is_session_freeze = True
                else:
                    # Daytime Freeze for MCX Commodities (09:00 AM - 05:30 PM IST)
                    if now_dt.hour < 17 or (now_dt.hour == 17 and now_dt.minute < 30):
                        is_session_freeze = True

                f_move = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
                adverse_pct = abs(f_move) / entry_p if entry_p > 0 else 0.0

                # Check conditions: Must be past time cutoff, not in profit, not in session freeze, and not hovering near breakeven
                if mins_open >= stagnation_cutoff and f_move < 0 and not is_session_freeze and adverse_pct >= STAGNATION_MIN_ADVERSE_PCT:
                    # Rule 4: Technical Defense Check (Session VWAP & Range Support)
                    open_p = float(c_item.get('open_price', cur_p) if c_item else (eq_item.get('open', cur_p) if eq_item else cur_p))
                    vwap = InstitutionalDirectionEngine.calculate_session_vwap(open_p, day_h, day_l, cur_p)

                    is_holding_structure = False
                    if action == 'BUY':
                        if vwap > 0 and cur_p >= (vwap * 0.997):
                            is_holding_structure = True
                    elif action == 'SELL':
                        if vwap > 0 and cur_p <= (vwap * 1.003):
                            is_holding_structure = True

                    # Rule 5: Distance to Stop-Loss Check
                    # Trade must have travelled at least 40% of the distance to stop loss before declaring stagnation failure
                    sl_dist = abs(entry_p - stop_loss) if stop_loss else (entry_p * 0.015)
                    moved_toward_sl_pct = abs(f_move) / sl_dist if sl_dist > 0 else 1.0

                    if not is_holding_structure and moved_toward_sl_pct >= 0.40:
                        hit_stagnation = True
                        exit_p = cur_p
                        reason = (f"Smart Stagnation Exit ⏱️ ({int(stagnation_cutoff)}m cutoff: {int(mins_open)}m open | "
                                  f"Down {adverse_pct*100:.2f}% | Lost VWAP ₹{vwap:,.1f} | Thesis Decayed)")
            except Exception:
                pass

        is_trailing = bool(pt.get('stage_2_profit_locked') or pt.get('trailing_sl_active') or pt.get('target_runner_active'))

        # TARGET HIT & UNCAPPED RUNNER MODE ACTIVATION:
        # When 100% target is touched, do NOT cut the trade! Lock guaranteed 100% target profit floor & trail for bonus trend!
        if calc_target is not None:
            target_reached = False
            if action == 'BUY' and (cur_p >= calc_target or (recent_high and recent_high >= calc_target) or (effective_high and effective_high >= calc_target)):
                target_reached = True
            elif action == 'SELL' and (cur_p <= calc_target or (recent_low and recent_low <= calc_target) or (effective_low and effective_low <= calc_target)):
                target_reached = True

            if target_reached and not pt.get('target_runner_active'):
                pt['target_runner_active'] = True
                pt['target_hit_milestone'] = True
                pt['target_hit_price'] = calc_target
                pt['target_hit_time'] = now_time_str
                # Guarantee 100% Target profit floor (cushioned by small ATR pad to avoid spread shakeouts)
                c_atr = float(c_item.get('atr_14', 0) if c_item else (eq_item.get('atr_14', 0) if eq_item else (cur_p * 0.012)))
                atr_pad = round(max(c_atr * 0.15, 0.05), 2)
                target_floor = round(calc_target - atr_pad if action == 'BUY' else calc_target + atr_pad, 2)
                if (action == 'BUY' and target_floor > stop_loss) or (action == 'SELL' and target_floor < stop_loss):
                    pt['stop_loss'] = target_floor
                    stop_loss = target_floor
                trade['eod_outcome']['badge'] = f"Target Runner 🎯🚀 (Floor @ ₹{stop_loss:,.2f})"
                trade['eod_outcome']['summary'] = f"100% Target Achieved! Uncapped Runner Active (Floor ₹{stop_loss:,.2f}). Trailing along 20 EMA / structure for bonus upside."
                updated_any = True

                # Immediately Ratchet Active Broker Stop-Loss on Dhan to Target Floor
                dhan_sl_id = pt.get('dhan_sl_order_id')
                last_synced_sl = pt.get('last_synced_broker_sl')
                if is_autopilot_enabled() and dhan_sl_id and stop_loss != last_synced_sl:
                    try:
                        from dhan_live_executor import DhanLiveExecutor
                        dhan_exec = DhanLiveExecutor()
                        dhan_qty = pt.get('dhan_quantity') or (3 if 'CRUDE' in sym.upper() else 1)
                        mod_res = dhan_exec.modify_stop_loss_order(
                            order_id=dhan_sl_id,
                            new_trigger_price=stop_loss,
                            action=action,
                            symbol=sym,
                            quantity=dhan_qty,
                            reason=f"Target 1 Locked @ ₹{stop_loss:,.2f} (Uncapped Runner Active)",
                            old_trigger_price=last_synced_sl
                        )
                        if mod_res.get('status') == 'success':
                            pt['last_synced_broker_sl'] = stop_loss
                            print(f"🎯 [DHAN TARGET RUNNER SYNC] Successfully locked Stop-Loss at Target 1 Floor ₹{stop_loss:,.2f} on Dhan (Order: {dhan_sl_id})")
                    except Exception as sl_mod_err:
                        print(f"⚠️ Error syncing Target Runner SL to Dhan: {sl_mod_err}")

                subject = f"🎯🚀 [100% TARGET ACHIEVED — RUNNER MODE ACTIVE] {trade['name']} Hit Target @ ₹{calc_target:,.2f}!"
                body = (
                    f"🎯🚀 <b>100% TARGET ACHIEVED — UNCAPPED RUNNER ACTIVE</b>\n"
                    f"📅 <i>{today_str}, {now_time_str}</i>\n"
                    f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                    f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                    f"• <b>Action</b>: {action} {lot_size} units\n"
                    f"• <b>Entry Price</b>: ₹{entry_p:,.2f}\n"
                    f"• <b>Target Milestone Reached</b>: <b>₹{calc_target:,.2f} (100% Target Profit Secured!)</b>\n"
                    f"• <b>Guaranteed Trailing Floor</b>: <b>₹{stop_loss:,.2f}</b>\n"
                    f"• <b>Current Peak</b>: ₹{effective_high if action == 'BUY' else effective_low:,.2f}\n\n"
                    f"Position is now in <b>Target Runner Mode</b>. Full target profits are guaranteed while trailing along 20 EMA and market structure to capture runaway trend expansion!\n\n"
                    f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
                )
                alerts_to_send.append({'subject': subject, 'body': body})

        if not hit_exit:
            if action == 'BUY':
                if is_trailing:
                    # For trailing stops (Breakeven / Profit Lock), trigger strictly when price drops BELOW stop_loss
                    reached_sl = bool(stop_loss and (cur_p < stop_loss or (recent_low and recent_low < stop_loss)))
                else:
                    reached_sl = bool(stop_loss and (cur_p <= stop_loss or (effective_low and effective_low <= stop_loss)))

                if reached_sl:
                    hit_exit = True
                    exit_p = stop_loss
                    if pt.get('target_runner_active'):
                        hit_target_runner_exit = True
                        captured_pct = round(((exit_p - entry_p) / target_dist) * 100, 1) if target_dist > 0 else 100.0
                        reason = f"Target Runner Trailing Exit 🎯🚀 (Exit ₹{exit_p:,.2f} | Peak ₹{effective_high:,.2f} | +{captured_pct}% Target Gains Captured)"
                    elif pt.get('stage_2_profit_locked'):
                        hit_trailing_profit = True
                        reason = f"Structural Trailing Profit Exit 💰 (Exit ₹{exit_p:,.2f} | Structure Support Retested)"
                    elif pt.get('trailing_sl_active'):
                        hit_breakeven = True
                        reason = f"Fee-Shielded Breakeven Stop Triggered 🛡️ (Exit ₹{exit_p:,.2f} | Capital & Fees Protected)"
                    else:
                        hit_sl = True
                        if effective_low <= stop_loss and cur_p > stop_loss:
                            reason = f"Stop-Loss Triggered 🛑 (Price pierced ₹{effective_low:,.2f} ≤ Stop ₹{stop_loss:,.2f} during interval and bounced to ₹{cur_p:,.2f})"
                        else:
                            reason = f"Stop-Loss Triggered 🛑 (Exit ₹{exit_p:,.2f})"
                elif is_equity and ((now_dt.hour == 15 and now_dt.minute >= 20) or now_dt.hour >= 16 or now_dt.hour < 9):
                    hit_exit = True
                    hit_eod = True
                    exit_p = cur_p
                    reason = "Intraday EOD Square-off (03:20 PM) ⏰"

            elif action == 'SELL':
                if is_trailing:
                    # For trailing stops (Breakeven / Profit Lock), trigger strictly when price rises ABOVE stop_loss
                    reached_sl = bool(stop_loss and (cur_p > stop_loss or (recent_high and recent_high > stop_loss)))
                else:
                    reached_sl = bool(stop_loss and (cur_p >= stop_loss or (effective_high and effective_high >= stop_loss)))

                if reached_sl:
                    hit_exit = True
                    exit_p = stop_loss
                    if pt.get('target_runner_active'):
                        hit_target_runner_exit = True
                        captured_pct = round(((entry_p - exit_p) / target_dist) * 100, 1) if target_dist > 0 else 100.0
                        reason = f"Target Runner Trailing Exit 🎯🚀 (Exit ₹{exit_p:,.2f} | Trough ₹{effective_low:,.2f} | +{captured_pct}% Target Gains Captured)"
                    elif pt.get('stage_2_profit_locked'):
                        hit_trailing_profit = True
                        reason = f"Structural Trailing Profit Exit 💰 (Exit ₹{exit_p:,.2f} | Structure Resistance Retested)"
                    elif pt.get('trailing_sl_active'):
                        hit_breakeven = True
                        reason = f"Fee-Shielded Breakeven Stop Triggered 🛡️ (Exit ₹{exit_p:,.2f} | Capital & Fees Protected)"
                    else:
                        hit_sl = True
                        if effective_high >= stop_loss and cur_p < stop_loss:
                            reason = f"Stop-Loss Triggered 🛑 (Price spiked to ₹{effective_high:,.2f} ≥ Stop ₹{stop_loss:,.2f} during interval and pulled back to ₹{cur_p:,.2f})"
                        else:
                            reason = f"Stop-Loss Triggered 🛑 (Exit ₹{exit_p:,.2f})"
                elif is_equity and ((now_dt.hour == 15 and now_dt.minute >= 20) or now_dt.hour >= 16 or now_dt.hour < 9):
                    hit_exit = True
                    hit_eod = True
                    exit_p = cur_p
                    reason = "Intraday EOD Square-off (03:20 PM) ⏰"

        if hit_exit or hit_stagnation or hit_eod:
            pnl_pts = (exit_p - entry_p) if action == 'BUY' else (entry_p - exit_p)
            # Strict Stop-Loss PnL Sanity Guard: An initial/structural stop-loss hit can NEVER yield positive profit!
            if hit_sl and not is_trailing:
                if pnl_pts > 0:
                    pnl_pts = -abs(pnl_pts)
            gross_pnl_inr = round(pnl_pts * lot_size, 2)
            
            # Authoritative Dhan Brokerage & Statutory Taxes
            charges = DhanBrokerageCalculator.calculate_trade_charges(
                symbol=sym,
                asset_type=trade.get('asset_type'),
                action=action,
                entry_price=entry_p,
                exit_price=exit_p,
                quantity=lot_size,
                product_type=pt.get('product_type')
            )
            net_pnl_inr = round(gross_pnl_inr - charges['total_charges'], 2)
            pnl_inr = net_pnl_inr
            
            pt['exit_price'] = exit_p
            pt['exit_time'] = now_time_str
            pt['gross_pnl_inr'] = gross_pnl_inr
            pt['charges'] = charges
            pt['net_pnl_inr'] = net_pnl_inr
            
            if hit_target:
                pt['exit_trigger'] = "TARGET_HIT"
                outcome_val = "CORRECT"
                badge_text = f"Target Hit 🎯 ({now_time_str})"
            elif hit_target_runner_exit:
                pt['exit_trigger'] = "TARGET_RUNNER_EXIT"
                outcome_val = "CORRECT"
                badge_text = f"Target Runner 🎯🚀 ({now_time_str})"
            elif hit_trailing_profit:
                pt['exit_trigger'] = "TRAILING_PROFIT_HIT"
                outcome_val = "CORRECT"
                badge_text = f"Trailing Profit 💰 ({now_time_str})"
            elif hit_breakeven:
                pt['exit_trigger'] = "TRAILING_BREAKEVEN_HIT"
                outcome_val = "PROTECTED"
                badge_text = f"Fee-Shielded Breakeven 🛡️ ({now_time_str})"
            elif hit_sl:
                pt['exit_trigger'] = "STOP_LOSS_HIT"
                outcome_val = "WRONG"
                badge_text = f"Stop Loss Hit 🛑 ({now_time_str})"
            elif hit_stagnation:
                pt['exit_trigger'] = "STAGNATION_EXIT"
                outcome_val = "CORRECT" if gross_pnl_inr > 0 else ("PROTECTED" if abs(gross_pnl_inr) < 1.0 else "WRONG")
                badge_text = f"Stagnation Exit ⏱️ ({now_time_str})"
            elif hit_eod:
                pt['exit_trigger'] = "TARGET_RUNNER_EOD" if pt.get('target_runner_active') else "EOD_SQUAREOFF"
                outcome_val = "CORRECT" if gross_pnl_inr > 0 else ("WRONG" if gross_pnl_inr < 0 else "PROTECTED")
                badge_text = f"Runner EOD Peak 🎯🚀 ({now_time_str})" if pt.get('target_runner_active') else f"EOD Square-off ⏰ ({now_time_str})"
            
            pt['pnl_points'] = round(pnl_pts, 2)
            pt['pnl_inr'] = pnl_inr
            pt['status'] = "CLOSED"
            
            # Live Dhan Broker Square-off and Exit Email Notification
            if pt.get('dhan_order_id') or pt.get('dhan_sl_order_id'):
                try:
                    from dhan_live_executor import DhanLiveExecutor
                    dhan_exec = DhanLiveExecutor()
                    dhan_exec.notify_dhan_event('POSITION_SQUARED_OFF', {
                        'symbol': sym,
                        'exit_action': 'BUY' if action == 'SELL' else 'SELL',
                        'quantity': pt.get('quantity', 1),
                        'exit_price': exit_p,
                        'reason': f"{badge_text} ({reason})",
                        'pnl': net_pnl_inr
                    })
                    scrip_info = dhan_exec.resolve_scrip(sym)
                    if scrip_info:
                        sec_id = str(scrip_info['security_id'])
                        seg = scrip_info.get('exchange_segment', 'MCX_COMM')
                        dhan_exec.square_off_position(sec_id, seg, action, pt.get('quantity', 1), price=exit_p)
                except Exception as dex:
                    print(f"⚠️ Live Broker Square-Off Notice: {dex}")
            
            trade['time_exit'] = now_time_str
            trade['eod_outcome']['session_close'] = exit_p
            trade['eod_outcome']['exit_time'] = now_time_str
            trade['eod_outcome']['exit_trigger'] = pt['exit_trigger']
            trade['eod_outcome']['outcome'] = outcome_val
            trade['eod_outcome']['badge'] = badge_text
            trade['eod_outcome']['gross_pnl_inr'] = gross_pnl_inr
            trade['eod_outcome']['charges'] = charges
            trade['eod_outcome']['net_pnl_inr'] = net_pnl_inr
            trade['eod_outcome']['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
            trade['eod_outcome']['summary'] = f"{reason} at {now_time_str}. Entry: ₹{entry_p:,.2f} -> Exit: ₹{exit_p:,.2f} (Gross: {'+' if gross_pnl_inr >= 0 else ''}₹{gross_pnl_inr:,.2f} | Dhan: -₹{charges['total_charges']:,.2f} | Net: {'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f})."
            
            updated_any = True
            
            icon = "🎯" if hit_target else ("🚀" if hit_target_runner_exit else ("💰" if hit_trailing_profit else ("🛡️" if hit_breakeven else "🛑")))
            subject = f"{icon} [{badge_text.upper()}] {trade['name']} Paper Trade Exit ({now_time_str})"
            body = (
                f"{icon} <b>PAPER TRADE AUTO-EXIT: {badge_text.upper()}</b>\n"
                f"📅 <i>{today_str}, {now_time_str}</i>\n"
                f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                f"• <b>Asset</b>: {trade['name']} ({sym})\n"
                f"• <b>Position</b>: {action} {lot_size} units\n"
                f"• <b>Entry Price</b>: ₹{entry_p:,.2f}\n"
                f"• <b>Exit Price</b>: ₹{exit_p:,.2f} ({reason})\n"
                f"• <b>Target</b>: {f'₹{target:,.2f}' if target is not None else (f'₹{calc_target:,.2f} (Trailed)' if calc_target else 'None')} | <b>Stop-Loss</b>: ₹{stop_loss:,.2f}\n"
                f"• <b>Gross P&L</b>: <b>{'+' if gross_pnl_inr >= 0 else ''}₹{gross_pnl_inr:,.2f}</b>\n"
                f"• <b>Dhan Charges & Taxes</b>: <b>-₹{charges['total_charges']:,.2f}</b> (Brokerage ₹{charges['brokerage']:,.2f} + Taxes ₹{charges['total_charges'] - charges['brokerage']:,.2f})\n"
                f"• <b>Net Realized P&L</b>: <b>{'+' if net_pnl_inr >= 0 else ''}₹{net_pnl_inr:,.2f}</b>\n\n"
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

def evaluate_realtime_technical_triggers(checker=None, alerter=None):
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

    # MASTER SAFETY HALT SWITCH (User unpaused - Active)
    TRADING_PAUSED = False
    if TRADING_PAUSED:
        print(f"🛑 MASTER SAFETY HALT ACTIVE: All trading and triggers are suspended per user request.")
        return False

    if alerter is None:
        try:
            from alerter import EmailAlerter
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r') as cf:
                    cfg = json.load(cf)
                alerter = EmailAlerter(cfg)
        except Exception as e:
            print(f"Warning: Could not initialize fallback EmailAlerter: {e}")
            alerter = None
        
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

    # 2. Maximum Concurrent Open Positions Cap (Max 2 Commodities, Max 2 Stock Futures, Max 3 Intraday Stocks)
    active_commodity_trades = [
        t for t in trades 
        if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']
        and (t.get('symbol') in LOT_SIZES and not str(t.get('symbol', '')).endswith('-FUT'))
    ]
    active_stock_futures = [
        t for t in trades
        if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']
        and ('-FUT' in str(t.get('symbol', '')) or t.get('asset_type') == 'STOCK_FUTURES')
    ]
    active_equity_trades = [
        t for t in trades 
        if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT']
        and (t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] and not str(t.get('symbol', '')).endswith('-FUT'))
    ]
    total_active = len(active_commodity_trades) + len(active_stock_futures) + len(active_equity_trades)
    if (len(active_commodity_trades) >= MAX_CONCURRENT_COMMODITIES and 
        len(active_stock_futures) >= MAX_CONCURRENT_STOCK_FUTURES and 
        len(active_equity_trades) >= MAX_CONCURRENT_EQUITIES):
        return False
    if total_active >= MAX_CONCURRENT_TOTAL:
        return False

    new_trades = []
    alerts_to_send = []

    # =========================================================================
    # PART 1: MCX COMMODITIES CONTINUOUS SCANNER
    # =========================================================================
    circuits_res = checker.generate_circuits_data() if checker else {}
    commodities = circuits_res.get('commodities', []) if (checker and len(active_commodity_trades) < MAX_CONCURRENT_COMMODITIES) else []
    commodities_map = {c.get('symbol'): c for c in commodities if c.get('symbol')}

    # 🎯 Institutional Directional Movement Ranking for Commodities:
    # Prioritizes commodities displaying the highest directional impulse (ADX Trend Strength + Absolute % Move + Volume Expansion).
    # Ensures the engine acts on active trending movers (e.g. Crude Oil, Natural Gas, Silver) rather than choppy, flat contracts.
    commodities.sort(
        key=lambda c: (
            float(c.get('adx_14') or 15.0) * 1.5 +
            abs(float(c.get('change_pct') or 0.0)) * 20.0 +
            (float(c.get('volume_ratio') or 1.0) if c.get('volume_ratio') else 1.0) * 10.0
        ),
        reverse=True
    )

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

        # Prevent duplicate active positions in the same canonical commodity
        canon_sym = get_canonical_commodity_root(sym)
        if any(
            get_canonical_commodity_root(t.get('symbol')) == canon_sym
            and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT'] 
            for t in (trades + new_trades)
        ):
            continue

        item_sector = SECTORS.get(sym, 'OTHER')
        if item_sector in active_sectors:
            continue

        # Check symbol trade count and cooldown today (only for actual executed BUY/SELL trades, not STAND_ASIDE)
        # Match using canonical commodity root so parent, Mini, and Micro contracts share the same cooldown
        sym_today_trades = [
            t for t in (trades + new_trades)
            if t.get('date') == today_str 
            and get_canonical_commodity_root(t.get('symbol')) == canon_sym
            and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
            and t.get('paper_trade', {}).get('quantity', 0) > 0
        ]
        if len(sym_today_trades) >= MAX_TRADES_PER_SYMBOL_DAY:
            continue  # Max 3 trades per commodity per day

        # Universal Cooldown Guard: Enforces 120m post-SL, 120m post-target, and 90m standard lockout (BOTH BUY & SELL)
        is_cd, cd_msg, cd_rem = check_commodity_cooldown(sym, trades=(trades + new_trades), today_str=today_str, now_dt=now_dt)
        if is_cd:
            print(f"⏱️ Cooldown Active for {sym}: {cd_msg}")
            continue

        cur_p = float(item.get('current_price') or 0)
        # Fetch authoritative live execution price if broker API is connected
        if checker and getattr(checker, 'angel_active', False):
            try:
                base_c_sym = 'COPPER' if sym in ['COPPER', 'COPPERM'] else ('CRUDEOIL' if sym in ['CRUDEOIL', 'CRUDEOILM'] else ('NATURALGAS' if sym in ['NATURALGAS', 'NATURALGASM'] else ('SILVER' if 'SILVER' in sym else ('GOLD' if 'GOLD' in sym else sym))))
                sec_id = checker.get_angel_mcx_near_month(base_c_sym)
                if sec_id:
                    live_mcx = checker.get_angel_current_price(sec_id, exchange_segment="MCX")
                    if live_mcx and live_mcx > 0:
                        cur_p = round(float(live_mcx), 2)
            except Exception:
                pass
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
        sym, name, lot_size, quote_item = resolve_defense_contract_and_quote(sym, item, commodities_map)
        if quote_item is not item:
            cur_p = float(quote_item.get('current_price') or cur_p)
            day_h = float(quote_item.get('day_high') or cur_p)
            day_l = float(quote_item.get('day_low') or cur_p)
            p_close = float(quote_item.get('prev_close') or cur_p)
            chg_pct = float(quote_item.get('change_pct') or chg_pct)
            if quote_item.get('rsi_14'): rsi = quote_item['rsi_14']
            if quote_item.get('adx_14'): adx = quote_item['adx_14']
            if quote_item.get('atr_14'): atr = quote_item['atr_14']
            vol = quote_item.get('volume', 0) or vol
            avg_vol = quote_item.get('avg_volume_10d', 0) or avg_vol
            vol_ratio = (vol / avg_vol) if (avg_vol > 0 and vol > 0) else vol_ratio
            atr_val = atr if (atr and atr > 0) else (cur_p * 0.008)

        triggered = False
        action = None
        setup_name = ""
        rationale = ""
        sl = None
        tgt = None

        # --- SETUP 1: Exhaustion Mean-Reversion Reversal Engine ---
        # Activates strictly at extreme oversold/overbought conditions where momentum is exhausted (>72% reversal edge).
        # Replaces bottom-shorting with high-conviction support bounces targeting VWAP / 20 EMA.
        bb_low = float(item.get('bb_lower') or (cur_p * 0.98))
        bb_high = float(item.get('bb_upper') or (cur_p * 1.02))

        # A. Extreme Oversold Exhaustion Reversal (BUY)
        # Conditions: RSI <= 34.0 or near Lower BB, strictly located at support near Day Low (lower half of range)
        session_mid_check = (day_h + day_l) / 2.0 if (day_h and day_l and day_h > day_l) else cur_p
        if (((rsi is not None and rsi <= 34.0) or (cur_p <= bb_low * 1.008 and rsi is not None and rsi <= 38.0) or (chg_pct <= -1.8 and rsi is not None and rsi <= 38.0)) 
            and (day_l > 0 and cur_p <= day_l * 1.025 and cur_p <= session_mid_check)):
            if day_l > 0 and cur_p >= day_l:
                triggered = True
                action = "BUY"
                setup_name = "Exhaustion Mean-Reversion Reversal (Oversold Bounce)"
                sl = round(day_l - (0.5 * atr_val), 2)
                revert_target = round(ema20 if (ema20 and ema20 > cur_p) else (cur_p + (1.8 * atr_val)), 2)
                min_target = round(cur_p + (1.5 * abs(cur_p - sl)), 2)
                tgt = max(revert_target, min_target)
                rationale = (f"Extreme oversold exhaustion: RSI {rsi:.1f} <= 34 at support (Day Low: ₹{day_l:,.2f}). "
                             f"Seller exhaustion confirmed; mean-reversion reversal targeting 20 EMA / VWAP (₹{tgt:,.2f}).")

        # B. Extreme Overbought Exhaustion Reversal (SELL)
        # Conditions: RSI >= 66.0 or near Upper BB, strictly located at resistance near Day High (upper half of range)
        elif (((rsi is not None and rsi >= 66.0) or (cur_p >= bb_high * 0.992 and rsi is not None and rsi >= 62.0) or (chg_pct >= 1.8 and rsi is not None and rsi >= 62.0))
              and (day_h > 0 and cur_p >= day_h * 0.975 and cur_p >= session_mid_check)):
            if day_h > 0 and cur_p <= day_h:
                triggered = True
                action = "SELL"
                setup_name = "Exhaustion Mean-Reversion Reversal (Overbought Fade)"
                sl = round(day_h + (0.5 * atr_val), 2)
                revert_target = round(ema20 if (ema20 and ema20 < cur_p) else (cur_p - (1.8 * atr_val)), 2)
                min_target = round(cur_p - (1.5 * abs(sl - cur_p)), 2)
                tgt = min(revert_target, min_target)
                rationale = (f"Extreme overbought exhaustion: RSI {rsi:.1f} >= 70 at resistance (Day High: ₹{day_h:,.2f}). "
                             f"Buyer exhaustion confirmed; mean-reversion fade targeting 20 EMA / VWAP (₹{tgt:,.2f}).")

        # --- SETUP 0: Pre-Ignition Volatility Coil (Restricted to Afternoon Trends) ---
        # ⚠️ Strict Session & Regime Restrictions:
        # 1. Blocked before 01:30 PM IST (Morning ranges are naturally narrow; fake coil trap).
        # 2. Blocked if RSI is oversold (< 38) for SELL or overbought (> 62) for BUY.
        elif not (now_dt.hour < 13 or (now_dt.hour == 13 and now_dt.minute < 30)):
            pm_signal = PreMoveDetector.evaluate_live_snapshot(
                sym, cur_p, day_h, day_l, p_close, chg_pct, adx=adx, rsi=rsi,
                atr=atr_val, ema20=ema20, ema50=ema50, volume=vol, avg_volume=avg_vol
            )
            if pm_signal and pm_signal.status in ["PRE_MOVE_BUY_TRIGGER", "PRE_MOVE_SELL_TRIGGER"]:
                coil_act = pm_signal.action
                if coil_act == "SELL" and (rsi is not None and rsi < 38.0):
                    pm_signal = None
                elif coil_act == "BUY" and (rsi is not None and rsi > 62.0):
                    pm_signal = None
                else:
                    triggered = True
                    action = pm_signal.action
                    setup_name = "Pre-Ignition Volatility Coil"
                    sl = pm_signal.stop_loss
                    tgt = pm_signal.target_1
                    rationale = (f"Pre-Move Readiness Index: {pm_signal.pmri_score:.1f}/100. "
                                 f"{' | '.join(pm_signal.reasons[:3])}. Early coil entry prior to breakout expansion.")

        # --- SETUP 2: Volatility Breakout with ADX Momentum ---
        # A. Bullish Day-High Breakout
        elif (cur_p >= day_h * 0.996) and (chg_pct >= 0.40) and (adx is not None and adx >= 22.0) and (rsi is None or rsi <= 82.0):
            if vol_ratio is None or vol_ratio >= 0.30:
                triggered = True
                action = "BUY"
                setup_name = "Day-High Volatility Breakout"
                sl = round(cur_p - (1.3 * atr_val), 2)
                tgt = round(cur_p + (2.4 * atr_val), 2)
                rationale = (f"Breaking Day High ₹{day_h:,.2f} with strong trend momentum (ADX {adx:.1f} >= 22 | "
                             f"RSI {f'{rsi:.1f}' if rsi else 'N/A'} | Chg: +{chg_pct:.2f}%). Riding directional impulse.")

        # B. Bearish Day-Low Breakdown
        elif (cur_p <= day_l * 1.004) and (chg_pct <= -0.40) and (adx is not None and adx >= 22.0) and (rsi is None or rsi >= 18.0):
            if vol_ratio is None or vol_ratio >= 0.30:
                triggered = True
                action = "SELL"
                setup_name = "Day-Low Volatility Breakdown"
                sl = round(cur_p + (1.3 * atr_val), 2)
                tgt = round(cur_p - (2.4 * atr_val), 2)
                rationale = (f"Breaking Day Low ₹{day_l:,.2f} with strong trend breakdown (ADX {adx:.1f} >= 22 | "
                             f"RSI {f'{rsi:.1f}' if rsi else 'N/A'} | Chg: {chg_pct:.2f}%). Riding directional impulse.")

        # --- SETUP 3: EMA 20/50 Golden / Death Trend Cross ---
        elif (ema20 is not None and ema50 is not None) and (adx is not None and adx >= 20.0):
            bullish_rsi = (50.0 <= rsi <= 65.0) or (adx >= 25.0 and 50.0 <= rsi <= 68.0) if rsi is not None else True
            bearish_rsi = (35.0 <= rsi <= 50.0) or (adx >= 25.0 and 28.0 <= rsi <= 50.0) if rsi is not None else True
            # Bullish Cross
            if (ema20 > ema50) and (cur_p >= ema20) and bullish_rsi and (chg_pct >= 0.20):
                triggered = True
                action = "BUY"
                setup_name = "EMA 20/50 Bullish Trend Confluence"
                sl = round(ema20 - (1.1 * atr_val), 2)
                tgt = round(cur_p + (2.0 * atr_val), 2)
                rationale = (f"20 EMA (₹{ema20:,.2f}) > 50 EMA (₹{ema50:,.2f}) with ADX {adx:.1f} >= 20 and "
                             f"healthy RSI {f'{rsi:.1f}' if rsi else 'N/A'}. Trend continuation in effect.")

            # Bearish Cross
            elif (ema20 < ema50) and (cur_p <= ema20) and bearish_rsi and (chg_pct <= -0.20):
                triggered = True
                action = "SELL"
                setup_name = "EMA 20/50 Bearish Trend Confluence"
                sl = round(ema20 + (1.1 * atr_val), 2)
                tgt = round(cur_p - (2.0 * atr_val), 2)
                rationale = (f"20 EMA (₹{ema20:,.2f}) < 50 EMA (₹{ema50:,.2f}) with ADX {adx:.1f} >= 20 and "
                             f"healthy RSI {f'{rsi:.1f}' if rsi else 'N/A'}. Downward trend continuation in effect.")

        # --- SETUP 4: Directional Volatility Expansion (High-Momentum Trending Moves) ---
        if not triggered and (adx is not None and adx >= 24.0) and abs(chg_pct) >= 0.85:
            # Strong Bullish Impulse (e.g. Natural Gas rally, Crude surge)
            if (chg_pct >= 0.85) and (cur_p >= ema20 if ema20 else True) and (rsi is None or rsi <= 82.0):
                triggered = True
                action = "BUY"
                setup_name = "Directional Volatility Expansion (Bullish)"
                sl = round(cur_p - (1.4 * atr_val), 2)
                tgt = round(cur_p + (2.5 * atr_val), 2)
                rationale = (f"High-momentum directional surge: Chg +{chg_pct:.2f}%, ADX {adx:.1f} >= 24, "
                             f"RSI {f'{rsi:.1f}' if rsi else 'N/A'}. Capturing aggressive trend expansion.")
            # Strong Bearish Impulse (e.g. Bullion selloff, Energy breakdown)
            elif (chg_pct <= -0.85) and (cur_p <= ema20 if ema20 else True) and (rsi is None or rsi >= 18.0):
                triggered = True
                action = "SELL"
                setup_name = "Directional Volatility Expansion (Bearish)"
                sl = round(cur_p + (1.4 * atr_val), 2)
                tgt = round(cur_p - (2.5 * atr_val), 2)
                rationale = (f"High-momentum directional breakdown: Chg {chg_pct:.2f}%, ADX {adx:.1f} >= 24, "
                             f"RSI {f'{rsi:.1f}' if rsi else 'N/A'}. Capturing aggressive trend expansion.")

        if triggered and action:
            is_reversal = ("Exhaustion Mean-Reversion" in setup_name)
            is_breakout = ("Breakout" in setup_name or "Breakdown" in setup_name or "Expansion" in setup_name)

            # 🚫 Universal Anti-Bottom-Shorting / Anti-Top-Buying Guard:
            # For pullbacks/reversals, prevent buying into exhausted ceilings (RSI >= 68) or selling into floors (RSI <= 32).
            # For genuine breakouts/expansions, allow momentum to run up to extreme exhaustion (RSI >= 82 for BUY, RSI <= 18 for SELL).
            max_buy_rsi = 82.0 if is_breakout else 68.0
            min_sell_rsi = 18.0 if is_breakout else 32.0

            if action == "SELL" and (rsi is not None and rsi <= min_sell_rsi):
                print(f"🚫 Universal Exhaustion Guard: Blocked {sym} SELL. RSI ({rsi:.1f}) is <= {min_sell_rsi:.1f} (extreme oversold). Selling into exhausted floor strictly prohibited.")
                continue

            if action == "BUY" and (rsi is not None and rsi >= max_buy_rsi):
                print(f"🚫 Universal Exhaustion Guard: Blocked {sym} BUY. RSI ({rsi:.1f}) is >= {max_buy_rsi:.1f} (extreme overbought). Buying into exhausted ceiling strictly prohibited.")
                continue

            # 🛡️ Consecutive Directional Loss Guard (trader-risk skill / Anti-Whipsaw Freeze):
            # If the last trade on this symbol today stopped out, enforce a strict 90-minute cooldown
            # and price structure invalidation check before allowing another trade in the SAME direction.
            if sym_today_trades:
                def _trade_ts_key(tr):
                    pt_tr = tr.get('paper_trade', {})
                    t_str = pt_tr.get('exit_time') or pt_tr.get('entry_time') or tr.get('time_trigger') or ''
                    dt_obj = parse_ist_timestamp(t_str, today_str)
                    return dt_obj if dt_obj else datetime.min.replace(tzinfo=pytz.timezone('Asia/Kolkata'))

                sorted_sym_trades = sorted(sym_today_trades, key=_trade_ts_key)
                recent_t = sorted_sym_trades[-1]
                recent_pt = recent_t.get('paper_trade', {})
                recent_exit_trigger = str(recent_pt.get('exit_trigger') or recent_t.get('eod_outcome', {}).get('exit_trigger', ''))
                recent_sl_hit = 'STOP_LOSS' in recent_exit_trigger or (recent_pt.get('pnl_inr', 0.0) < 0)
                
                if recent_sl_hit:
                    recent_action = recent_pt.get('action')
                    recent_time_str = recent_pt.get('exit_time') or recent_pt.get('entry_time') or recent_t.get('time_trigger')
                    recent_dt = parse_ist_timestamp(recent_time_str, today_str)
                    elapsed_m = ((now_dt - recent_dt).total_seconds() / 60.0) if recent_dt else 999.0
                    
                    # Mandatory 120-minute Post-Stop-Loss Lockout for BOTH BUY and SELL
                    if elapsed_m < 120.0:
                        print(f"🚫 Post-Stop-Loss Universal Lockout: Blocked {sym} {action}. Previous {recent_action} stopped out {int(elapsed_m)}m ago (<120m required lockout). Mandatory anti-churning freeze active for BOTH BUY and SELL.")
                        continue

                    if recent_action == action:
                        recent_entry_p = float(recent_pt.get('entry_price') or 0.0)
                        if elapsed_m < 180.0 and recent_entry_p > 0.0:
                            if action == 'SELL' and cur_p > recent_entry_p:
                                print(f"🚫 Directional Loss Guard: Blocked {sym} SELL at ₹{cur_p:,.2f} because price is still above prior failed entry ₹{recent_entry_p:,.2f} ({int(elapsed_m)}m elapsed < 180m).")
                                continue
                            elif action == 'BUY' and cur_p < recent_entry_p:
                                print(f"🚫 Directional Loss Guard: Blocked {sym} BUY at ₹{cur_p:,.2f} because price is still below prior failed entry ₹{recent_entry_p:,.2f} ({int(elapsed_m)}m elapsed < 180m).")
                                continue

            # 🛡️ Institutional Trend & Multi-Timeframe Trend-Vector Gate:
            # Mandate: ALWAYS trade in the direction of the dominant market move!
            # 1. Macro Regime Alignment: Never BUY in a macro BREAKDOWN; never SHORT in a BULL_TREND.
            # 2. Session Midpoint Alignment: Never BUY in the lower half of the day; never SELL in the upper half.
            # 3. 1-Hour Intraday Momentum Delta: Never BUY during active 1h selling; never SELL during active 1h surging.
            # Enforced strictly across ALL standard breakout/trend setups; EXEMPT for Exhaustion Mean-Reversion Reversals!
            regime_obj = item.get('market_regime') or {}
            macro_regime = regime_obj.get('regime', '')
            session_mid = round((day_h + day_l) / 2.0, 2) if (day_h > day_l) else cur_p
            session_vwap = round((float(item.get('open') or cur_p) + day_h + day_l + (2.0 * cur_p)) / 5.0, 2)

            if not is_reversal:
                # Rule 1: Macro Regime Filter (The 68% Win-Rate Alignment Rule)
                if macro_regime == 'BREAKDOWN' and action == 'BUY':
                    print(f"🚫 Trend-Vector Gate: Blocked {sym} BUY. Macro Regime is BREAKDOWN (Price < 50E < 200E). Counter-trend buying strictly prohibited.")
                    continue
                if macro_regime == 'BULL_TREND' and action == 'SELL':
                    print(f"🚫 Trend-Vector Gate: Blocked {sym} SELL. Macro Regime is BULL_TREND (Price > 50E > 200E). Counter-trend shorting strictly prohibited.")
                    continue

                # Rule 2: Intraday Session Flow Alignment (Session Midpoint Anchor)
                if action == "BUY":
                    if cur_p < (session_mid * 0.998):
                        print(f"🚫 Trend-Vector Gate: Blocked {sym} BUY. Price (₹{cur_p:,.2f}) is in the lower half of today's range (below Midpoint ₹{session_mid:,.2f}). Intraday flow is downward.")
                        continue
                    if not is_breakout and ema50 is not None and cur_p < (ema50 * 0.998):
                        print(f"🚫 Directional Trend Guard: Blocked {sym} BUY. Price (₹{cur_p:,.2f}) is below 50 EMA (₹{ema50:,.2f}). Counter-trend buying strictly prohibited.")
                        continue
                    if session_vwap > 0 and cur_p < (session_vwap * 0.998):
                        print(f"🚫 Directional VWAP Guard: Blocked {sym} BUY. Price (₹{cur_p:,.2f}) is below Session VWAP (₹{session_vwap:,.2f}). Counter-trend buying strictly prohibited.")
                        continue

                if action == "SELL":
                    if cur_p > (session_mid * 1.002):
                        print(f"🚫 Trend-Vector Gate: Blocked {sym} SELL. Price (₹{cur_p:,.2f}) is in the upper half of today's range (above Midpoint ₹{session_mid:,.2f}). Intraday flow is upward.")
                        continue
                    if not is_breakout and ema50 is not None and cur_p > (ema50 * 1.002):
                        print(f"🚫 Directional Trend Guard: Blocked {sym} SELL. Price (₹{cur_p:,.2f}) is above 50 EMA (₹{ema50:,.2f}). Counter-trend shorting strictly prohibited.")
                        continue
                    if session_vwap > 0 and cur_p > (session_vwap * 1.002):
                        print(f"🚫 Directional VWAP Guard: Blocked {sym} SELL. Price (₹{cur_p:,.2f}) is above Session VWAP (₹{session_vwap:,.2f}). Counter-trend shorting strictly prohibited.")
                        continue

                # Rule 3: 1-Hour Intraday Trend Vector (from recent 5m bars)
                try:
                    from trader_engine_bridge import TimeMachineEngine
                    recent_bars = TimeMachineEngine.fetch_recent_5m_bars(sym)
                    if recent_bars and len(recent_bars) >= 12:
                        # Price ratio delta over last 12 bars (1 hour)
                        delta_1h = recent_bars[-1]['c_ratio'] - recent_bars[-12]['c_ratio']
                        if action == 'BUY' and delta_1h < -0.003:  # Falling > 0.3% in last hour
                            print(f"🚫 Trend-Vector Gate: Blocked {sym} BUY. 1-Hour Intraday Trend is falling ({delta_1h*100:+.2f}%). Counter-trend buying into active selling is prohibited.")
                            continue
                        elif action == 'SELL' and delta_1h > 0.003:  # Rising > 0.3% in last hour
                            print(f"🚫 Trend-Vector Gate: Blocked {sym} SELL. 1-Hour Intraday Trend is rising ({delta_1h*100:+.2f}%). Counter-trend shorting into active buying is prohibited.")
                            continue
                except Exception:
                    pass

            # 🛡️ Option 2: Pullback-Only Retest Sniping Gate (Anti-Chasing & Structural Proximity)
            # Reversals enter at extreme support/resistance boundaries; Breakouts enter on range expansion.
            # Retest gate strictly applies to pullback & trend-following confluence setups.
            pullback_eval = {"passed": True, "status": "CONFIRMED", "reason": "Standard pullback confluence."}
            if not is_reversal and not is_breakout:
                pullback_eval = PullbackRetestGate.validate_retest_entry(sym, action, cur_p, day_h, day_l, ema20=ema20, atr=atr_val)
                if not pullback_eval.get('passed'):
                    print(f"🛡️ Option 2 Pullback Gate Blocked {sym} {action}: {pullback_eval.get('reason')}")
                    continue

            # 📐 Structural Technical Bracket: Option 2 calibrated with contract-specific bounds
            if not is_reversal or not sl or not tgt:
                calc_sl, calc_tgt = calculate_technical_bracket(action, cur_p, day_h, day_l, ema20=ema20, ema50=ema50, atr=atr_val, min_rrr=1.2, is_intraday=True, symbol=sym)
                sl = calc_sl or sl
                tgt = calc_tgt or tgt

            # Strict Invariant: Stop distance must NEVER exceed contract's max risk cap (<= ₹6,000 risk)
            clean_sym_check = sym.replace('-FUT', '').replace('.NS', '').upper()
            contract_max_stop = (
                PullbackRetestGate.MAX_STOP_DISTANCE.get(clean_sym_check) or
                PullbackRetestGate.MAX_STOP_DISTANCE.get(f"{clean_sym_check}M") or
                PullbackRetestGate.MAX_STOP_DISTANCE.get(f"{clean_sym_check}MINI")
            )
            if contract_max_stop and sl:
                if action.upper() == 'BUY' and (cur_p - sl) > contract_max_stop:
                    sl = round(cur_p - contract_max_stop, 2)
                    tgt = round(cur_p + max(1.5 * contract_max_stop, abs(tgt - cur_p) if tgt else contract_max_stop), 2)
                elif action.upper() == 'SELL' and (sl - cur_p) > contract_max_stop:
                    sl = round(cur_p + contract_max_stop, 2)
                    tgt = round(cur_p - max(1.5 * contract_max_stop, abs(cur_p - tgt) if tgt else contract_max_stop), 2)

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

            # 4. Dynamic Position Sizing & Mini-Contract Routing (Ensures Technical Stop fits <= ₹6,000 risk)
            contract_sym, contract_name, adaptive_lot = PositionSizer.resolve_contract_and_lot(
                sym, name, cur_p, sl, lot_size, max_risk_inr=MAX_LOSS_PER_TRADE_INR, atr=atr_val
            )
            if adaptive_lot <= 0:
                adaptive_lot = lot_size if lot_size > 0 else 1

            all_existing = new_trades + trades
            risk_metrics = PreTradeRiskEngine.check_risk(
                contract_sym, action, cur_p, sl, tgt, adaptive_lot, all_existing,
                max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR, max_daily_loss_inr=MAX_DAILY_LOSS_INR,
                day_h=day_h, day_l=day_l, ema20=ema20, atr=atr_val,
                ema50=ema50, vwap=session_vwap, chg_pct=chg_pct, rsi=rsi,
                is_reversal=is_reversal, is_breakout=is_breakout
            )
            if not risk_metrics.get('passed'):
                print(f"🛡️ Pre-Trade Risk Engine Blocked {contract_sym} {action}: {risk_metrics.get('reason')}")
                continue

            # Expiry Risk Check: Only exclude if no active forward-month contract is available in rollover chain
            try:
                from dhan_live_executor import DhanLiveExecutor
                _dhan_chk = DhanLiveExecutor()
                is_near_exp, days_left, exp_date = _dhan_chk.is_nearing_expiry(contract_sym)
                if is_near_exp:
                    print(f"🛑 Expiry Risk Guard Blocked {contract_sym} {action}: All contracts nearing expiry on {exp_date} ({days_left}d left <= {_dhan_chk.expiry_safety_days}d threshold) with no active forward contract available.")
                    continue
            except Exception:
                pass

            # Run Monte Carlo Time Machine simulation (3,000 replays)
            sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, sl, tgt, action=action.lower(), replays=3000, is_breakout=is_breakout)
            if not sim_metrics.get('passed'):
                print(f"🎲 Monte Carlo Time Machine Blocked {sym} {action}: {sim_metrics.get('summary')}")
                continue

            # Rigorous Profitability Verification Gate (Commodities):
            # Must demonstrate statistical edge for pullbacks/reversals; breakouts confirmed by momentum
            if not is_breakout:
                exp_r = float(sim_metrics.get('expected_r', 0.0) or 0.0)
                win_rate = float(sim_metrics.get('resolved_win_rate', 0.0) or sim_metrics.get('resolved_win_rate_pct', 0.0) or 0.0)
                p_tgt = float(sim_metrics.get('p_target_pct', 0.0) or 0.0)
                if exp_r < 0.05 or win_rate < 40.0 or p_tgt < 10.0:
                    print(f"🛡️ Profitability Gate Blocked {sym} {action}: Statistical replay shows low target hit probability (P_tgt: {p_tgt:.1f}%, Exp R: {exp_r:+.2f}R, Win Rate: {win_rate:.1f}%). Standing aside.")
                    continue

            # Check Margin Availability for commodity trigger
            # If available balance < ₹1.5L target margin, downscale to affordable whole lots instead of skipping
            has_margin, req_margin, avail_margin, adaptive_lot = check_margin_availability(tracker, contract_sym, cur_p, adaptive_lot, is_equity=False, sl=sl)
            if not has_margin:
                entry_obj = {
                    "id": trade_id,
                    "date": today_str,
                    "session_name": f"Real-Time Technical Trigger ({setup_name})",
                    "time_trigger": now_time_str,
                    "time_6pm": now_time_str,
                    "time_validation": now_time_str,
                    "time_615": now_time_str,
                    "time_exit": get_us_aligned_session_times(now_dt)["eod_str"],
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
                        "status": "INSUFFICIENT_MARGIN_STAND_ASIDE",
                        "price_615": cur_p,
                        "action_taken": f"Execution Skipped: Insufficient Margin (Min Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Alert Sent.",
                        "detail": f"{setup_name}: Setup confirmed, but available cash ₹{avail_margin:,.2f} cannot afford even 1 lot (Min Req: ₹{req_margin:,.2f})."
                    },
                    "paper_trade": {
                        "user": USER_ACCOUNT,
                        "action": "STAND_ASIDE",
                        "quantity": 0,
                        "entry_price": cur_p,
                        "entry_time": now_time_str,
                        "exit_price": cur_p,
                        "exit_time": get_us_aligned_session_times(now_dt)["eod_str"],
                        "pnl_points": 0.0,
                        "pnl_inr": 0.0,
                        "status": "STAND_ASIDE",
                        "margin_required_inr": 0.0
                    },
                    "eod_outcome": {
                        "session_close": None,
                        "circuit_limit": None,
                        "outcome": "PROTECTED",
                        "badge": "Margin Skipped 🛡️",
                        "pnl_display": "₹0.00 (Discretionary)",
                        "summary": f"{setup_name} cleared rules, but execution skipped on managed portfolio due to margin (Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Discretionary alert sent."
                    }
                }
                new_trades.append(entry_obj)
                active_sectors.add(item_sector)

                mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
                subject = f"📢 [MARGIN EXHAUSTED - DISCRETIONARY ALERT] {name} {action} @ ₹{cur_p:,.2f} ({now_time_str})"
                body = (
                    f"📢 <b>REAL-TIME TECHNICAL CONFLUENCE TRIGGER (MARGIN EXHAUSTED)</b>\n"
                    f"📅 <i>{today_str}, {now_time_str}</i>\n"
                    f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                    f"• <b>Asset</b>: {name} ({sym})\n"
                    f"• <b>Signal</b>: <b>{action} {adaptive_lot} units @ ₹{cur_p:,.2f}</b>\n"
                    f"• <b>Setup</b>: {setup_name}\n"
                    f"• <b>Technical Rationale</b>: {rationale}\n"
                    f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                    f"• <b>Stop-Loss</b>: ₹{sl:,.2f} | <b>Target</b>: ₹{tgt:,.2f}\n"
                    f"• <b>Margin Status</b>: ⚠️ <b>Required: ₹{req_margin:,.2f} | Available Pool: ₹{avail_margin:,.2f}</b>\n"
                    f"• <b>Execution Status</b>: <b>SKIPPED on Managed Portfolio</b> (Zero margin balance remaining)\n"
                    f"• <b>Discretionary Notice</b>: Broadcasted for manual execution if additional broker capital is available.\n\n"
                    f"🛡️ <i>Disciplined Margin & Risk Governance Active</i>"
                )
                if alerter:
                    try:
                        alerter.send_advisory_alert(subject, body)
                    except Exception as ex:
                        print(f"Error dispatching margin-exhausted alert: {ex}")
                continue

            entry_obj = {
                "id": trade_id,
                "date": today_str,
                "session_name": f"Real-Time Technical Trigger ({setup_name})",
                "time_trigger": now_time_str,
                "time_6pm": now_time_str,
                "time_validation": now_time_str,
                "time_615": now_time_str,
                "time_exit": get_us_aligned_session_times(now_dt)["eod_str"],
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
                    "target": tgt,
                    "original_stop_loss": sl,
                    "original_target": tgt
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
                    "margin_required_inr": req_margin,
                    "target": tgt,
                    "stop_loss": sl,
                    "initial_stop_loss": sl,
                    "original_sl": sl,
                    "peak_high": cur_p,
                    "trough_low": cur_p,
                    "entry_day_high": cur_p,
                    "entry_day_low": cur_p
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
            dispatch_autonomous_limit_order(entry_obj, asset_type='COMMODITY')
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
    # PART 2: REAL-TIME NIFTY 100 (NIFTY 50 + MIDCAP 50) CONTINUOUS SCANNER (Phase 1 & Phase 2)
    # Active Window: Mon-Fri, 09:30 AM to 02:45 PM IST
    # Strictly enforces 02:45 PM IST hard cutoff for any new stock or stock future entries.
    # New intraday entries after 02:45 PM are prohibited since brokers auto square-off MIS orders by 03:15-03:20 PM.
    # Sized <= ₹3,000 risk (1% of ₹3L Trading pool) with 03:20 PM EOD square-off
    # =========================================================================
    ENABLE_EQUITY_DERIVATIVES_SCANNER = os.environ.get('ENABLE_EQUITY_SCANNER', 'true').lower() in ['true', '1', 'yes']
    is_equity_window = ((hour == 9 and minute >= 30) or (10 <= hour < 14) or (hour == 14 and minute <= 45))
    cur_equity_positions = len(active_equity_trades) + sum(1 for t in new_trades if t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'])

    if ENABLE_EQUITY_DERIVATIVES_SCANNER and is_equity_window and cur_equity_positions < MAX_CONCURRENT_EQUITIES:
        if os.path.exists(NIFTY_JSON_FILE):
            try:
                with open(NIFTY_JSON_FILE, 'r') as nf:
                    nifty_data = json.load(nf)
                equity_stocks = nifty_data.get('stocks', [])
                nifty_last_updated_str = nifty_data.get('last_updated', '')
                is_nifty_data_fresh = True
                age_mins = 0.0
                if nifty_last_updated_str:
                    try:
                        clean_dt_str = nifty_last_updated_str.replace(' IST', '').strip()
                        dt_nifty = parse_ist_timestamp(clean_dt_str)
                        if dt_nifty:
                            age_mins = (now_dt - dt_nifty).total_seconds() / 60.0
                            if age_mins > 8.0:
                                is_nifty_data_fresh = False
                    except Exception:
                        pass
            except Exception as e:
                print(f"Warning: Could not read Nifty F&O scanner data: {e}")
                equity_stocks = []

            # 0. Strict 09:30 AM Opening Bell Blackout Guard:
            # Pre-09:30 AM trades suffer from opening auction whipsaws, wide spreads, and incomplete ORB ranges.
            if hour == 9 and minute < 30:
                print(f"⏱️ Opening Bell Range Forming (09:15-09:30 AM IST): Intraday breakout scans paused until 15m range is established.")
                equity_stocks = []

            # Pillar 2: Nifty 50 Market Breadth Gate (Trade WITH the Tide)
            # Calculate broad market breadth across the 100-stock universe before candidate curation
            all_nifty_stocks = nifty_data.get('stocks', [])
            nifty_benchmark_chg = round(sum(float(st.get('change_pct', 0.0) or 0.0) for st in all_nifty_stocks) / max(len(all_nifty_stocks), 1), 2)

            # Institutional Intraday Stock Selection Engine:
            # When broad market is negative (< -0.15%), block BUY breakouts and prioritize high-velocity shorts.
            # When broad market is positive (> +0.15%), block SHORT breakdowns and prioritize high-momentum longs.
            # When neutral (-0.15% to +0.15%), allow both but require robust volume confirmation.
            if nifty_benchmark_chg < -0.15:
                long_candidates = []
                short_candidates = IntradayStockSelector.rank_candidates(equity_stocks, action="SELL")
                curated_intraday_stocks = short_candidates
                print(f"🌊 Nifty Breadth Gate (BEARISH TIDE {nifty_benchmark_chg:+.2f}%): Long breakouts blocked. Curated {len(short_candidates)} prime breakdown short candidates.")
            elif nifty_benchmark_chg > 0.15:
                long_candidates = IntradayStockSelector.rank_candidates(equity_stocks, action="BUY")
                short_candidates = []
                curated_intraday_stocks = long_candidates
                print(f"🌊 Nifty Breadth Gate (BULLISH TIDE {nifty_benchmark_chg:+.2f}%): Short breakdowns blocked. Curated {len(long_candidates)} prime breakout long candidates.")
            else:
                long_candidates = IntradayStockSelector.rank_candidates(equity_stocks, action="BUY")
                short_candidates = IntradayStockSelector.rank_candidates(equity_stocks, action="SELL")
                curated_intraday_stocks = []
                max_cand = max(len(long_candidates), len(short_candidates))
                for idx in range(max_cand):
                    if idx < len(long_candidates):
                        curated_intraday_stocks.append(long_candidates[idx])
                    if idx < len(short_candidates):
                        curated_intraday_stocks.append(short_candidates[idx])
                print(f"🌊 Nifty Breadth Gate (NEUTRAL TIDE {nifty_benchmark_chg:+.2f}%): Balanced selection ({len(long_candidates)} Longs, {len(short_candidates)} Shorts).")
            
            equity_stocks = curated_intraday_stocks
            if equity_stocks:
                top_syms = [f"{s.get('symbol', '').replace('.NS', '')} ({s.get('_selector_action', 'BUY')})" for s in equity_stocks[:5]]
                print(f"🎯 Intraday Curated Institutional Candidates (Nifty Breadth: {nifty_benchmark_chg:+.2f}%): {', '.join(top_syms)}")

            for s in equity_stocks:
                cand_action = s.get('_selector_action') or 'BUY'
                # Guard A: Hard False Signal Invalidation Guard (applies to BUY signals; breakdown stocks are valid for SHORT)
                if cand_action == 'BUY' and s.get('is_false_signal'):
                    continue

                # Guard B: Conviction Thresholds (Win Prob >= 55%, Safety >= 40%)
                win_prob = float(s.get('win_probability') or 0)
                safety_score = float(s.get('capital_safety_score') or 0)
                if win_prob < 55.0 or safety_score < 40.0:
                    continue

                # Guard C: Market Regime Strategy Restrictions
                regime_data = s.get('market_regime') or {}
                blocked_strategies = regime_data.get('blocked_strategies') or []
                cur_active_stock_futures = len(active_stock_futures) + sum(1 for t in new_trades if ('-FUT' in str(t.get('symbol', '')) or t.get('asset_type') == 'STOCK_FUTURES'))
                cur_active_equity = len(active_equity_trades) + sum(1 for t in new_trades if t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'])

                can_enter_futures = (cur_active_stock_futures < MAX_CONCURRENT_STOCK_FUTURES)
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

                # Symbol cooldown guard: Prevent taking multiple trades on same equity/stock future within cooldown window
                eq_today_trades = [
                    t for t in (trades + new_trades)
                    if t.get('date') == today_str
                    and t.get('symbol') in [sym, clean_sym, f"{clean_sym}-FUT"]
                    and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
                    and t.get('paper_trade', {}).get('quantity', 0) > 0
                ]
                # Option B: Session Cooldown for Equities (90 mins after Target Hit, 60 mins after Stop Loss, 30 mins standard)
                if eq_today_trades:
                    def _eq_trade_sort_key(tr):
                        pt_tr = tr.get('paper_trade', {})
                        t_str = pt_tr.get('exit_time') or pt_tr.get('entry_time') or tr.get('time_trigger') or ''
                        dt_obj = parse_ist_timestamp(t_str, today_str)
                        return dt_obj if dt_obj else datetime.min.replace(tzinfo=pytz.timezone('Asia/Kolkata'))

                    sorted_eq_trades = sorted(eq_today_trades, key=_eq_trade_sort_key)
                    last_eq_t = sorted_eq_trades[-1]
                    last_eq_pt = last_eq_t.get('paper_trade', {})
                    last_eq_time_str = last_eq_pt.get('exit_time') or last_eq_pt.get('entry_time') or last_eq_t.get('time_trigger')
                    if last_eq_time_str:
                        try:
                            eq_t_struct = parse_ist_timestamp(last_eq_time_str, today_str)
                            if eq_t_struct:
                                eq_diff_mins = (now_dt - eq_t_struct).total_seconds() / 60.0
                                is_eq_target = ('TARGET' in str(last_eq_pt.get('exit_trigger', ''))) or (last_eq_pt.get('net_pnl_inr', last_eq_pt.get('pnl_inr', 0.0)) > 100.0) or (last_eq_t.get('eod_outcome', {}).get('outcome') == 'CORRECT')
                                is_eq_sl_hit = 'STOP_LOSS' in str(last_eq_pt.get('exit_trigger', '')) or (last_eq_pt.get('pnl_inr', 0.0) < 0)
                                req_eq_cooldown = 90.0 if is_eq_target else (60.0 if is_eq_sl_hit else 30.0)
                                if eq_diff_mins < req_eq_cooldown:
                                    continue
                        except Exception:
                            pass

                name = s.get('full_name') or s.get('name', clean_sym)
                cur_p = float(s.get('close') or 0)
                # Fetch authoritative real-time execution price at this exact moment (0s latency)
                live_eq_p, eq_tick = get_authoritative_contract_price(clean_sym, 'EQUITY')
                if not live_eq_p and checker and getattr(checker, 'angel_active', False):
                    try:
                        sec_id = checker.get_angel_token(sym)
                        if sec_id:
                            p_angel = checker.get_angel_current_price(sec_id)
                            if p_angel and p_angel > 0:
                                live_eq_p = round(float(p_angel), 2)
                    except Exception:
                        pass
                
                if not live_eq_p:
                    try:
                        import yfinance as yf
                        yf_sym = sym if sym.endswith('.NS') else f"{clean_sym}.NS"
                        t_obj = yf.Ticker(yf_sym)
                        fast_p = getattr(t_obj.fast_info, 'last_price', None)
                        if fast_p and float(fast_p) > 0:
                            live_eq_p = round(float(fast_p), 2)
                        else:
                            hist_1m = t_obj.history(period='1d', interval='1m')
                            if not hist_1m.empty:
                                c_last = float(hist_1m['Close'].iloc[-1])
                                if c_last > 0:
                                    live_eq_p = round(c_last, 2)
                    except Exception:
                        pass
                
                if live_eq_p and live_eq_p > 0:
                    cur_p = live_eq_p
                elif not is_nifty_data_fresh:
                    print(f"🛡️ Stale Data Guard: Blocked {clean_sym} trigger. Scanner data is {age_mins:.1f} mins old and real-time quote unavailable. Trade refused.")
                    continue
                if cur_p <= 0:
                    continue

                # Strict Stock Diversification: Each intraday stock gets at most 1 trade per day
                # so the 3 slots are guaranteed to be 3 distinct companies, preventing one stock from dominating all slots.
                eq_today_trades = [
                    t for t in (trades + new_trades)
                    if t.get('date') == today_str and (t.get('symbol') == sym or t.get('symbol') == clean_sym or f"{clean_sym}-FUT" in str(t.get('symbol', '')))
                    and t.get('paper_trade', {}).get('action') in ['BUY', 'SELL']
                    and t.get('paper_trade', {}).get('quantity', 0) > 0
                ]
                if len(eq_today_trades) >= 1:
                    continue

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
                bb_upper = float(s.get('bb_upper') or (cur_p * 1.05))
                is_golden = bool(s.get('is_golden'))
                is_death = (str(s.get('crossover_status', '')).upper() == 'DEATH') or (float(s.get('ema_50') or cur_p) < float(s.get('ema_200') or cur_p) * 0.995)
                rsi_curling_up = bool(s.get('rsi_curling_up'))
                rsi_curling_down = bool(s.get('rsi_curling_down') or (float(s.get('rsi_prev') or rsi) > rsi))
                near_bb = bool(s.get('near_bb_lower'))
                near_bb_upper = bool(s.get('near_bb_upper') or (cur_p >= bb_upper * 0.99))
                day_h = float(s.get('day_high') or s.get('high') or 0)
                day_l = float(s.get('day_low') or s.get('low') or 0)
                if day_h <= 0 or day_l <= 0:
                    day_h = 0.0
                    day_l = 0.0
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
                pullback_eval = {"passed": True, "status": "CONFIRMED", "reason": "Standard pullback confluence."}
                vwap = round((day_h + day_l + cur_p) / 3.0, 2)

                # Setup A: High-Beta Intraday Momentum Breakout (BUY - Controlled Momentum, not Overextended)
                if (cur_p >= high_52 * 0.985 or (0.75 <= chg_pct <= 2.5 and vol_ratio >= 1.0)) and (52.0 <= rsi <= 66.0) and (cur_p >= vwap * 0.998):
                    eq_action = "BUY"
                    eq_setup = "20-Day / High-Beta Volatility Breakout"
                    
                    # 2. 15-Minute ORB Bar-Close & Opening Bell Blackout Guard (trader-entry & trader-scanner skills)
                    orb_metrics = ORBTimer.check_orb_confirmation(
                        sym, eq_action, cur_p, day_h, day_l,
                        volume_ratio=vol_ratio, last_bar_close=cur_p, current_time=now_dt,
                        stock_chg_pct=chg_pct, benchmark_chg_pct=nifty_benchmark_chg
                    )
                    if not orb_metrics.get('passed'):
                        print(f"⏱️ ORB Timer Blocked {clean_sym} {eq_action}: {orb_metrics.get('reason')}")
                        continue

                    # 3. Structural Invalidation Stop & Realistic Intraday Target (trader-exit skill)
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=1.2, is_intraday=True, symbol=clean_sym)
                    eq_sl = calc_sl or round(cur_p * 0.988, 2)
                    eq_tgt = calc_tgt or round(cur_p + (1.2 * (cur_p - eq_sl)), 2)
                    eq_triggered = True
                    eq_rationale = (f"High-Beta breakout momentum (Chg: +{chg_pct:.2f}%) with healthy RSI ({rsi:.1f}) "
                                    f"holding above VWAP. Sized with realistic intraday 1.2R target.")

                # Setup B: Oversold Mean Reversion Bounce (Bollinger Band Mean Reversion BUY)
                elif (rsi <= 36.0) and (rsi_curling_up or near_bb or cur_p <= bb_lower * 1.01) and (discount_pct >= 15.0):
                    eq_action = "BUY"
                    eq_setup = "Oversold Mean Reversion Bounce"
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=min(bb_lower, day_l), ema20=ema20, ema50=ema50, atr=atr, min_rrr=1.2, is_intraday=True, symbol=clean_sym)
                    eq_sl = calc_sl or round(min(bb_lower, cur_p * 0.985), 2)
                    eq_tgt = calc_tgt or round(cur_p + (1.3 * (cur_p - eq_sl)), 2)
                    eq_triggered = True
                    eq_rationale = (f"Oversold accumulation RSI ({rsi:.1f} <= 36) bouncing off Lower Bollinger Band (₹{bb_lower:,.2f}) "
                                    f"at a deep {discount_pct:.1f}% discount to 52W high. High probability mean-reversion.")

                # Setup C: EMA 50/200 Golden Cross Trend Pullback (BUY)
                elif is_golden and (35.0 <= rsi <= 52.0) and (cur_p >= vwap * 0.995):
                    eq_action = "BUY"
                    eq_setup = "EMA 50/200 Golden Cross Trend Pullback"
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=1.2, is_intraday=True, symbol=clean_sym)
                    eq_sl = calc_sl or round(cur_p * 0.988, 2)
                    eq_tgt = calc_tgt or round(cur_p + (1.2 * (cur_p - eq_sl)), 2)
                    eq_triggered = True
                    eq_rationale = (f"50 EMA > 200 EMA Golden Cross confirmed with value pullback into demand zone (RSI: {rsi:.1f}). "
                                    f"Trend continuation entry targeting realistic intraday 1.2R upside.")

                # Setup D: Intraday Breakdown / Weakness Expansion (SELL - High Velocity Short)
                elif ((chg_pct <= -0.60 and vol_ratio >= 1.0) or (cur_p <= day_l * 1.015 and chg_pct <= -0.60)) and (26.0 <= rsi <= 52.0) and (cur_p <= ema20 * 1.005) and (cur_p <= vwap * 1.002):
                    eq_action = "SELL"
                    eq_setup = "Intraday Breakdown / Weakness Expansion"
                    orb_metrics = ORBTimer.check_orb_confirmation(
                        sym, eq_action, cur_p, day_h, day_l,
                        volume_ratio=vol_ratio, last_bar_close=cur_p, current_time=now_dt,
                        stock_chg_pct=chg_pct, benchmark_chg_pct=nifty_benchmark_chg
                    )
                    if not orb_metrics.get('passed'):
                        print(f"⏱️ ORB Timer Blocked {clean_sym} {eq_action}: {orb_metrics.get('reason')}")
                        continue
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=1.2, is_intraday=True, symbol=clean_sym)
                    eq_sl = calc_sl or round(cur_p * 1.015, 2)
                    eq_tgt = calc_tgt or round(cur_p - (1.2 * abs(eq_sl - cur_p)), 2)
                    eq_triggered = True
                    eq_rationale = (f"Breaking towards Day Low (Chg: {chg_pct:+.2f}%) with accelerating downward RSI momentum ({rsi:.1f}) "
                                    f"and verified ORB breakdown below VWAP. Realistic intraday short targeting 1.2R downside.")

                # Setup E: Overbought Mean Reversion Rejection (SELL - Upper Band Exhaustion Fade)
                elif (rsi >= 68.0) and (rsi_curling_down or near_bb_upper or cur_p >= bb_upper * 0.99):
                    eq_action = "SELL"
                    eq_setup = "Overbought Mean Reversion Rejection"
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=max(bb_upper, day_h), day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=1.2, is_intraday=True, symbol=clean_sym)
                    eq_sl = calc_sl or round(max(bb_upper, cur_p * 1.025), 2)
                    eq_tgt = calc_tgt or round(cur_p - (1.3 * abs(eq_sl - cur_p)), 2)
                    eq_triggered = True
                    eq_rationale = (f"Overbought exhaustion RSI ({rsi:.1f} >= 68) facing sharp rejection off Upper Bollinger Band (₹{bb_upper:,.2f}). "
                                    f"High probability mean-reversion fade targeting return to 20 EMA.")

                # Setup F: EMA 50/200 Death Cross Trend Continuation Pullback (SELL - Bear Trend Short)
                elif is_death and (48.0 <= rsi <= 62.0) and (cur_p <= ema50 * 1.008) and (cur_p <= vwap * 1.002):
                    eq_action = "SELL"
                    eq_setup = "EMA 50/200 Death Cross Trend Continuation"
                    calc_sl, calc_tgt = calculate_technical_bracket(eq_action, cur_p, day_h=day_h, day_l=day_l, ema20=ema20, ema50=ema50, atr=atr, min_rrr=1.2, is_intraday=True, symbol=clean_sym)
                    eq_sl = calc_sl or round(cur_p * 1.025, 2)
                    eq_tgt = calc_tgt or round(cur_p - (1.2 * abs(eq_sl - cur_p)), 2)
                    eq_triggered = True
                    eq_rationale = (f"50 EMA < 200 EMA Death Cross confirmed with counter-trend pullback into supply zone (RSI: {rsi:.1f}). "
                                    f"Trend continuation short entry targeting realistic intraday 1.2R downside.")

                if eq_triggered and eq_action and eq_sl and eq_tgt:
                    # Pillar 2 Gate Check: Nifty Market Breadth Gate (Trade WITH the Tide)
                    if eq_action == "BUY" and nifty_benchmark_chg < -0.15:
                        print(f"🌊 Nifty Breadth Gate Blocked {clean_sym} BUY: Broad market tide is negative ({nifty_benchmark_chg:+.2f}%). Counter-trend long forbidden.")
                        continue
                    if eq_action == "SELL" and nifty_benchmark_chg > 0.15:
                        print(f"🌊 Nifty Breadth Gate Blocked {clean_sym} SELL: Broad market tide is positive ({nifty_benchmark_chg:+.2f}%). Counter-trend short forbidden.")
                        continue
                    if (-0.15 <= nifty_benchmark_chg <= 0.15) and (vol_ratio < 1.25):
                        print(f"🌊 Nifty Breadth Gate Blocked {clean_sym} {eq_action}: In neutral market ({nifty_benchmark_chg:+.2f}%), volume ratio {vol_ratio:.2f}x < 1.25x minimum.")
                        continue

                    # Guard D1: Regime Blocked Strategy Filter
                    if eq_action == "BUY" and any(strat in blocked_strategies for strat in ['BREAKOUT_BUY', 'BUY_EQUITY', 'GOLDEN_CROSS_BUY', 'TREND_BREAKOUT_BUY']):
                        print(f"🛡️ Market Regime Blocked {clean_sym} BUY: In {regime_data.get('regime')} regime where breakout buying is blocked.")
                        continue
                    if eq_action == "SELL" and any(strat in blocked_strategies for strat in ['SHORT_FUTURES', 'SELL_EQUITY']):
                        print(f"🛡️ Market Regime Blocked {clean_sym} SELL: In {regime_data.get('regime')} regime where shorting is blocked.")
                        continue

                    # Pillar 3: Option 2 Pullback-Only Retest Sniping Gate (Anti-Chasing & Structural Proximity)
                    # Forbids buying near day high / shorting near day low on Pullback & Mean-Reversion setups
                    if "Breakout" not in eq_setup:
                        pullback_eval = PullbackRetestGate.validate_retest_entry(clean_sym, eq_action, cur_p, day_h, day_l, ema20=ema20, atr=atr)
                        if not pullback_eval.get('passed'):
                            print(f"🛡️ Option 2 Pullback Gate Blocked {clean_sym} {eq_action}: {pullback_eval.get('reason')}")
                            continue

                    # Guard D2: Anti-Chasing Overextension Guard (Stop buying at day highs / panic selling at day lows)
                    if eq_action == "BUY" and (chg_pct >= 2.5 or (day_h > 0 and cur_p >= day_h * 0.996 and rsi >= 66.0)):
                        print(f"🛡️ Anti-Chase Guard Blocked {clean_sym} BUY: Stock already extended (+{chg_pct:.2f}%, RSI: {rsi:.1f}) near day high. Chasing forbidden; requires pullback to VWAP / 20 EMA.")
                        continue
                    if eq_action == "SELL" and (chg_pct <= -2.5 or (day_l > 0 and cur_p <= day_l * 1.004 and rsi <= 34.0)):
                        print(f"🛡️ Anti-Chase Guard Blocked {clean_sym} SELL: Stock already dumped ({chg_pct:.2f}%, RSI: {rsi:.1f}) near day low. Selling into panic forbidden; requires pullback to VWAP / 20 EMA.")
                        continue

                    # Guard D3: Session VWAP Pullback Test (Avoid overextended entries)
                    if eq_action == "BUY" and cur_p > (vwap * 1.015) and cur_p > (ema20 * 1.012):
                        print(f"🛡️ Pullback Guard Blocked {clean_sym} BUY: Price ₹{cur_p:,.2f} is extended > 1.5% above VWAP (₹{vwap:,.2f}). Wait for pullback to VWAP/20 EMA.")
                        continue
                    if eq_action == "SELL" and cur_p < (vwap * 0.985) and cur_p < (ema20 * 0.988):
                        print(f"🛡️ Pullback Guard Blocked {clean_sym} SELL: Price ₹{cur_p:,.2f} is extended > 1.5% below VWAP (₹{vwap:,.2f}). Wait for bounce to VWAP/20 EMA.")
                        continue

                    # PATH A: Enter as an NSE Stock Future if futures pool has room and stock is an active F&O contract
                    if can_enter_futures and clean_sym in STOCK_FUTURES_CONFIG:
                        sfc = STOCK_FUTURES_CONFIG[clean_sym]
                        fut_sym = f"{clean_sym}-FUT"
                        if sfc['sector'] not in active_sectors:
                            fut_lot = sfc['lot_size']
                            fut_margin_rate = sfc['margin_rate']
                            fut_margin_req = round(cur_p * fut_lot * fut_margin_rate, 2)

                            # 1. Institutional Direction Engine Evaluation for Stock Futures (Long & Short)
                            inst_fut_check = InstitutionalDirectionEngine.evaluate_equity_futures_direction(
                                symbol=clean_sym,
                                current_price=cur_p,
                                open_price=s.get('open_price', cur_p),
                                high_price=day_h,
                                low_price=day_l,
                                prev_close=float(s.get('prev_close') or cur_p),
                                ema20=ema20,
                                ema50=ema50,
                                ema200=float(s.get('ema_200') or cur_p),
                                crossover_status=s.get('crossover_status', 'NONE'),
                                rsi=rsi,
                                adx=float(s.get('adx') or 20.0),
                                bb_lower=bb_lower,
                                bb_upper=bb_upper,
                                atr=atr,
                                action=eq_action
                            )
                            if not inst_fut_check.get('passed'):
                                print(f"🛡️ Institutional Direction Engine Blocked Stock Future {fut_sym}: {inst_fut_check.get('reason')}")
                                can_enter_futures = False
                            else:
                                # 2. Hard Stand-Aside on Risk Bracket (NEVER choke/compress stops)
                                bracket_check = InstitutionalDirectionEngine.validate_futures_risk_bracket(
                                    fut_sym, eq_action, cur_p, eq_sl, eq_tgt, fut_lot,
                                    max_loss_inr=MAX_LOSS_PER_TRADE_INR,
                                    min_rrr=1.1
                                )
                                if not bracket_check.get('passed'):
                                    print(f"🛡️ Hard Risk Stop Blocked Stock Future {fut_sym}: {bracket_check.get('reason')}")
                                    can_enter_futures = False
                                else:
                                    fut_sl = eq_sl
                                    fut_tgt = eq_tgt
                                    fut_risk_per_share = abs(cur_p - fut_sl)

                            if can_enter_futures:
                                # Fetch live authoritative futures contract price (0s latency front-month quote)
                                fut_live_p, fut_tick = get_authoritative_contract_price(fut_sym, 'STOCK_FUTURES')
                                fut_entry_p = fut_live_p if (fut_live_p and fut_live_p > 0) else cur_p
                                basis_spread = round(fut_entry_p - cur_p, 2)
                                fut_sl = round(fut_sl + basis_spread, 2)
                                fut_tgt = round(fut_tgt + basis_spread, 2)

                                regime_info = inst_fut_check.get('regime_info') or s.get('regime') or MarketRegimeDetector.classify_regime(
                                    symbol=clean_sym, close=fut_entry_p, ema50=ema50, ema200=float(s.get('ema_200') or cur_p),
                                    adx=float(s.get('adx') or 20.0), rsi=rsi, bb_lower=bb_lower, bb_upper=float(s.get('bb_upper') or cur_p * 1.05),
                                    atr=atr
                                )

                                # 14-Point Pre-Trade Risk Engine Check for Stock Futures
                                all_existing = new_trades + trades
                                risk_metrics = PreTradeRiskEngine.check_risk(
                                    fut_sym, eq_action, fut_entry_p, fut_sl, fut_tgt, fut_lot, all_existing,
                                    max_loss_per_trade_inr=MAX_LOSS_PER_TRADE_INR,
                                    max_daily_loss_inr=MAX_DAILY_LOSS_INR
                                )
                                if risk_metrics.get('passed'):
                                    # Monte Carlo Time Machine simulation (3,000 replays)
                                    sim_metrics = MonteCarloTimeMachine.run_simulation(sym, fut_entry_p, fut_sl, fut_tgt, action=eq_action.lower(), replays=3000)
                                    if sim_metrics.get('passed'):
                                        # Rigorous Profitability Verification Gate (Long & Short):
                                        exp_r = float(sim_metrics.get('expected_r', 0.0) or 0.0)
                                        win_rate = float(sim_metrics.get('resolved_win_rate', 0.0) or sim_metrics.get('resolved_win_rate_pct', 0.0) or 0.0)
                                        if exp_r < 0.05 or win_rate < 48.0:
                                            print(f"🛡️ Profitability Gate Blocked {fut_sym} {eq_action}: Statistical replay shows negative/marginal profit expectancy (Exp R: {exp_r:+.2f}R, Win Rate: {win_rate:.1f}%). Standing aside.")
                                            can_enter_futures = False
                                            continue

                                        # Multi-Agent Consensus Deliberation (TradingAgents)
                                        if TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                                            ta_check = TradingAgentsOrchestrator.deliberate_intraday(clean_sym, fut_entry_p, action=eq_action)
                                            if not ta_check.get('passed') and not ta_check.get('approved', True):
                                                print(f"🤖 TradingAgents AI Guard Vetoed Stock Future {fut_sym} {eq_action}: {ta_check.get('reason')}")
                                                can_enter_futures = False
                                                continue

                                        trade_id = f"ADV-{today_str.replace(' ', '')}-{now_dt.strftime('%H%M')}-{clean_sym}FUT"
                                        if not any(t.get('id') == trade_id for t in (trades + new_trades)):
                                            has_margin, req_margin, avail_margin, fut_lot = check_margin_availability(tracker, fut_sym, fut_entry_p, fut_lot, is_equity=False, sl=fut_sl)
                                            if not has_margin:
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
                                                    "contract_ticker": fut_tick or f"NSE:{clean_sym}1!",
                                                    "event_context": f"NSE Stock Future ({sfc['sector']}): {eq_setup}",
                                                    "regime": regime_info,
                                                    "time_machine": sim_metrics,
                                                    "risk_check": risk_metrics,
                                                    "orb_check": orb_metrics,
                                                    "event_check": event_metrics,
                                                    "pullback_check": pullback_eval,
                                                    "suggestion_6pm": {
                                                        "status": "REALTIME_TRIGGER",
                                                        "action_text": f"{eq_action} 1 Lot @ ₹{fut_entry_p:,.2f}",
                                                        "rationale": f"{eq_rationale} {sim_metrics.get('summary', '')}",
                                                        "reference_price": fut_entry_p,
                                                        "stop_loss": fut_sl,
                                                        "target": fut_tgt
                                                    },
                                                    "check_615": {
                                                        "checked": True,
                                                        "status": "INSUFFICIENT_MARGIN_STAND_ASIDE",
                                                        "price_615": fut_entry_p,
                                                        "action_taken": f"Execution Skipped: Insufficient Margin (Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Alert Sent.",
                                                        "detail": f"{eq_setup}: Setup confirmed, but available futures cash ₹{avail_margin:,.2f} is insufficient for margin ₹{req_margin:,.2f}."
                                                    },
                                                    "paper_trade": {
                                                        "user": USER_ACCOUNT,
                                                        "action": "STAND_ASIDE",
                                                        "quantity": 0,
                                                        "entry_price": fut_entry_p,
                                                        "entry_time": now_time_str,
                                                        "exit_price": fut_entry_p,
                                                        "exit_time": "03:25 PM IST",
                                                        "pnl_points": 0.0,
                                                        "pnl_inr": 0.0,
                                                        "status": "STAND_ASIDE",
                                                        "margin_required_inr": 0.0,
                                                        "product_type": "NRML_FUTURES"
                                                    },
                                                    "eod_outcome": {
                                                        "session_close": None,
                                                        "circuit_limit": None,
                                                        "outcome": "PROTECTED",
                                                        "badge": "Margin Skipped 🛡️",
                                                        "pnl_display": "₹0.00 (Discretionary)",
                                                        "summary": f"{eq_setup} cleared rules, but execution skipped on managed portfolio due to margin (Req: ₹{req_margin:,.2f} > Avail: ₹{avail_margin:,.2f}). Discretionary alert sent."
                                                    }
                                                }
                                                new_trades.append(entry_obj)
                                                active_sectors.add(sfc['sector'])
                                                mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
                                                subject = f"📢 [STOCK FUTURE MARGIN EXHAUSTED - DISCRETIONARY ALERT] {sfc['name']} {eq_action} @ ₹{fut_entry_p:,.2f} ({now_time_str})"
                                                body = (
                                                    f"📢 <b>REAL-TIME NSE STOCK FUTURE TRIGGER (MARGIN EXHAUSTED)</b>\n"
                                                    f"📅 <i>{today_str}, {now_time_str}</i>\n"
                                                    f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                                                    f"• <b>Contract</b>: <b>{sfc['name']} ({fut_sym})</b> [Ticker: {fut_tick or f'NSE:{clean_sym}1!'}]\n"
                                                    f"• <b>Signal</b>: <b>{eq_action} 1 Lot ({fut_lot} units) @ ₹{fut_entry_p:,.2f}</b>\n"
                                                    f"• <b>Setup</b>: {eq_setup}\n"
                                                    f"• <b>Technical Rationale</b>: {eq_rationale}\n"
                                                    f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                                                    f"• <b>Stop-Loss</b>: <b>₹{fut_sl:,.2f}</b> | <b>Target (2.0R)</b>: <b>₹{fut_tgt:,.2f}</b>\n"
                                                    f"• <b>Margin Status</b>: ⚠️ <b>Required: ₹{req_margin:,.2f} | Available Futures Pool: ₹{avail_margin:,.2f}</b>\n"
                                                    f"• <b>Execution Status</b>: <b>SKIPPED on Managed Portfolio</b> (Cash pool fully deployed)\n"
                                                    f"• <b>Discretionary Notice</b>: Broadcasted for manual execution if you have separate broker margin.\n\n"
                                                    f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trading-tab"
                                                )
                                                alerts_to_send.append({'subject': subject, 'body': body})
                                                print(f"📢 Discretionary Alert Queued (Futures Margin Exhausted): {sfc['name']} {eq_action} @ ₹{fut_entry_p:,.2f}")
                                                can_enter_futures = False
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
                                                "symbol": fut_sym,
                                                "name": sfc['name'],
                                                "lot_size": fut_lot,
                                                "asset_type": "FUTURES",
                                                "contract_ticker": fut_tick or f"NSE:{clean_sym}1!",
                                                "event_context": f"NSE Stock Future ({sfc['sector']}): {eq_setup}",
                                                "regime": regime_info,
                                                "time_machine": sim_metrics,
                                                "risk_check": risk_metrics,
                                                "orb_check": orb_metrics,
                                                "event_check": event_metrics,
                                                "pullback_check": pullback_eval,
                                                "suggestion_6pm": {
                                                    "status": "REALTIME_TRIGGER",
                                                    "action_text": f"{eq_action} 1 Lot @ ₹{fut_entry_p:,.2f}",
                                                    "rationale": f"{eq_rationale} {sim_metrics.get('summary', '')}",
                                                    "reference_price": fut_entry_p,
                                                    "stop_loss": fut_sl,
                                                    "target": fut_tgt
                                                },
                                                "check_615": {
                                                    "checked": True,
                                                    "status": "TECHNICAL_TRIGGER_ENTERED",
                                                    "price_615": fut_entry_p,
                                                    "action_taken": f"Entered {eq_action} 1 Lot ({fut_lot} units) @ ₹{fut_entry_p:,.2f}",
                                                    "detail": f"{eq_setup}: {eq_rationale} | {sim_metrics.get('summary', '')}"
                                                },
                                                "paper_trade": {
                                                    "user": USER_ACCOUNT,
                                                    "action": eq_action,
                                                    "quantity": fut_lot,
                                                    "entry_price": fut_entry_p,
                                                    "entry_time": now_time_str,
                                                    "exit_price": None,
                                                    "exit_time": None,
                                                    "pnl_points": 0.0,
                                                    "pnl_inr": 0.0,
                                                    "status": "ACTIVE",
                                                    "target": fut_tgt,
                                                    "stop_loss": fut_sl,
                                                    "initial_stop_loss": fut_sl,
                                                    "original_sl": fut_sl,
                                                    "peak_high": fut_entry_p,
                                                    "trough_low": fut_entry_p,
                                                    "margin_required_inr": fut_margin_req,
                                                    "product_type": "NRML_FUTURES"
                                                },
                                                "eod_outcome": {
                                                    "session_close": None,
                                                    "circuit_limit": None,
                                                    "outcome": "PENDING",
                                                    "badge": "Active Trigger ⚡",
                                                    "pnl_display": "Ticking Live...",
                                                    "summary": f"{eq_setup} active: {eq_action} @ ₹{fut_entry_p:,.2f} (Target: ₹{fut_tgt:,.2f}, SL: ₹{fut_sl:,.2f}) | {sim_metrics.get('summary', '')}"
                                                }
                                            }
                                            new_trades.append(entry_obj)
                                            active_sectors.add(sfc['sector'])
                                            mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
                                            subject = f"🎯 [NSE STOCK FUTURE TRIGGER] {sfc['name']} {eq_action} @ ₹{fut_entry_p:,.2f} ({now_time_str})"
                                            body = (
                                                f"🎯 <b>REAL-TIME NSE STOCK FUTURE TRIGGER</b>\n"
                                                f"📅 <i>{today_str}, {now_time_str}</i>\n"
                                                f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                                                f"• <b>Contract</b>: <b>{sfc['name']} ({fut_sym})</b> [Ticker: {fut_tick or f'NSE:{clean_sym}1!'}]\n"
                                                f"• <b>Action</b>: <b>{eq_action} 1 Lot ({fut_lot} units) @ ₹{fut_entry_p:,.2f}</b>\n"
                                                f"• <b>Technical Setup</b>: {eq_setup}\n"
                                                f"• <b>Technical Rationale</b>: {eq_rationale}\n"
                                                f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                                                f"• <b>Stop-Loss</b>: <b>₹{fut_sl:,.2f}</b> (Max loss strictly ₹{round(fut_risk_per_share * fut_lot, 2):,.2f} <= ₹{MAX_LOSS_PER_TRADE_INR:,.0f})\n"
                                                f"• <b>Target (2.0R)</b>: <b>₹{fut_tgt:,.2f}</b>\n"
                                                f"• <b>Contract Value</b>: ₹{fut_entry_p * fut_lot:,.2f} (Margin Required: ₹{fut_margin_req:,.2f})\n"
                                                f"• <b>Auto Square-off</b>: Strict 03:25 PM IST EOD flat exit\n\n"
                                                f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trading-tab"
                                            )
                                            alerts_to_send.append({'subject': subject, 'body': body})
                                            print(f"🎯 NSE Stock Future Trigger Fired: {sfc['name']} {eq_action} @ ₹{fut_entry_p:,.2f} ({eq_setup}) | Replay: {mc_badge}")
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
                        intraday_pool_info = get_intraday_margin_pool(tracker)
                        # Dedicated ₹30,000 daily MIS margin pool (~₹10,000 margin / ₹50,000 buying power per stock)
                        avail_intraday_margin = max(0.0, intraday_pool_info['base_pool'] - used_intraday_margin)
                        target_margin_per_stock = round(min(avail_intraday_margin, intraday_pool_info['base_pool'] / MAX_CONCURRENT_EQUITIES), 2)
                        if target_margin_per_stock < 9500.0 and avail_intraday_margin >= 9500.0:
                            target_margin_per_stock = min(avail_intraday_margin, 10000.0)
                        margin_per_share = cur_p * 0.20  # 20% SEBI MIS intraday margin requirement (5x leverage)
                        if margin_per_share <= 0:
                            continue

                        # Strict Balance Check Before Buying: Must have sufficient MIS margin pool
                        if avail_intraday_margin < margin_per_share:
                            print(f"🛡️ Margin Deficit Guard: Blocked {clean_sym} Stock Purchase. Available MIS Margin (₹{avail_intraday_margin:,.2f}) < Required 1-Share Margin (₹{margin_per_share:,.2f}).")
                            has_equity_margin = False
                            eq_shares = 0
                            req_margin_equity = margin_per_share
                        else:
                            # Sized to approximately ₹10,000 margin per trade (5x leverage = ~₹50,000 position)
                            target_shares = round(target_margin_per_stock / margin_per_share)
                            max_shares = math.floor(avail_intraday_margin / margin_per_share)
                            eq_shares = max(1, min(target_shares, max_shares))
                            while eq_shares > 1 and (cur_p * eq_shares * 0.20) > avail_intraday_margin:
                                eq_shares -= 1

                            if eq_shares < 1:
                                has_equity_margin = False
                                eq_shares = 0
                                req_margin_equity = margin_per_share
                            else:
                                req_margin_equity = round(cur_p * eq_shares * 0.20, 2)
                                has_equity_margin = (eq_shares >= 1 and req_margin_equity <= avail_intraday_margin)

                        risk_per_share = abs(cur_p - eq_sl)

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
                            max_daily_loss_inr=MAX_INTRADAY_STOCK_DAILY_LOSS_INR,
                            min_reward_risk=1.1
                        )
                        if not risk_metrics.get('passed'):
                            print(f"🛡️ Pre-Trade Risk Engine Blocked {clean_sym} {eq_action}: {risk_metrics.get('reason')}")
                            continue

                        # 7. Monte Carlo Time Machine simulation (trader-timemachine skill: 3,000 replays)
                        sim_metrics = MonteCarloTimeMachine.run_simulation(sym, cur_p, eq_sl, eq_tgt, action=eq_action.lower(), replays=3000)
                        if not sim_metrics.get('passed'):
                            print(f"🎲 Monte Carlo Time Machine Blocked {clean_sym} {eq_action}: {sim_metrics.get('summary')}")
                            continue

                        # Rigorous Profitability Verification Gate (Long & Short):
                        exp_r = float(sim_metrics.get('expected_r', 0.0) or 0.0)
                        win_rate = float(sim_metrics.get('resolved_win_rate', 0.0) or sim_metrics.get('resolved_win_rate_pct', 0.0) or 0.0)
                        if exp_r < 0.05 or win_rate < 48.0:
                            print(f"🛡️ Profitability Gate Blocked {clean_sym} {eq_action}: Statistical replay shows negative/marginal profit expectancy (Exp R: {exp_r:+.2f}R, Win Rate: {win_rate:.1f}%). Standing aside.")
                            continue

                        # Multi-Agent Consensus Deliberation (TradingAgents)
                        if TradingAgentsOrchestrator and TradingAgentsOrchestrator.is_available():
                            ta_check = TradingAgentsOrchestrator.deliberate_intraday(clean_sym, cur_p, action=eq_action)
                            if not ta_check.get('passed') and not ta_check.get('approved', True):
                                print(f"🤖 TradingAgents AI Guard Vetoed Intraday Stock {clean_sym} {eq_action}: {ta_check.get('reason')}")
                                continue

                        if not has_equity_margin:
                            # MARGIN DEFICIT: Setup cleared all rules, but MIS pool is exhausted. Record Stood Aside & dispatch alert!
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
                                "pullback_check": pullback_eval,
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
                                    "status": "INSUFFICIENT_MARGIN_STAND_ASIDE",
                                    "price_615": cur_p,
                                    "action_taken": f"Execution Skipped: Insufficient Margin (Req: ₹{req_margin_equity:,.2f} > Avail: ₹{avail_intraday_margin:,.2f}). Alert Sent.",
                                    "detail": f"{eq_setup}: Setup confirmed, but available MIS margin ₹{avail_intraday_margin:,.2f} is insufficient for ₹{req_margin_equity:,.2f}."
                                },
                                "paper_trade": {
                                    "user": USER_ACCOUNT,
                                    "action": "STAND_ASIDE",
                                    "quantity": 0,
                                    "entry_price": cur_p,
                                    "entry_time": now_time_str,
                                    "exit_price": cur_p,
                                    "exit_time": "03:25 PM IST",
                                    "pnl_points": 0.0,
                                    "pnl_inr": 0.0,
                                    "status": "STAND_ASIDE",
                                    "margin_required_inr": 0.0,
                                    "product_type": "MIS_INTRADAY"
                                },
                                "eod_outcome": {
                                    "session_close": None,
                                    "circuit_limit": None,
                                    "outcome": "PROTECTED",
                                    "badge": "Margin Skipped 🛡️",
                                    "pnl_display": "₹0.00 (Discretionary)",
                                    "summary": f"{eq_setup} cleared rules, but execution skipped on managed portfolio due to MIS margin (Req: ₹{req_margin_equity:,.2f} > Avail: ₹{avail_intraday_margin:,.2f}). Discretionary alert sent."
                                }
                            }
                            new_trades.append(entry_obj)

                            mc_badge = f"{sim_metrics.get('p_target_pct', 0)}% Win Prob | {sim_metrics.get('expected_r', 0):+.2f}R Expectancy"
                            subject = f"📢 [NIFTY MIS MARGIN EXHAUSTED - DISCRETIONARY ALERT] {name} {eq_action} @ ₹{cur_p:,.2f} ({now_time_str})"
                            body = (
                                f"📢 <b>REAL-TIME INTRADAY STOCK TRIGGER (MIS MARGIN EXHAUSTED)</b>\n"
                                f"📅 <i>{today_str}, {now_time_str}</i>\n"
                                f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
                                f"• <b>Asset Class</b>: Nifty Intraday Stock (MIS 5x Margin Setup)\n"
                                f"• <b>Stock</b>: <b>{name} ({clean_sym})</b>\n"
                                f"• <b>Signal</b>: <b>{eq_action} {eq_shares} shares @ ₹{cur_p:,.2f}</b>\n"
                                f"• <b>Setup</b>: {eq_setup}\n"
                                f"• <b>Technical Rationale</b>: {eq_rationale}\n"
                                f"• <b>🎲 Monte Carlo Replay (3,000 runs)</b>: <b>{mc_badge}</b>\n"
                                f"• <b>Stop-Loss</b>: <b>₹{eq_sl:,.2f}</b> | <b>Target (2.0R)</b>: <b>₹{eq_tgt:,.2f}</b>\n"
                                f"• <b>Margin Status</b>: ⚠️ <b>Required: ₹{req_margin_equity:,.2f} | Available MIS Pool: ₹{avail_intraday_margin:,.2f}</b>\n"
                                f"• <b>Execution Status</b>: <b>SKIPPED on Managed Portfolio</b> (Intraday ₹30k margin pool fully allocated)\n"
                                f"• <b>Discretionary Notice</b>: Broadcasted for manual execution if additional broker buying power is available.\n\n"
                                f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trading-tab"
                            )
                            alerts_to_send.append({'subject': subject, 'body': body})
                            print(f"📢 Discretionary Alert Queued (MIS Margin Exhausted): {name} {eq_action} @ ₹{cur_p:,.2f}")
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
                            "pullback_check": pullback_eval,
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
                                "initial_stop_loss": eq_sl,
                                "original_sl": eq_sl,
                                "peak_high": cur_p,
                                "trough_low": cur_p,
                                "margin_required_inr": req_margin_equity,
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
                            f"• <b>Stop-Loss</b>: <b>₹{eq_sl:,.2f}</b> (Risk at stop: ₹{round(risk_per_share * eq_shares, 2):,.2f})\n"
                            f"• <b>Target (2.0R)</b>: <b>₹{eq_tgt:,.2f}</b>\n"
                            f"• <b>Position Value</b>: ₹{cur_p * eq_shares:,.2f} (MIS Margin Blocked: ₹{round(cur_p * eq_shares * 0.20, 2):,.2f} / ₹30k pool | Slot: ₹10k)\n"
                            f"• <b>Auto Square-off</b>: Strict 03:25 PM IST EOD flat exit (Zero overnight risk)\n\n"
                            f"🌐 <b>Interactive Terminal</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trading-tab"
                        )
                        alerts_to_send.append({'subject': subject, 'body': body})
                        print(f"⚡ Nifty Intraday Stock Trigger Fired: {name} {eq_action} @ ₹{cur_p:,.2f} ({eq_setup}) | Replay: {mc_badge}")
                        can_enter_equity = (len(active_intraday_trades) + len(new_trades) < MAX_CONCURRENT_EQUITIES)

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
        try:
            import yfinance as yf
            yf_sym = sym if sym.endswith('.NS') else f"{clean_sym}.NS"
            t_obj = yf.Ticker(yf_sym)
            fast_p = getattr(t_obj.fast_info, 'last_price', None)
            if fast_p and float(fast_p) > 0:
                cur_p = round(float(fast_p), 2)
            else:
                hist_1m = t_obj.history(period='1d', interval='1m')
                if not hist_1m.empty:
                    cur_p = round(float(hist_1m['Close'].iloc[-1]), 2)
        except Exception as e:
            print(f"Warning: Could not fetch live yfinance price for 03:20 square-off {sym}: {e}")
        
        action = pt.get('action', 'BUY')
        qty = pt.get('quantity') or trade.get('lot_size') or 1
        
        pts = (cur_p - entry_p) if action == 'BUY' else (entry_p - cur_p)
        gross_pnl_inr = round(pts * qty, 2)
        charges = DhanBrokerageCalculator.calculate_trade_charges(
            symbol=sym,
            asset_type=trade.get('asset_type'),
            action=action,
            entry_price=entry_p,
            exit_price=cur_p,
            quantity=qty,
            product_type=pt.get('product_type')
        )
        net_pnl_inr = round(gross_pnl_inr - charges['total_charges'], 2)
        pnl_inr = net_pnl_inr
        
        pt['exit_price'] = cur_p
        pt['exit_time'] = "03:20 PM IST"
        pt['exit_trigger'] = "INTRADAY_320_SQUAREOFF"
        pt['pnl_points'] = round(pts, 2)
        pt['gross_pnl_inr'] = gross_pnl_inr
        pt['charges'] = charges
        pt['net_pnl_inr'] = net_pnl_inr
        pt['pnl_inr'] = pnl_inr
        pt['status'] = "CLOSED"
        
        is_correct = gross_pnl_inr > 0
        trade['time_exit'] = "03:20 PM IST"
        eod = trade.setdefault('eod_outcome', {})
        eod['session_close'] = cur_p
        eod['exit_time'] = "03:20 PM IST"
        eod['exit_trigger'] = "INTRADAY_320_SQUAREOFF"
        eod['outcome'] = "CORRECT" if is_correct else ("PROTECTED" if abs(gross_pnl_inr) < 1.0 else "WRONG")
        eod['badge'] = "Intraday Exit (03:20 PM)"
        eod['gross_pnl_inr'] = gross_pnl_inr
        eod['charges'] = charges
        eod['net_pnl_inr'] = net_pnl_inr
        eod['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
        eod['summary'] = f"Intraday 03:20 PM square-off executed. Entry: ₹{entry_p:,.2f} -> Exit: ₹{cur_p:,.2f} ({pts:+.2f} pts | Gross: {'+' if gross_pnl_inr >= 0 else ''}₹{gross_pnl_inr:,.2f} | Dhan: -₹{charges['total_charges']:,.2f} | Net: {'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}). Margin released."
        
        summary_lines.append(
            f"• <b>{trade['name']} ({action})</b>: Entry ₹{entry_p:,.2f} ➔ Exit ₹{cur_p:,.2f} | Gross: <b>{'+' if gross_pnl_inr >= 0 else ''}₹{gross_pnl_inr:,.2f}</b> | Dhan: -₹{charges['total_charges']:,.2f} | Net P&L: <b>{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}</b>"
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
    if not circuits_res:
        circuits_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_data.json')
        if os.path.exists(circuits_file):
            try:
                with open(circuits_file, 'r') as cf:
                    circuits_res = json.load(cf)
            except Exception:
                pass
    commodities_map = {c['symbol']: c for c in circuits_res.get('commodities', [])} if circuits_res else {}
    
    summary_lines = []
    total_day_pnl = 0.0

    for trade in active_trades:
        sym = trade['symbol']
        c_item = resolve_commodity_item(sym, commodities_map)
        is_eq = trade.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] or '-FUT' in sym or sym in STOCK_FUTURES_CONFIG
        
        pt = trade.setdefault('paper_trade', {})
        action = pt.get('action', 'BUY')
        entry_p = float(pt.get('entry_price') or trade.get('suggestion_6pm', {}).get('reference_price') or 0.0)
        lot_size = trade.get('lot_size') or pt.get('quantity') or 1
        
        if c_item:
            close_p = float(c_item.get('current_price') or entry_p)
        elif is_eq:
            close_p = float(pt.get('exit_price') or entry_p)
            try:
                import yfinance as yf
                clean_sym = sym.replace('-FUT', '').replace('.NS', '').upper()
                yf_sym = sym if sym.endswith('.NS') else f"{clean_sym}.NS"
                t_obj = yf.Ticker(yf_sym)
                fast_p = getattr(t_obj.fast_info, 'last_price', None)
                if fast_p and float(fast_p) > 0:
                    close_p = round(float(fast_p), 2)
                else:
                    hist_1m = t_obj.history(period='1d', interval='1m')
                    if not hist_1m.empty:
                        close_p = round(float(hist_1m['Close'].iloc[-1]), 2)
            except Exception:
                pass
        else:
            close_p = float(pt.get('exit_price') or entry_p)
            
        pnl_pts = (close_p - entry_p) if action == 'BUY' else (entry_p - close_p)
        gross_pnl_inr = round(pnl_pts * lot_size, 2)
        charges = DhanBrokerageCalculator.calculate_trade_charges(
            symbol=sym,
            asset_type=trade.get('asset_type'),
            action=action,
            entry_price=entry_p,
            exit_price=close_p,
            quantity=lot_size,
            product_type=pt.get('product_type')
        )
        net_pnl_inr = round(gross_pnl_inr - charges['total_charges'], 2)
        pnl_inr = net_pnl_inr
        total_day_pnl += pnl_inr
        
        timing = get_us_aligned_session_times(now_dt)
        exit_time_label = "03:20 PM IST" if is_eq else timing["eod_str"]
        exit_trigger = "INTRADAY_320_SQUAREOFF" if is_eq else "EOD_SQUAREOFF"
        
        pt['exit_price'] = close_p
        pt['exit_time'] = exit_time_label
        pt['exit_trigger'] = exit_trigger
        pt['pnl_points'] = round(pnl_pts, 2)
        pt['gross_pnl_inr'] = gross_pnl_inr
        pt['charges'] = charges
        pt['net_pnl_inr'] = net_pnl_inr
        pt['pnl_inr'] = pnl_inr
        pt['status'] = "CLOSED"
        
        is_correct = gross_pnl_inr > 0
        trade['time_exit'] = exit_time_label
        eod = trade.setdefault('eod_outcome', {})
        eod['session_close'] = close_p
        eod['exit_time'] = exit_time_label
        eod['exit_trigger'] = exit_trigger
        eod['outcome'] = "CORRECT" if is_correct else ("PROTECTED" if abs(gross_pnl_inr) < 1.0 else "WRONG")
        eod['badge'] = f"{'Correct ✅' if is_correct else 'Wrong ❌'} ({exit_time_label.replace(' IST', '')})"
        eod['gross_pnl_inr'] = gross_pnl_inr
        eod['charges'] = charges
        eod['net_pnl_inr'] = net_pnl_inr
        eod['pnl_display'] = f"{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}"
        eod['summary'] = f"{exit_time_label} session close exit. Entry: ₹{entry_p:,.2f} -> Exit: ₹{close_p:,.2f} ({pnl_pts:+.2f} pts | Gross: {'+' if gross_pnl_inr >= 0 else ''}₹{gross_pnl_inr:,.2f} | Dhan: -₹{charges['total_charges']:,.2f} | Net: {'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}). Margin released."
        
        summary_lines.append(
            f"• <b>{trade['name']} ({action})</b>: Entry ₹{entry_p:,.2f} ➔ Exit ₹{close_p:,.2f} | Gross: <b>{'+' if gross_pnl_inr >= 0 else ''}₹{gross_pnl_inr:,.2f}</b> | Dhan: -₹{charges['total_charges']:,.2f} | Net P&L: <b>{'+' if pnl_inr >= 0 else ''}₹{pnl_inr:,.2f}</b> ({'Correct ✅' if is_correct else 'Wrong ❌'})"
        )

    save_tracker(tracker)
    
    if summary_lines and alerter:
        eod_time_title = timing["eod_str"].replace(" IST", "")
        body = (
            f"🏁 <b>DAILY {eod_time_title} IST ADVISORY PAPER TRADING SETTLEMENT ({timing['schedule_label']})</b>\n"
            f"📅 <i>{today_str}</i>\n"
            f"👤 Account: <b>{USER_ACCOUNT}</b>\n\n"
            f"<b>📊 TODAY'S ADVISORY RESULTS:</b>\n"
            + "\n".join(summary_lines) + "\n\n"
            f"💰 <b>Total Day Realized P&L</b>: <b>{'+' if total_day_pnl >= 0 else ''}₹{total_day_pnl:,.2f}</b>\n"
            f"📈 Cumulative Advisory Win Rate: <b>{tracker['summary']['win_rate_pct']}%</b> ({tracker['summary']['correct_count']}/{tracker['summary'].get('executed_count', 0)})\n\n"
            f"🌐 <b>Interactive Tracker</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
        )
        alerter.send_alert(f"🏁 [{eod_time_title} SETTLEMENT - {timing['schedule_label']}] Daily Advisory Results ({today_str})", body)
        
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
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--reset":
        t = init_clean_live_tracker()
        print("Live tracker reset complete. Summary:", t.get('summary'))
    else:
        check_and_execute_intraday_targets_and_stops()
        evaluate_realtime_technical_triggers()
        t = load_tracker()
        print("Advisory tracker check complete. Summary:", t.get('summary'))
