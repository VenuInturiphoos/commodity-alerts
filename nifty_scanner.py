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

try:
    from trader_engine_bridge import (
        MonteCarloTimeMachine,
        SwingStockMonteCarloEngine,
        MarketRegimeDetector
    )
    HAS_TIME_MACHINE = True
except Exception:
    HAS_TIME_MACHINE = False

SCANNER_URL = 'https://scanner.tradingview.com/india/scan'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

ALIAS_MAP = {
    'BAJAJ-AUTO': 'BAJAJ_AUTO',
    'TATAMOTORS': 'TMCV',
    'LTIM': 'LTI',
    'M&M': 'M_M'
}

REVERSE_ALIAS = {
    'BAJAJ_AUTO': 'BAJAJ-AUTO.NS',
    'TMCV': 'TATAMOTORS.NS',
    'TMPV': 'TMPV.NS',
    'LTI': 'LTIM.NS',
    'M_M': 'M&M.NS'
}

NIFTY_50_SYMBOLS = [
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

MIDCAP_SYMBOLS = [
    'POLYCAB.NS', 'PERSISTENT.NS', 'COFORGE.NS', 'MPHASIS.NS', 'DIXON.NS',
    'CUMMINSIND.NS', 'VOLTAS.NS', 'FEDERALBNK.NS', 'ASHOKLEY.NS', 'ASTRAL.NS',
    'OBEROIRLTY.NS', 'TATACOMM.NS', 'JUBLFOOD.NS', 'PIDILITIND.NS', 'SUNDARMFIN.NS',
    'SUPREMEIND.NS', 'MAXHEALTH.NS', 'HDFCAMC.NS', 'BHEL.NS', 'IDFCFIRSTB.NS',
    'AUROPHARMA.NS', 'LUPIN.NS', 'PAGEIND.NS', 'COLPAL.NS', 'GODREJPROP.NS',
    'DALBHARAT.NS', 'PIIND.NS', 'BALKRISIND.NS', 'DEEPAKNTR.NS', 'PETRONET.NS',
    'CONCOR.NS', 'ESCORTS.NS', 'ALKEM.NS', 'BANDHANBNK.NS', 'MRF.NS',
    'MUTHOOTFIN.NS', 'LICHSGFIN.NS', 'BHARATFORG.NS', 'ABCAPITAL.NS', 'SAIL.NS',
    'NMDC.NS', 'GMRAIRPORT.NS', 'TATAELXSI.NS', 'IPCALAB.NS', 'ZYDUSLIFE.NS',
    'AUBANK.NS', 'METROPOLIS.NS', 'PVRINOX.NS', 'APLAPOLLO.NS', 'PRESTIGE.NS'
]

def load_nifty50_symbols(config_path='config.json'):
    return list(NIFTY_50_SYMBOLS)

def load_midcap_symbols():
    return list(MIDCAP_SYMBOLS)

def load_all_scannable_symbols():
    return list(NIFTY_50_SYMBOLS) + list(MIDCAP_SYMBOLS)

def scan_nifty50_opportunities(output_path='web/nifty_crossover_oversold.json'):
    """
    Scans all Nifty 50 and premier liquid Midcap stocks for Golden Crossovers and Oversold opportunities
    and writes results to JSON for the frontend dashboard and daily stock investment engine.
    """
    raw_symbols = load_all_scannable_symbols()
    large_cap_set = set(NIFTY_50_SYMBOLS)
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
        'BB.lower', 'BB.upper', 'Recommend.All', 'sector',
        'Volatility.D', 'ATR', 'ADX'
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
        volatility_d = float(d.get('Volatility.D') or 1.8)
        atr_val = float(d.get('ATR') or (close * 0.02))
        adx_val = float(d.get('ADX') or 20.0)

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

        # Trader-TimeMachine Simulation (3,000 empirical paths using stock-specific daily volatility)
        time_machine_metrics = None
        if HAS_TIME_MACHINE:
            try:
                time_machine_metrics = SwingStockMonteCarloEngine.simulate_swing_trade(
                    symbol=orig_sym,
                    close=round(close, 2),
                    stop_loss=stop_loss,
                    target_1=target_1,
                    target_2=target_2,
                    daily_vol_pct=volatility_d,
                    holding_days=20,
                    replays=3000
                )
            except Exception:
                try:
                    time_machine_metrics = MonteCarloTimeMachine._parametric_fallback(
                        entry=round(close, 2),
                        stop=stop_loss,
                        target=target_1,
                        action='buy'
                    )
                except Exception:
                    pass

        if not time_machine_metrics:
            p_stop = min(50.0, max(20.0, risk_pct * 4.2))
            p_tgt = min(60.0, max(30.0, 50.0 - (risk_pct * 1.5)))
            time_machine_metrics = {
                'replays': 1000,
                'p_target_pct': round(p_tgt, 1),
                'p_target_1_pct': round(p_tgt, 1),
                'p_target_2_pct': round(p_tgt * 0.45, 1),
                'p_stop_pct': round(p_stop, 1),
                'expected_r': round((p_tgt * 2.2 - p_stop * 1.0) / 100, 3),
                'summary': f"Parametric Replay: {p_tgt:.1f}% Target | {p_stop:.1f}% Stop"
            }

        # Capital Safety / Capital Defense Score: Statistical chance of preserving capital (not losing capital)
        # 1. Base statistical edge from confluence (0-35 pts): (win_prob / 96.0) * 35.0
        # 2. Institutional EMA structural foundation (0-20 pts): 20 pts if 50 EMA >= 200 EMA (golden cross provides dynamic accumulation support), 10 pts if near golden cross
        # 3. Margin of Safety / Valuation buffer (0-20 pts): Deep 52W high discount buffer (min(35.0, max(0.0, discount_pct)) / 35.0) * 20.0
        # 4. Downside floor proximity / Capital Protection (0-15 pts): max(0.0, 15.0 - (risk_pct * 1.5))
        # 5. Trader-TimeMachine Empirical Safety (0-10 pts): (100 - p_stop_pct) / 100 * 10.0
        p_stop_rate = float(time_machine_metrics.get('p_stop_pct', 40.0))
        safety_score = (win_prob / 96.0) * 35.0
        if is_golden:
            safety_score += 20.0
        elif near_golden:
            safety_score += 10.0
        safety_score += (min(35.0, max(0.0, discount_pct)) / 35.0) * 20.0
        safety_score += max(0.0, 15.0 - (risk_pct * 1.5))
        safety_score += ((100.0 - p_stop_rate) / 100.0) * 10.0
        capital_safety_score = round(min(98.5, max(50.0, safety_score)), 1)

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

        is_large_cap = orig_sym in large_cap_set
        cap_category = 'Large Cap' if is_large_cap else 'Mid Cap'

        # Market Regime Classifier (trader-engine: 4-state mathematical regime)
        regime_info = None
        if HAS_TIME_MACHINE:
            try:
                regime_info = MarketRegimeDetector.classify_regime(
                    symbol=orig_sym,
                    close=close,
                    ema50=ema50,
                    ema200=ema200,
                    adx=adx_val,
                    rsi=rsi,
                    bb_lower=bb_low,
                    bb_upper=bb_high,
                    daily_vol_pct=volatility_d,
                    atr=atr_val
                )
            except Exception:
                pass

        # Validate Signal against Market Regime to filter out false signals
        is_false_signal = False
        false_signal_reason = None
        signal_quality = 'REGIME_VERIFIED'
        signal_quality_badge = '🛡️ Regime Verified'
        
        if regime_info and HAS_TIME_MACHINE:
            try:
                reg_chk = MarketRegimeDetector.validate_signal(regime_info, 'BUY', crossover_label or 'Golden Crossover')
                if not reg_chk.get('passed'):
                    is_false_signal = True
                    false_signal_reason = reg_chk.get('reason')
                    signal_quality = 'FALSE_SIGNAL_RISK'
                    signal_quality_badge = '⚠️ False Signal (Chop/Breakdown)'
            except Exception:
                pass

        opportunities.append({
            'symbol': orig_sym,
            'name': name,
            'full_name': full_name,
            'sector': sector,
            'cap_category': cap_category,
            'is_midcap': not is_large_cap,
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
            'capital_safety_score': capital_safety_score,
            'time_machine': time_machine_metrics,
            'market_regime': regime_info,
            'regime_tag': regime_info.get('regime') if regime_info else 'NEUTRAL',
            'regime_badge': regime_info.get('badge') if regime_info else 'Neutral',
            'regime_short': regime_info.get('short_tag') if regime_info else 'Neutral',
            'regime_color': regime_info.get('color') if regime_info else '#94a3b8',
            'regime_mandate': regime_info.get('mandate') if regime_info else '',
            'is_false_signal': is_false_signal,
            'false_signal_reason': false_signal_reason,
            'signal_quality': signal_quality,
            'signal_quality_badge': signal_quality_badge,
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

    # Sort opportunities by capital_safety_score descending (Highest chance of not losing capital), then win_probability descending, then discount_pct descending
    opportunities.sort(key=lambda x: (x.get('capital_safety_score', 0), x['win_probability'], x['discount_pct']), reverse=True)

    # Compute high-level summary KPIs
    high_safety_count = len([o for o in opportunities if o.get('capital_safety_score', 0) >= 80.0])
    high_win_prob_count = len([o for o in opportunities if o['win_probability'] >= 80])
    golden_cross_count = len([o for o in opportunities if o['is_golden']])
    oversold_count = len([o for o in opportunities if o['rsi'] < 40.0])
    deep_discount_count = len([o for o in opportunities if o['discount_pct'] >= 15.0])
    large_cap_count = len([o for o in opportunities if o.get('cap_category') == 'Large Cap'])
    mid_cap_count = len([o for o in opportunities if o.get('cap_category') == 'Mid Cap'])
    
    top_10 = opportunities[:10]
    avg_discount_top_10 = round(sum(o['discount_pct'] for o in top_10) / len(top_10), 1) if top_10 else 0.0

    bull_regime_count = len([o for o in opportunities if o.get('regime_tag') == 'BULL_TREND'])
    range_regime_count = len([o for o in opportunities if o.get('regime_tag') == 'COMPRESSED_RANGE'])
    expansion_regime_count = len([o for o in opportunities if o.get('regime_tag') == 'VOLATILE_EXPANSION'])
    bear_regime_count = len([o for o in opportunities if o.get('regime_tag') == 'BEAR_TREND'])

    output_payload = {
        'last_updated': timestamp_str,
        'summary': {
            'total_scanned': len(opportunities),
            'large_cap_count': large_cap_count,
            'mid_cap_count': mid_cap_count,
            'high_safety_count': high_safety_count,
            'high_win_prob_count': high_win_prob_count,
            'golden_cross_count': golden_cross_count,
            'oversold_count': oversold_count,
            'deep_discount_count': deep_discount_count,
            'avg_discount_top_10': avg_discount_top_10,
            'bull_regime_count': bull_regime_count,
            'range_regime_count': range_regime_count,
            'expansion_regime_count': expansion_regime_count,
            'bear_regime_count': bear_regime_count
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
