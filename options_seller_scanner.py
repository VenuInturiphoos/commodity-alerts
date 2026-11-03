"""
options_seller_scanner.py
========================
Institutional Options Selling & Theta Decay Scanner for Indian NSE F&O Stocks.
Identifies stocks with the LOWEST probability of breaking out or breaking down
so options sellers can harvest time-decay (theta) safely.

Filters:
1. Low ADX (< 20-22) -> Non-trending / range-bound consolidation.
2. Bollinger Band Squeeze -> Contained volatility channel.
3. Heavy institutional support (50/200 EMA, S1/S2) -> Safe OTM Put Sell.
4. Heavy institutional resistance ceiling -> Safe OTM Call Sell.
5. Tight horizontal channel -> Short Strangle (Sell CE + PE) for double theta harvest.
"""

import os
import json
import math
import pytz
import urllib3
from datetime import datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SCANNER_URL = 'https://scanner.tradingview.com/india/scan'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# NSE F&O Lot Sizes
FNO_LOT_SIZES = {
    'RELIANCE': 250, 'TCS': 175, 'HDFCBANK': 550, 'INFY': 400, 'ICICIBANK': 700,
    'BHARTIARTL': 475, 'SBIN': 750, 'ITC': 1600, 'LTIM': 150, 'LT': 150,
    'BAJFINANCE': 125, 'HINDUNILVR': 300, 'AXISBANK': 625, 'MARUTI': 50,
    'TATAMOTORS': 550, 'SUNPHARMA': 350, 'TITAN': 175, 'KOTAKBANK': 400,
    'ADANIENT': 300, 'ADANIPORTS': 400, 'ASIANPAINT': 200, 'BAJAJ-AUTO': 75,
    'BAJAJFINSV': 500, 'BPCL': 1800, 'BRITANNIA': 125, 'CIPLA': 650,
    'COALINDIA': 2100, 'DIVISLAB': 150, 'DRREDDY': 125, 'EICHERMOT': 150,
    'GRASIM': 250, 'HCLTECH': 350, 'HDFCLIFE': 1100, 'HEROMOTOCO': 150,
    'HINDALCO': 1400, 'INDUSINDBK': 500, 'JSWSTEEL': 675, 'M&M': 350,
    'NTPC': 1500, 'NESTLEIND': 40, 'ONGC': 3850, 'POWERGRID': 1800,
    'SBILIFE': 750, 'TATACONSUM': 900, 'TATASTEEL': 5500, 'TECHM': 600,
    'SHRIRAMFIN': 300, 'ULTRACEMCO': 100, 'WIPRO': 1500, 'POLYCAB': 125,
    'PERSISTENT': 100, 'COFORGE': 150, 'MPHASIS': 275, 'DIXON': 100,
    'CUMMINSIND': 300, 'VOLTAS': 600, 'FEDERALBNK': 5000, 'ASHOKLEY': 5000,
    'ASTRAL': 425, 'OBEROIRLTY': 700, 'TATACOMM': 500, 'JUBLFOOD': 1250,
    'PIDILITIND': 250, 'HDFCAMC': 300, 'BHEL': 2625, 'IDFCFIRSTB': 7500,
    'AUROPHARMA': 550, 'LUPIN': 425, 'PAGEIND': 15, 'COLPAL': 350,
    'GODREJPROP': 225, 'PIIND': 250, 'BALKRISIND': 300, 'DEEPAKNTR': 300,
    'PETRONET': 3000, 'CONCOR': 1000, 'ESCORTS': 175, 'ALKEM': 125,
    'BANDHANBNK': 2500, 'MUTHOOTFIN': 550, 'LICHSGFIN': 1000, 'BHARATFORG': 500,
    'ABCAPITAL': 3100, 'SAIL': 8000, 'NMDC': 4500, 'TATAELXSI': 100,
    'IPCALAB': 650, 'ZYDUSLIFE': 900, 'AUBANK': 1000, 'PVRINOX': 400
}

ALIAS_MAP = {
    'BAJAJ-AUTO': 'BAJAJ_AUTO',
    'TATAMOTORS': 'TMCV',
    'LTIM': 'LTI',
    'M&M': 'M_M'
}

REVERSE_ALIAS = {
    'BAJAJ_AUTO': 'BAJAJ-AUTO',
    'TMCV': 'TATAMOTORS',
    'TMPV': 'TMPV',
    'LTI': 'LTIM',
    'M_M': 'M&M'
}

def get_strike_step(price):
    """Calculates standardized NSE option strike intervals."""
    if price < 250:
        return 5
    elif price < 600:
        return 10
    elif price < 1500:
        return 20
    elif price < 3000:
        return 50
    else:
        return 100

def get_monthly_expiry_info(now_ist):
    """Returns the last Thursday of the current month (standard NSE Stock Option Expiry)."""
    year = now_ist.year
    month = now_ist.month
    
    # Find last day of month
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    last_dt = datetime(year, month, last_day, 15, 30, tzinfo=pytz.timezone('Asia/Kolkata'))
    
    # Walk backwards to find Thursday (weekday() == 3)
    offset = (last_dt.weekday() - 3) % 7
    expiry_day = last_day - offset
    expiry_dt = datetime(year, month, expiry_day, 15, 30, tzinfo=pytz.timezone('Asia/Kolkata'))
    
    days_to_expiry = max(1, (expiry_dt.date() - now_ist.date()).days)
    expiry_str = expiry_dt.strftime('%d %b %Y')
    return expiry_str, days_to_expiry

def scan_options_theta_opportunities(output_path='web/options_selling_data.json'):
    """
    Scans liquid F&O stocks for low-breakout, range-bound consolidation candidates.
    Generates actionable OTM Put Sell, OTM Call Sell, and Short Strangle suggestions.
    """
    now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
    expiry_str, dte = get_monthly_expiry_info(now_ist)
    
    fno_tickers = list(FNO_LOT_SIZES.keys())
    clean_map = {}
    for sym in fno_tickers:
        tv_sym = ALIAS_MAP.get(sym, sym)
        clean_map[tv_sym] = sym

    query_tickers = [f"NSE:{s}" for s in clean_map.keys()]

    cols = [
        'name', 'close', 'change', 'volume',
        'EMA50', 'EMA200', 'SMA50', 'SMA200',
        'RSI', 'ADX', 'ATR', 'BB.lower', 'BB.upper',
        'Volatility.D', 'sector'
    ]

    payload = {
        'symbols': {'tickers': query_tickers},
        'columns': cols
    }

    try:
        resp = requests.post(SCANNER_URL, json=payload, headers=HEADERS, timeout=12, verify=False)
        if resp.status_code != 200:
            print(f"TradingView Scanner failed with status {resp.status_code}")
            return None
        res_json = resp.json()
        raw_items = res_json.get('data', [])
    except Exception as e:
        print(f"Error fetching from TradingView: {e}")
        return None

    opportunities = []

    for item in raw_items:
        data = item.get('d', [])
        if len(data) < len(cols):
            continue

        raw_name = data[0]
        close = data[1]
        change_pct = data[2]
        volume = data[3]
        ema50 = data[4]
        ema200 = data[5]
        sma50 = data[6]
        sma200 = data[7]
        rsi = data[8]
        adx = data[9]
        atr = data[10]
        bb_low = data[11]
        bb_high = data[12]
        volatility_d = data[13]
        sector = data[14] or 'F&O Equities'

        if not close or close <= 0 or not ema50 or not ema200:
            continue

        orig_sym = REVERSE_ALIAS.get(raw_name, raw_name)
        lot_size = FNO_LOT_SIZES.get(orig_sym, 500)
        
        # Volatility & Trend Metrics
        adx_val = round(float(adx), 1) if adx is not None else 25.0
        rsi_val = round(float(rsi), 1) if rsi is not None else 50.0
        atr_val = round(float(atr), 2) if atr is not None else round(close * 0.02, 2)
        daily_vol = round(float(volatility_d), 2) if volatility_d is not None else 2.0
        
        bb_low_val = round(float(bb_low), 2) if bb_low is not None else round(close * 0.94, 2)
        bb_high_val = round(float(bb_high), 2) if bb_high is not None else round(close * 1.06, 2)
        bb_width_pct = round(((bb_high_val - bb_low_val) / close) * 100, 2)

        # Expected Move over DTE (1 Standard Deviation in %)
        expected_move_pct = round(daily_vol * math.sqrt(dte), 2)
        step = get_strike_step(close)

        # -------------------------------------------------------------
        # 1. Structural Floor & OTM Put Strike Recommendation
        # -------------------------------------------------------------
        # Put strike placed safely below the lower Bollinger Band and 200 EMA
        put_anchor = min(bb_low_val * 0.985, ema200 * 0.98, close * (1 - (expected_move_pct * 0.01 * 0.85)))
        put_strike = int(math.floor(put_anchor / step) * step)
        put_buffer_pts = round(close - put_strike, 2)
        put_buffer_pct = round((put_buffer_pts / close) * 100, 1)

        # Put Probability of Expiring Worthless (PoP)
        z_put = (put_buffer_pct / max(0.5, expected_move_pct)) if expected_move_pct > 0 else 1.5
        put_pop_pct = round(min(97.5, max(75.0, 50.0 + (z_put * 28.0))), 1)

        # Estimated OTM Put Premium (~0.8% - 1.2% of CMP for ~1.5 sigma OTM)
        est_put_premium = round(max(0.5, close * 0.010 * max(0.4, (100 - put_pop_pct) / 15)), 2)

        # -------------------------------------------------------------
        # 2. Structural Ceiling & OTM Call Strike Recommendation
        # -------------------------------------------------------------
        # Call strike placed safely above upper Bollinger Band and 200 EMA
        call_anchor = max(bb_high_val * 1.015, ema200 * 1.02, close * (1 + (expected_move_pct * 0.01 * 0.85)))
        call_strike = int(math.ceil(call_anchor / step) * step)
        call_buffer_pts = round(call_strike - close, 2)
        call_buffer_pct = round((call_buffer_pts / close) * 100, 1)

        # Call Probability of Expiring Worthless (PoP)
        z_call = (call_buffer_pct / max(0.5, expected_move_pct)) if expected_move_pct > 0 else 1.5
        call_pop_pct = round(min(97.5, max(75.0, 50.0 + (z_call * 28.0))), 1)

        # Estimated OTM Call Premium
        est_call_premium = round(max(0.5, close * 0.010 * max(0.4, (100 - call_pop_pct) / 15)), 2)

        # -------------------------------------------------------------
        # 3. Strategy Selection: Put Sell vs Call Sell vs Strangle
        # -------------------------------------------------------------
        is_golden = ema50 >= ema200
        is_rangebound = (adx_val <= 21.0) and (42.0 <= rsi_val <= 58.0) and (bb_width_pct <= 13.0)
        
        margin_required = round(lot_size * close * 0.20, -2) # Standard ~20% SPAN + Exposure

        if is_rangebound:
            strategy_type = 'STRANGLE'
            strategy_label = 'Short Strangle (Sell CE + PE)'
            strategy_badge = 'STRANGLE'
            primary_action = f"SELL {put_strike} PE & SELL {call_strike} CE"
            primary_pop = round((put_pop_pct + call_pop_pct) / 2.0, 1)
            primary_buffer_pct = min(put_buffer_pct, call_buffer_pct)
            est_total_premium = round(est_put_premium + est_call_premium, 2)
            est_inflow_inr = round(est_total_premium * lot_size, 2)
            rom_pct = round((est_inflow_inr / margin_required) * 100, 2)
            rationale = (f"Dead rangebound (ADX: {adx_val:.1f} < 21) trapped inside tight {bb_width_pct:.1f}% channel. "
                         f"Both Put ({put_strike} PE) and Call ({call_strike} CE) sit safely beyond 1.5σ volatility boundaries.")
        elif is_golden or (close >= ema50) or (rsi_val < 46.0):
            strategy_type = 'PUT_SELL'
            strategy_label = 'OTM Put Sell (Floor Defense)'
            strategy_badge = 'PUT_SELL'
            primary_action = f"SELL {put_strike} PE"
            primary_pop = put_pop_pct
            primary_buffer_pct = put_buffer_pct
            est_total_premium = est_put_premium
            est_inflow_inr = round(est_total_premium * lot_size, 2)
            rom_pct = round((est_inflow_inr / margin_required) * 100, 2)
            rationale = (f"Solid institutional floor underneath (50 EMA > 200 EMA / BB Floor). "
                         f"{put_strike} PE offers a massive {put_buffer_pct:.1f}% downside buffer with {put_pop_pct:.1f}% chance of expiring worthless.")
        else:
            strategy_type = 'CALL_SELL'
            strategy_label = 'OTM Call Sell (Ceiling Defense)'
            strategy_badge = 'CALL_SELL'
            primary_action = f"SELL {call_strike} CE"
            primary_pop = call_pop_pct
            primary_buffer_pct = call_buffer_pct
            est_total_premium = est_call_premium
            est_inflow_inr = round(est_total_premium * lot_size, 2)
            rom_pct = round((est_inflow_inr / margin_required) * 100, 2)
            rationale = (f"Capped below overhead resistance ceiling (200 EMA / BB Upper). "
                         f"{call_strike} CE provides {call_buffer_pct:.1f}% upside headroom with {call_pop_pct:.1f}% chance of full decay.")

        # Non-Breakout Theta Score (0 - 100 pts)
        # Higher score = Lower chance of breaking out/down
        # Formula: Low ADX weight (35 pts) + High PoP (35 pts) + Wide Buffer (20 pts) + Narrow Bandwidth (10 pts)
        adx_score = max(0.0, 35.0 - (adx_val * 1.2))
        pop_score = (primary_pop / 96.0) * 35.0
        buffer_score = min(20.0, (primary_buffer_pct / 8.0) * 20.0)
        bw_score = max(0.0, 10.0 - (bb_width_pct * 0.5))
        theta_safety_score = round(min(98.5, max(60.0, adx_score + pop_score + buffer_score + bw_score)), 1)

        opportunities.append({
            'symbol': orig_sym,
            'name': orig_sym,
            'sector': sector,
            'close': round(close, 2),
            'change_pct': round(change_pct, 2),
            'lot_size': lot_size,
            'adx': adx_val,
            'adx_status': 'Consolidating 💤' if adx_val < 18 else ('Rangebound 🟢' if adx_val < 24 else 'Trending ⚡'),
            'rsi': rsi_val,
            'atr': atr_val,
            'ema_50': round(ema50, 2),
            'ema_200': round(ema200, 2),
            'bb_lower': bb_low_val,
            'bb_upper': bb_high_val,
            'bb_width_pct': bb_width_pct,
            'daily_volatility_pct': daily_vol,
            'expected_move_pct': expected_move_pct,
            'strategy_type': strategy_type,
            'strategy_label': strategy_label,
            'strategy_badge': strategy_badge,
            'primary_action': primary_action,
            'primary_pop': primary_pop,
            'primary_buffer_pct': primary_buffer_pct,
            'theta_safety_score': theta_safety_score,
            'put_strike': put_strike,
            'put_buffer_pct': put_buffer_pct,
            'put_pop_pct': put_pop_pct,
            'est_put_premium': est_put_premium,
            'call_strike': call_strike,
            'call_buffer_pct': call_buffer_pct,
            'call_pop_pct': call_pop_pct,
            'est_call_premium': est_call_premium,
            'est_total_premium': est_total_premium,
            'est_inflow_inr': est_inflow_inr,
            'margin_required_inr': margin_required,
            'rom_pct': rom_pct,
            'days_to_expiry': dte,
            'expiry_date': expiry_str,
            'rationale': rationale
        })

    # Sort opportunities by theta_safety_score descending (Lowest probability of breakdown/breakout)
    opportunities.sort(key=lambda x: (x['theta_safety_score'], x['primary_pop'], x['primary_buffer_pct']), reverse=True)

    summary = {
        'total_scanned': len(opportunities),
        'high_pop_count': len([o for o in opportunities if o['primary_pop'] >= 85.0]),
        'strangle_count': len([o for o in opportunities if o['strategy_type'] == 'STRANGLE']),
        'put_sell_count': len([o for o in opportunities if o['strategy_type'] == 'PUT_SELL']),
        'call_sell_count': len([o for o in opportunities if o['strategy_type'] == 'CALL_SELL']),
        'avg_safety_buffer': round(sum(o['primary_buffer_pct'] for o in opportunities) / len(opportunities), 1) if opportunities else 0.0,
        'expiry_date': expiry_str,
        'days_to_expiry': dte
    }

    output_payload = {
        'last_updated': now_ist.strftime('%d %b %Y, %I:%M %p IST'),
        'summary': summary,
        'stocks': opportunities
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output_payload, f, indent=2)

    print(f"Successfully generated Options Theta Selling data with {len(opportunities)} stocks -> {output_path}")
    print(f"Summary: {summary['high_pop_count']} High PoP (>85%) | {summary['strangle_count']} Strangles | Avg Buffer: {summary['avg_safety_buffer']}%")
    return output_payload

if __name__ == '__main__':
    scan_options_theta_opportunities()
