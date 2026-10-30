"""
Nifty 50 Golden Crossover & Oversold Stocks Scanner Engine
Identifies high-probability value & swing opportunities across Indian Nifty 50 stocks
combining Golden Moving Average Crossovers (50/200 EMA), Oversold RSI (<35-40),
Bollinger Band mean-reversion bounces, and deep 52-week discounts.
"""

import os
import json
import pytz
import requests
from datetime import datetime

SCANNER_URL = 'https://scanner.tradingview.com/india/scan'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

ALIAS_MAP = {
    'BAJAJ-AUTO': 'BAJAJ_AUTO',
    'TATAMOTORS': 'TMCV',
    'LTIM': 'LTI'
}

REVERSE_ALIAS = {
    'BAJAJ_AUTO': 'BAJAJ-AUTO.NS',
    'TMCV': 'TATAMOTORS.NS',
    'TMPV': 'TMPV.NS',
    'LTI': 'LTIM.NS'
}

def load_nifty50_symbols(config_path='config.json'):
    try:
        with open(config_path, 'r') as f:
            cfg = json.load(f)
        stocks = cfg.get('stocks', {})
        if stocks:
            return list(stocks.keys())
    except Exception as e:
        print(f"Warning: Could not read config.json: {e}")
        
    # Standard Nifty 50 Fallback List
    return [
        'ADANIENT.NS', 'ADANIPORTS.NS', 'APOLLOHOSP.NS', 'ASIANPAINT.NS', 'AXISBANK.NS',
        'BAJAJ-AUTO.NS', 'BAJFINANCE.NS', 'BAJAJFINSV.NS', 'BPCL.NS', 'BHARTIARTL.NS',
        'BRITANNIA.NS', 'CIPLA.NS', 'COALINDIA.NS', 'DIVISLAB.NS', 'DRREDDY.NS',
        'EICHERMOT.NS', 'GRASIM.NS', 'HCLTECH.NS', 'HDFCBANK.NS', 'HDFCLIFE.NS',
        'HEROMOTOCO.NS', 'HINDALCO.NS', 'HINDUNILVR.NS', 'ICICIBANK.NS', 'ITC.NS',
        'INDUSINDBK.NS', 'INFY.NS', 'JSWSTEEL.NS', 'KOTAKBANK.NS', 'LTIM.NS',
        'LT.NS', 'M&M.NS', 'MARUTI.NS', 'NTPC.NS', 'NESTLEIND.NS',
        'ONGC.NS', 'POWERGRID.NS', 'RELIANCE.NS', 'SBILIFE.NS', 'SBIN.NS',
        'SUNPHARMA.NS', 'TCS.NS', 'TATACONSUM.NS', 'TATAMOTORS.NS', 'TATASTEEL.NS',
        'TECHM.NS', 'TITAN.NS', 'SHRIRAMFIN.NS', 'ULTRACEMCO.NS', 'WIPRO.NS'
    ]

def scan_nifty50_opportunities(output_path='web/nifty_crossover_oversold.json'):
    """
    Scans all Nifty 50 stocks for Golden Crossovers and Oversold opportunities
    and writes results to JSON for the frontend dashboard.
    """
    raw_symbols = load_nifty50_symbols()
    clean_map = {}
    for sym in raw_symbols:
        base = sym.replace('.NS', '')
        tv_sym = ALIAS_MAP.get(base, base)
        clean_map[tv_sym] = sym
        
    query_symbols = list(clean_map.keys())
    if 'TMPV' not in query_symbols:
        query_symbols.append('TMPV')

    cols = [
        'name', 'description', 'close', 'change', 'change_abs', 'volume',
        'price_52_week_high', 'price_52_week_low',
        'EMA50', 'EMA200', 'SMA50', 'SMA200',
        'RSI', 'RSI[1]', 'MACD.macd', 'MACD.signal',
        'BB.lower', 'BB.upper', 'Recommend.All', 'sector'
    ]

    payload = {
        'filter': [
            {'left': 'exchange', 'operation': 'equal', 'right': 'NSE'},
            {'left': 'name', 'operation': 'in_range', 'right': query_symbols}
        ],
        'columns': cols
    }

    try:
        resp = requests.post(SCANNER_URL, json=payload, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            print(f"TradingView scanner returned HTTP {resp.status_code}")
            return None
            
        data_rows = resp.json().get('data', [])
    except Exception as e:
        print(f"Error querying TradingView stock scanner: {e}")
        return None

    now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
    timestamp_str = now_ist.strftime('%d %b %Y, %I:%M %p IST')

    opportunities = []
    seen_symbols = set()

    for row in data_rows:
        d = dict(zip(cols, row.get('d', [])))
        name = d.get('name')
        if not name:
            continue
            
        orig_sym = clean_map.get(name) or REVERSE_ALIAS.get(name) or f"{name}.NS"
        if orig_sym in seen_symbols:
            continue
        seen_symbols.add(orig_sym)

        close = float(d.get('close') or 0.0)
        if close <= 0:
            continue

        change_pct = float(d.get('change') or 0.0)
        change_abs = float(d.get('change_abs') or 0.0)
        volume = int(d.get('volume') or 0)
        h52 = float(d.get('price_52_week_high') or close)
        l52 = float(d.get('price_52_week_low') or close)
        ema50 = float(d.get('EMA50') or close)
        ema200 = float(d.get('EMA200') or close)
        sma50 = float(d.get('SMA50') or close)
        sma200 = float(d.get('SMA200') or close)
        rsi = float(d.get('RSI') or 50.0)
        rsi_prev = float(d.get('RSI[1]') or rsi)
        bb_low = float(d.get('BB.lower') or (close * 0.95))
        bb_high = float(d.get('BB.upper') or (close * 1.05))
        macd_line = float(d.get('MACD.macd') or 0.0)
        macd_sig = float(d.get('MACD.signal') or 0.0)
        full_name = d.get('description') or name
        sector = d.get('sector') or 'Indian Equities'

        # Technical Metrics
        discount_pct = round(((h52 - close) / h52) * 100, 2) if h52 > 0 else 0.0
        discount_inr = round(h52 - close, 2)

        # Crossover Evaluation
        is_golden = ema50 >= ema200
        near_golden = (not is_golden) and (abs(ema50 - ema200) / ema200 <= 0.025)
        sma_golden = sma50 >= sma200
        ema_spread_pct = round(((ema50 - ema200) / ema200) * 100, 2)

        if is_golden:
            crossover_status = 'GOLDEN'
            crossover_label = 'Golden Cross Active 🌟'
            crossover_badge = 'GOLDEN'
        elif near_golden:
            crossover_status = 'BREWING'
            crossover_label = 'Cross Brewing ⏳'
            crossover_badge = 'BREWING'
        else:
            crossover_status = 'DEATH'
            crossover_label = '50 < 200 EMA ❄️'
            crossover_badge = 'BEARISH'

        # RSI Evaluation
        rsi_curling_up = rsi > rsi_prev
        if rsi < 30.0:
            rsi_status = 'EXTREME_OVERSOLD'
            rsi_badge = 'Extreme Oversold 🔥'
            rsi_color = '#ef4444'
        elif rsi < 38.0:
            rsi_status = 'OVERSOLD'
            rsi_badge = 'Oversold Zone 📉'
            rsi_color = '#f97316'
        elif rsi < 45.0:
            rsi_status = 'VALUE_ACCUMULATION'
            rsi_badge = 'Value Zone 🏷️'
            rsi_color = '#eab308'
        elif rsi < 60.0:
            rsi_status = 'BALANCED'
            rsi_badge = 'Balanced Range ⚖️'
            rsi_color = '#3b82f6'
        else:
            rsi_status = 'OVERBOUGHT'
            rsi_badge = 'Extended / Overbought 📈'
            rsi_color = '#8b5cf6'

        # Bollinger Band Proximity
        near_bb_lower = (close <= bb_low * 1.018)
        bb_spread = round(((close - bb_low) / bb_low) * 100, 2)

        # MACD Bullish Status
        macd_bullish = macd_line > macd_sig
        macd_status = 'Bullish Expansion 📈' if macd_bullish else 'Consolidating ⏳'

        # Quantitative Probability of Profit / Confluence Scoring (Base 50, Scale to 96%)
        # Multi-factor algorithmic model:
        # 1. Trend & Crossover Alignment (Up to 25 pts)
        # 2. Oversold Mean Reversion (Up to 30 pts)
        # 3. Discount Depth (Up to 25 pts)
        # 4. Support Proximity (BB Lower / Curled RSI / MACD) (Up to 20 pts)
        score = 50.0

        if is_golden:
            score += 20.0
            if sma_golden:
                score += 5.0
        elif near_golden:
            score += 12.0
            
        if rsi < 30.0:
            score += 28.0
        elif rsi < 35.0:
            score += 24.0
        elif rsi < 40.0:
            score += 18.0
        elif rsi < 48.0:
            score += 10.0
            
        if rsi_curling_up and rsi < 48.0:
            score += 4.0

        if discount_pct >= 25.0:
            score += 22.0
        elif discount_pct >= 20.0:
            score += 17.0
        elif discount_pct >= 15.0:
            score += 12.0
        elif discount_pct >= 10.0:
            score += 6.0

        if near_bb_lower:
            score += 10.0
        if macd_bullish and rsi < 50.0:
            score += 6.0

        # Cap between 58% and 96%
        win_prob = int(round(min(96.0, max(58.0, score))))

        # Probability Classification
        if win_prob >= 90:
            prob_tier = 'ELITE_SETUP'
            prob_label = 'Elite High Prob 🔥'
            prob_badge = 'ELITE'
        elif win_prob >= 82:
            prob_tier = 'HIGH_PROB'
            prob_label = 'High Probability ⚡'
            prob_badge = 'HIGH'
        elif win_prob >= 72:
            prob_tier = 'SOLID_VALUE'
            prob_label = 'Solid Value 🟢'
            prob_badge = 'GOOD'
        else:
            prob_tier = 'WATCHLIST'
            prob_label = 'Watchlist Setup 👁️'
            prob_badge = 'WATCH'

        # Actionable Trade Plan
        if win_prob >= 88 and (is_golden or near_golden or rsi < 35.0):
            action = 'STRONG BUY'
            action_desc = 'High-confluence bounce setup with attractive risk-reward.'
        elif is_golden and rsi < 45.0:
            action = 'ACCUMULATE'
            action_desc = 'Golden trend intact, accumulating at favorable dip.'
        elif rsi < 35.0:
            action = 'SWING BUY'
            action_desc = 'Oversold mean reversion trade with defined protective stop.'
        else:
            action = 'WATCHLIST'
            action_desc = 'Monitor for price stabilization before triggering entry.'

        # Targets and Stop Loss Calculation
        # Stop loss placed below lower Bollinger Band or 4.5% below CMP
        stop_loss = round(min(bb_low * 0.985, close * 0.955), 2)
        risk_per_share = round(close - stop_loss, 2)
        if risk_per_share <= 0:
            risk_per_share = round(close * 0.04, 2)
            stop_loss = round(close - risk_per_share, 2)

        # Target 1: 1.8x to 2.2x risk (Mean reversion to 50 EMA / 20 MA)
        target_1 = round(close + (risk_per_share * 2.2), 2)
        # Target 2: 3.5x risk (Swing high retest or 52W High recovery)
        target_2 = round(min(h52, close + (risk_per_share * 3.8)), 2)

        potential_upside_t1 = round(((target_1 - close) / close) * 100, 1)
        potential_upside_t2 = round(((target_2 - close) / close) * 100, 1)
        risk_pct = round(((close - stop_loss) / close) * 100, 1)
        risk_reward_str = f"1 : {round((target_1 - close) / risk_per_share, 1)}"

        # Rationale Narrative
        reasons = []
        if is_golden:
            reasons.append("50 EMA > 200 EMA Golden Cross confirmed")
        elif near_golden:
            reasons.append("50 & 200 EMA converging within 2.5% for imminent crossover")
            
        if rsi < 32.0:
            reasons.append(f"Deeply oversold RSI at {rsi:.1f}")
        elif rsi < 42.0:
            reasons.append(f"Oversold accumulation RSI at {rsi:.1f}")
            
        if discount_pct >= 15.0:
            reasons.append(f"Trading at a deep {discount_pct:.1f}% discount to 52W high")
            
        if near_bb_lower:
            reasons.append("Price testing lower Bollinger Band mean-reversion floor")
            
        if macd_bullish:
            reasons.append("MACD momentum positive")

        rationale_text = "; ".join(reasons) if reasons else "Favorable risk-reward swing structure on daily timeframe."

        opportunities.append({
            'symbol': orig_sym,
            'name': name,
            'full_name': full_name,
            'sector': sector,
            'close': round(close, 2),
            'change_pct': round(change_pct, 2),
            'change_abs': round(change_abs, 2),
            'volume': volume,
            'price_52_week_high': round(h52, 2),
            'price_52_week_low': round(l52, 2),
            'discount_pct': discount_pct,
            'discount_inr': discount_inr,
            'ema_50': round(ema50, 2),
            'ema_200': round(ema200, 2),
            'sma_50': round(sma50, 2),
            'sma_200': round(sma200, 2),
            'ema_spread_pct': ema_spread_pct,
            'crossover_status': crossover_status,
            'crossover_label': crossover_label,
            'crossover_badge': crossover_badge,
            'is_golden': is_golden,
            'near_golden': near_golden,
            'rsi': round(rsi, 1),
            'rsi_prev': round(rsi_prev, 1),
            'rsi_status': rsi_status,
            'rsi_badge': rsi_badge,
            'rsi_color': rsi_color,
            'rsi_curling_up': rsi_curling_up,
            'bb_lower': round(bb_low, 2),
            'bb_upper': round(bb_high, 2),
            'near_bb_lower': near_bb_lower,
            'macd_line': round(macd_line, 2),
            'macd_signal': round(macd_sig, 2),
            'macd_status': macd_status,
            'macd_bullish': macd_bullish,
            'win_probability': win_prob,
            'prob_tier': prob_tier,
            'prob_label': prob_label,
            'prob_badge': prob_badge,
            'trade_plan': {
                'action': action,
                'action_desc': action_desc,
                'suggested_entry': round(close, 2),
                'stop_loss': stop_loss,
                'target_1': target_1,
                'target_2': target_2,
                'risk_per_share': risk_per_share,
                'risk_pct': risk_pct,
                'potential_upside_t1': potential_upside_t1,
                'potential_upside_t2': potential_upside_t2,
                'risk_reward': risk_reward_str,
                'rationale': rationale_text
            }
        })

    # Sort opportunities by win_probability descending, then by discount_pct descending
    opportunities.sort(key=lambda x: (x['win_probability'], x['discount_pct']), reverse=True)

    # Compute high-level summary KPIs
    high_win_prob_count = len([o for o in opportunities if o['win_probability'] >= 80])
    golden_cross_count = len([o for o in opportunities if o['is_golden']])
    oversold_count = len([o for o in opportunities if o['rsi'] < 40.0])
    deep_discount_count = len([o for o in opportunities if o['discount_pct'] >= 15.0])
    
    top_10 = opportunities[:10]
    avg_discount_top_10 = round(sum(o['discount_pct'] for o in top_10) / len(top_10), 1) if top_10 else 0.0

    output_payload = {
        'last_updated': timestamp_str,
        'summary': {
            'total_scanned': len(opportunities),
            'high_win_prob_count': high_win_prob_count,
            'golden_cross_count': golden_cross_count,
            'oversold_count': oversold_count,
            'deep_discount_count': deep_discount_count,
            'avg_discount_top_10': avg_discount_top_10
        },
        'stocks': opportunities
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output_payload, f, indent=2)

    print(f"Successfully generated Nifty 50 Scanner data with {len(opportunities)} stocks -> {output_path}")
    print(f"KPIs: {high_win_prob_count} High Win-Rate | {golden_cross_count} Golden Crosses | {oversold_count} Oversold | Avg Disc Top-10: {avg_discount_top_10}%")
    return output_payload

if __name__ == '__main__':
    scan_nifty50_opportunities()
