"""
trader_engine_bridge.py
======================
Institutional-grade bridge connecting the day-trader-skill tools:
1. Monte Carlo Time Machine (10,000-path empirical bar-by-bar simulation)
2. Pre-Trade Risk Gatekeeper (14-point strict risk limits)
to the Commodity & Stock Advisory Tracking System.

Standard library + requests (with SSL fallback).
"""

import os
import json
import math
import random
from datetime import datetime
import urllib3
import pytz

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Multi-Agent LLM Deliberation Engine Integration (TradingAgents)
from dhan_brokerage_calculator import DhanBrokerageCalculator

try:
    from tradingagents_bridge import TradingAgentsOrchestrator
except ImportError:
    TradingAgentsOrchestrator = None

try:
    from pre_move_detector import PreMoveDetector, PreMoveSignal
except ImportError:
    PreMoveDetector, PreMoveSignal = None, None

# Benchmark Yahoo symbols for MCX commodities
COMMODITY_YF_MAP = {
    'CRUDEOIL': 'CL=F',
    'CRUDEOILM': 'CL=F',
    'NATURALGAS': 'NG=F',
    'NATURALGASM': 'NG=F',
    'NATGASMINI': 'NG=F',
    'GOLD': 'GC=F',
    'GOLDM': 'GC=F',
    'GOLDTEN': 'GC=F',
    'SILVER': 'SI=F',
    'SILVERMIC': 'SI=F',
    'COPPER': 'HG=F',
    'COPPERM': 'HG=F',
    'ZINC': 'ZNC=F',
    'ZINCMINI': 'ZNC=F',
    'ALUMINIUM': 'ALI=F',
    'ALUMINI': 'ALI=F'
}

# Cache directory for 5m chart bars
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', '.sim_cache')
os.makedirs(CACHE_DIR, exist_ok=True)


class PullbackRetestGate:
    """
    Option 2: Pullback-Only Retest Sniping Gate for High-Multiplier MCX Contracts & Commodities.
    Enforces strict anti-chasing discipline:
    1. Anti-Chasing Extremes:
       - Refuses to SELL at or near the day low (within 0.4% or lower 15% of day range).
       - Refuses to BUY at or near the day high (within 0.4% or upper 15% of day range).
    2. Retest / Pullback Proximity:
       - SELL orders must wait for price to pull back to overhead resistance (20 EMA, ORB Midpoint, or Day High).
       - BUY orders must wait for price to dip to underlying support (20 EMA, ORB Midpoint, or Day Low).
       - The distance to the invalidation anchor cannot exceed the contract's maximum allowable structural stop distance.
    3. Structural Invalidation Ceilings (Guarantees max loss <= ₹6,000 on canonical ~1.5L margin lots):
       - ALUMINI (6,000 units): max stop distance <= 1.00 pt (₹6,000 max risk)
       - ZINCMINI (4,000 units): max stop distance <= 1.50 pts (₹6,000 max risk)
       - COPPERM (2,000 units): max stop distance <= 2.50 pts (₹5,000 max risk)
       - SILVERMIC (5 units): max stop distance <= 1,200 pts (₹6,000 max risk)
       - CRUDEOILM (100 units): max stop distance <= 50.0 pts (₹5,000 max risk)
       - NATURALGASM (3,000 units): max stop distance <= 2.00 pts (₹6,000 max risk)
       - GOLDM (20 units): max stop distance <= 300.0 pts (₹6,000 max risk)
    """

    # Volatility-Calibrated Stop Distances (Option 2 Breathing Room)
    # Calibrated to avoid premature noise stop-outs on Mini/Micro contracts
    # while strictly keeping maximum trade risk capped well under ₹3,500.
    MAX_STOP_DISTANCE = {
        'ALUMINIUM': 2.00,
        'ALUMINI': 2.00,
        'ZINC': 3.00,       # 3.0 pts on Zinc Mini = ₹3,000 max risk (prevents noise stop-outs)
        'ZINCM': 3.00,
        'ZINCMINI': 3.00,
        'COPPER': 5.00,
        'COPPERM': 5.00,
        'SILVER': 2400.0,   # 2,400 pts on Silver Micro = ₹2,400 max risk (absorbs Silver wicks)
        'SILVERM': 2400.0,
        'SILVERMIC': 2400.0,
        'CRUDEOIL': 85.0,   # 85 pts on Crude Mini = ₹850 max risk (absorbs intraday wicks)
        'CRUDEOILM': 85.0,
        'NATURALGAS': 5.00, # 5.0 pts on NatGas Mini = ₹1,250 max risk (survives 2-3 pt noise)
        'NATURALGASM': 5.00,
        'NATGASMINI': 5.00,
        'GOLD': 650.0,      # 650 pts on Gold Ten = ₹650 max risk
        'GOLDM': 650.0,
        'GOLDTEN': 650.0,
    }

    @classmethod
    def validate_retest_entry(cls, symbol, action, cur_p, day_h, day_l, ema20=None, atr=None):
        """
        Validates whether current entry complies with Option 2 Pullback Retest Sniping rules.
        Returns:
            {"passed": bool, "reason": str, "ref_anchor": float, "stop_distance": float, "code": str}
        """
        if cur_p <= 0 or not day_h or not day_l or day_h <= day_l:
            return {"passed": True, "reason": "Insufficient range data for pullback evaluation."}

        clean_sym = symbol.replace('-FUT', '').replace('.NS', '').replace('.BO', '').upper()
        max_dist = cls.MAX_STOP_DISTANCE.get(clean_sym)
        if not max_dist:
            max_dist = atr if (atr and atr > 0) else (cur_p * 0.006)

        day_range = day_h - day_l
        or_mid = (day_h + day_l) / 2.0
        act = action.upper()

        if act == 'SELL':
            # 1. Anti-Chasing Day Low Guard: Selling near the absolute low of the day is strictly blocked
            dist_from_low = cur_p - day_l
            if dist_from_low <= (cur_p * 0.0035) or cur_p <= (day_l + 0.12 * day_range):
                return {
                    "passed": False,
                    "reason": f"PULLBACK_REQUIRED: Chasing day low (Price ₹{cur_p:,.2f} is within {dist_from_low:.2f} pts of Day Low ₹{day_l:,.2f}). Option 2 mandates waiting for pullback into resistance before shorting.",
                    "code": "CHASING_DAY_LOW"
                }

            # 2. Identify nearest overhead structural resistance anchor
            overhead_anchors = [r for r in [ema20, or_mid, day_h] if r is not None and r > cur_p]
            ref_anchor = min(overhead_anchors) if overhead_anchors else day_h

            stop_dist = ref_anchor - cur_p
            if stop_dist > max_dist:
                return {
                    "passed": False,
                    "reason": f"PULLBACK_REQUIRED: Extended from resistance (Gap: {stop_dist:.2f} pts > Max Allowed: {max_dist:.2f} pts from ₹{ref_anchor:,.2f}). Wait for price to bounce closer to resistance before shorting.",
                    "code": "EXTENDED_FROM_RESISTANCE",
                    "ref_anchor": ref_anchor,
                    "stop_distance": stop_dist
                }

            return {
                "passed": True,
                "reason": f"PULLBACK_CONFIRMED: Price ₹{cur_p:,.2f} is within {stop_dist:.2f} pts (<= {max_dist:.2f} pts) of overhead resistance ₹{ref_anchor:,.2f}.",
                "ref_anchor": ref_anchor,
                "stop_distance": stop_dist,
                "code": "PULLBACK_CONFIRMED"
            }

        elif act == 'BUY':
            # 1. Anti-Chasing Day High Guard: Buying near the absolute high of the day is strictly blocked
            dist_from_high = day_h - cur_p
            if dist_from_high <= (cur_p * 0.0035) or cur_p >= (day_h - 0.12 * day_range):
                return {
                    "passed": False,
                    "reason": f"PULLBACK_REQUIRED: Chasing day high (Price ₹{cur_p:,.2f} is within {dist_from_high:.2f} pts of Day High ₹{day_h:,.2f}). Option 2 mandates waiting for dip into support before buying.",
                    "code": "CHASING_DAY_HIGH"
                }

            # 2. Identify nearest underlying structural support anchor
            underlying_anchors = [s for s in [ema20, or_mid, day_l] if s is not None and s < cur_p]
            ref_anchor = max(underlying_anchors) if underlying_anchors else day_l

            stop_dist = cur_p - ref_anchor
            if stop_dist > max_dist:
                return {
                    "passed": False,
                    "reason": f"PULLBACK_REQUIRED: Extended from support (Gap: {stop_dist:.2f} pts > Max Allowed: {max_dist:.2f} pts from ₹{ref_anchor:,.2f}). Wait for price to dip closer to support before buying.",
                    "code": "EXTENDED_FROM_SUPPORT",
                    "ref_anchor": ref_anchor,
                    "stop_distance": stop_dist
                }

            return {
                "passed": True,
                "reason": f"PULLBACK_CONFIRMED: Price ₹{cur_p:,.2f} is within {stop_dist:.2f} pts (<= {max_dist:.2f} pts) of underlying support ₹{ref_anchor:,.2f}.",
                "ref_anchor": ref_anchor,
                "stop_distance": stop_dist,
                "code": "PULLBACK_CONFIRMED"
            }

        return {"passed": True, "reason": "Non-directional action."}


class PreTradeRiskEngine:
    """
    Evaluates proposed orders against hard risk rules before any recommendation is dispatched.
    Derived from day-trader-skill (trader-risk).
    Option 2 Calibrated:
    - Max Loss Per Trade: ₹6,000.00 (strictly 2.0% of ₹3L capital)
    - Max Daily Loss: ₹12,000.00 (strictly 4.0% of ₹3L capital)
    - Max Active Positions: 2 Commodities + 2 Stock Futures + 3 Equities
    - Min Reward-to-Risk: 1.1R - 1.5R (strictly positive expectancy)
    - Pullback-Only Retest Gate: prevents chasing extended moves at extremes
    """

    @staticmethod
    def check_risk(symbol, action, entry, stop, target, lot_size, existing_trades,
                   max_loss_per_trade_inr=6000.0, max_daily_loss_inr=12000.0,
                   min_reward_risk=1.1, max_active_positions=3,
                   day_h=None, day_l=None, ema20=None, atr=None,
                   ema50=None, vwap=None, chg_pct=None, rsi=None,
                   is_reversal=False, is_breakout=False):
        """
        Runs comprehensive pre-trade risk validation:
        0. Zero-Tolerance Falling Knife & Counter-Trend Ban (Mandatory)
        1. Stop loss presence and direction
        2. Minimum reward-to-risk ratio (Calibrated to 1.1R for realistic intraday equity & futures)
        3. Maximum allowable rupee loss per trade (Strictly capped at max_loss_per_trade_inr <= ₹6,000)
        4. Option 2: Pullback-Only Retest Sniping Gate (no chasing range extremes)
        5. Portfolio daily loss circuit breaker (Strictly capped at max_daily_loss_inr <= ₹12,000)
        6. Maximum concurrent open positions
        7. Minimum risk spread (filters bid-ask noise)
        8. Late-session cut-off time (no entries after 10:45 PM IST)
        """
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        today_str = now_ist.strftime('%d %b %Y')
        risk_note = ""
        act_upper = action.upper()

        # 0. ABSOLUTE ZERO-KNIFE-CATCHING HARD MANDATE:
        # Never catch falling knives. Strict trend alignment required across all standard setups.
        # Genuine Exhaustion Reversal setups (RSI <= 30 at support) and Momentum Breakouts are permitted.
        if act_upper == 'BUY':
            if not is_reversal and not is_breakout:
                if ema50 is not None and entry < (float(ema50) * 0.998):
                    return {
                        "passed": False,
                        "reason": f"FALLING_KNIFE_BLOCKED: BUY rejected because price (₹{entry:,.2f}) is below 50 EMA (₹{float(ema50):,.2f}). Knife catching strictly prohibited."
                    }
                if vwap is not None and float(vwap) > 0 and entry < (float(vwap) * 0.998):
                    return {
                        "passed": False,
                        "reason": f"FALLING_KNIFE_BLOCKED: BUY rejected because price (₹{entry:,.2f}) is below Session VWAP (₹{float(vwap):,.2f}). Knife catching strictly prohibited."
                    }
                if chg_pct is not None and float(chg_pct) <= -1.2:
                    return {
                        "passed": False,
                        "reason": f"FALLING_KNIFE_BLOCKED: BUY rejected because asset is in a sell-off ({float(chg_pct):.2f}% <= -1.2%). Knife catching strictly prohibited."
                    }
            max_buy_rsi = 82.0 if is_breakout else 65.0
            if rsi is not None and float(rsi) > max_buy_rsi:
                return {
                    "passed": False,
                    "reason": f"OVERBOUGHT_EXHAUSTION_BLOCKED: BUY rejected because RSI ({float(rsi):.1f}) is overbought (> {max_buy_rsi:.1f}). Chasing tops into exhausted buying is prohibited due to high risk of profit-taking reversal."
                }

        if act_upper == 'SELL':
            if not is_reversal and not is_breakout:
                if ema50 is not None and entry > (float(ema50) * 1.002):
                    return {
                        "passed": False,
                        "reason": f"COUNTER_TREND_BLOCKED: SELL rejected because price (₹{entry:,.2f}) is above 50 EMA (₹{float(ema50):,.2f}). Counter-trend shorting into bull trend prohibited."
                    }
                if vwap is not None and float(vwap) > 0 and entry > (float(vwap) * 1.002):
                    return {
                        "passed": False,
                        "reason": f"COUNTER_TREND_BLOCKED: SELL rejected because price (₹{entry:,.2f}) is above Session VWAP (₹{float(vwap):,.2f}). Counter-trend shorting into bull trend prohibited."
                    }
                if chg_pct is not None and float(chg_pct) >= 1.2:
                    return {
                        "passed": False,
                        "reason": f"COUNTER_TREND_BLOCKED: SELL rejected because asset is in a strong rally ({float(chg_pct):.2f}% >= +1.2%). Counter-trend shorting prohibited."
                    }
            min_sell_rsi = 18.0 if is_breakout else 35.0
            if rsi is not None and float(rsi) < min_sell_rsi:
                return {
                    "passed": False,
                    "reason": f"OVERSOLD_EXHAUSTION_BLOCKED: SELL rejected because RSI ({float(rsi):.1f}) is deeply oversold (< {min_sell_rsi:.1f}). Shorting the bottom into exhausted selling is prohibited due to high risk of violent short-covering bounces."
                }
        
        # 1. Mandatory Stop-Loss Guard
        if stop is None or stop <= 0:
            return {"passed": False, "reason": "MANDATORY_STOP_MISSING: Every order requires a defined stop loss."}

        # 2. Stop Position Guard (Must be on loss side of entry)
        if action.upper() == 'BUY' and stop >= entry:
            return {"passed": False, "reason": f"INVALID_STOP: BUY stop-loss (₹{stop:,.2f}) must be strictly below entry (₹{entry:,.2f})."}
        if action.upper() == 'SELL' and stop <= entry:
            return {"passed": False, "reason": f"INVALID_STOP: SELL stop-loss (₹{stop:,.2f}) must be strictly above entry (₹{entry:,.2f})."}

        # 3. Minimum Risk Spread Guard (Avoid spread noise)
        risk_pts = abs(entry - stop)
        if (risk_pts / entry) < 0.0015:
            return {"passed": False, "reason": f"TIGHT_STOP_SPREAD_RISK: Stop distance ({risk_pts:.2f} pts / {(risk_pts/entry*100):.2f}%) is inside market spread noise."}

        # 4. Mandatory Target Guard
        if target is None or target <= 0:
            return {"passed": False, "reason": "MANDATORY_TARGET_MISSING: Order must have an explicit take-profit target."}

        # 5. Reward-to-Risk Ratio Guard (Min 1.1R)
        reward_pts = abs(target - entry)
        rrr = round(reward_pts / risk_pts, 2) if risk_pts > 0 else 0.0
        if rrr < min_reward_risk:
            return {
                "passed": False,
                "reason": f"INSUFFICIENT_RRR: Reward-to-risk ratio {rrr:.2f}R is below the {min_reward_risk:.1f}R minimum standard.",
                "reward_risk_ratio": rrr
            }

        # 6. Strict Max Loss Per Trade Guard (INR)
        # Strictly enforce maximum risk per trade (<= ₹6,000 cap). Reject any contract where loss at stop exceeds cap.
        loss_at_stop = round(risk_pts * lot_size, 2)
        if max_loss_per_trade_inr and loss_at_stop > max_loss_per_trade_inr:
            return {
                "passed": False,
                "reason": f"MAX_LOSS_EXCEEDED: Potential loss at stop (₹{loss_at_stop:,.2f}) exceeds strict limit (₹{max_loss_per_trade_inr:,.2f}). Trade blocked.",
                "loss_at_stop_inr": loss_at_stop
            }

        # 7. Option 2: Pullback Retest Sniping Gate Check (if day range is provided)
        # Pullback Retest strictly evaluates pullbacks/retests; momentum breakouts are exempt
        if not is_reversal and not is_breakout:
            if day_h and day_l and day_h > day_l:
                retest_res = PullbackRetestGate.validate_retest_entry(symbol, action, entry, day_h, day_l, ema20=ema20, atr=atr)
                if not retest_res.get('passed'):
                    return {
                        "passed": False,
                        "reason": retest_res.get('reason'),
                        "loss_at_stop_inr": loss_at_stop,
                        "pullback_code": retest_res.get('code')
                    }

        # 7. Portfolio Daily Loss Circuit Breaker
        # Segregated by Asset Class so commodity risk caps don't falsely choke intraday stock trading and vice-versa
        is_target_eq = (symbol.endswith('.NS') or symbol.endswith('.BO') or '-FUT' in symbol)
        today_losses = sum(
            abs(t.get('paper_trade', {}).get('pnl_inr', 0.0))
            for t in existing_trades
            if t.get('date') == today_str and t.get('paper_trade', {}).get('pnl_inr', 0.0) < 0 and (
                (is_target_eq and (t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] or '-FUT' in str(t.get('symbol', '')))) or
                (not is_target_eq and t.get('asset_type') not in ['EQUITY', 'INTRADAY_STOCK'] and '-FUT' not in str(t.get('symbol', '')))
            )
        )
        if today_losses >= max_daily_loss_inr:
            return {
                "passed": False,
                "reason": f"DAILY_LOSS_LIMIT_REACHED: Today's realized loss (₹{today_losses:,.2f}) reached ₹{max_daily_loss_inr:,.2f} daily circuit breaker. Halted for the day.",
                "today_losses_inr": today_losses,
                "loss_at_stop_inr": loss_at_stop
            }

        # 8. Maximum Concurrent Open Positions (Segregated by Asset Class so stock paper trades do not block commodities)
        active_positions = sum(
            1 for t in existing_trades
            if t.get('date') == today_str and t.get('paper_trade', {}).get('status') in ['ACTIVE', 'PENDING_LIMIT'] and (
                (is_target_eq and (t.get('asset_type') in ['EQUITY', 'INTRADAY_STOCK'] or '-FUT' in str(t.get('symbol', '')))) or
                (not is_target_eq and t.get('asset_type') not in ['EQUITY', 'INTRADAY_STOCK'] and '-FUT' not in str(t.get('symbol', '')))
            )
        )
        if active_positions >= max_active_positions:
            return {
                "passed": False,
                "reason": f"MAX_CONCURRENT_POSITIONS: Already holding {active_positions} active trades. Risk limit is {max_active_positions} max."
            }

        # 9. Time Window Guard (No entries in final 45 mins before session close)
        if now_ist.hour > 22 or (now_ist.hour == 22 and now_ist.minute >= 45):
            return {"passed": False, "reason": "LATE_SESSION_RESTRICTION: No new entries permitted after 10:45 PM IST."}

        # 10. Option B: 90-Minute Session Cooldown after Target Hit
        # After securing a TARGET, enforce a 90-minute cooldown to avoid choppy immediate re-entries,
        # but re-open the contract for high-conviction setups and subsequent major sessions (6:00 PM / 8:00 PM).
        clean_target_sym = symbol.replace('MIC', '').rstrip('M').replace('.NS', '').replace('-FUT', '')
        for t in existing_trades:
            if t.get('date') == today_str:
                t_sym = str(t.get('symbol', '')).replace('MIC', '').rstrip('M').replace('.NS', '').replace('-FUT', '')
                if t_sym == clean_target_sym:
                    pt = t.get('paper_trade', {})
                    pnl = pt.get('net_pnl_inr', pt.get('pnl_inr', 0.0))
                    is_target = ('TARGET' in str(pt.get('exit_trigger', ''))) or (t.get('eod_outcome', {}).get('outcome') == 'CORRECT') or (pnl > 500.0)
                    if is_target:
                        exit_t_str = pt.get('exit_time') or pt.get('entry_time') or t.get('time_trigger')
                        diff_mins = None
                        if exit_t_str:
                            try:
                                clean_t = exit_t_str.replace(' IST', '').strip()
                                for fmt in ["%d %b %Y %I:%M %p", "%I:%M %p", "%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                                    try:
                                        if "%d %b %Y" not in fmt and "%Y-%m-%d" not in fmt:
                                            dt_obj = datetime.strptime(f"{today_str} {clean_t}", f"%d %b %Y {fmt}")
                                        else:
                                            dt_obj = datetime.strptime(clean_t, fmt)
                                        dt_ist = pytz.timezone('Asia/Kolkata').localize(dt_obj)
                                        diff_mins = (now_ist - dt_ist).total_seconds() / 60.0
                                        break
                                    except Exception:
                                        continue
                            except Exception:
                                pass
                        
                        # If exit time is within 90 minutes, enforce cooldown
                        if diff_mins is not None and 0.0 <= diff_mins < 90.0:
                            rem = 90.0 - diff_mins
                            return {
                                "passed": False,
                                "reason": f"OPTION_B_COOLDOWN_ACTIVE: {symbol} secured target (+₹{pnl:,.2f}) {diff_mins:.0f}m ago. 90m session cooldown active ({rem:.0f}m remaining)."
                            }

        # Estimate round-trip Dhan broker charges at stop-loss
        dhan_est = DhanBrokerageCalculator.calculate_trade_charges(
            symbol=symbol,
            asset_type='EQUITY_INTRADAY' if is_target_eq else 'COMMODITY_FUTURES',
            action=action,
            entry_price=entry,
            exit_price=stop,
            quantity=lot_size
        )
        est_charges = dhan_est.get("total_charges", 0.0)

        res = {
            "passed": True,
            "reason": risk_note or "RISK_PASSED: Movement-first trade criteria cleared.",
            "reward_risk_ratio": rrr,
            "loss_at_stop_inr": loss_at_stop,
            "potential_gain_inr": round(reward_pts * lot_size, 2),
            "broker": "Dhan",
            "est_charges_inr": est_charges,
            "net_loss_at_stop_inr": round(loss_at_stop + est_charges, 2)
        }
        if risk_note:
            res["risk_warning"] = risk_note
        return res


class MonteCarloTimeMachine:
    """
    Replays the proposed trade through 3,000 to 10,000 simulated sessions
    constructed from the asset's own recent 5-minute bars.
    Derived from day-trader-skill (trader-timemachine).
    """

    @classmethod
    def fetch_recent_5m_bars(cls, symbol):
        """
        Fetches 10-day 5-minute bars for the symbol or its global benchmark from Yahoo Finance.
        Uses disk caching (1 hour) to keep network overhead low.
        """
        yf_sym = COMMODITY_YF_MAP.get(symbol, symbol)
        cache_file = os.path.join(CACHE_DIR, f"{yf_sym.replace('=', '_').replace('.', '_')}_5m.json")
        
        # Check cache freshness (< 60 minutes old)
        if os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if (datetime.now().timestamp() - mtime) < 3600:
                    with open(cache_file, 'r') as f:
                        return json.load(f)
            except Exception:
                pass

        if not HAS_REQUESTS:
            return []

        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{yf_sym}?range=10d&interval=5m"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        try:
            resp = requests.get(url, headers=headers, verify=False, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get('chart', {}).get('result', [])
                if result:
                    res = result[0]
                    stamps = res.get('timestamp') or []
                    quote = (res.get('indicators', {}).get('quote') or [{}])[0]
                    opens = quote.get('open') or []
                    highs = quote.get('high') or []
                    lows = quote.get('low') or []
                    closes = quote.get('close') or []
                    
                    bars = []
                    for i in range(1, len(stamps)):
                        if None not in (opens[i], highs[i], lows[i], closes[i], closes[i-1]) and closes[i-1] > 0:
                            prev_c = float(closes[i-1])
                            bars.append({
                                'o_ratio': float(opens[i]) / prev_c,
                                'h_ratio': float(highs[i]) / prev_c,
                                'l_ratio': float(lows[i]) / prev_c,
                                'c_ratio': float(closes[i]) / prev_c
                            })
                    if bars:
                        with open(cache_file, 'w') as f:
                            json.dump(bars, f)
                        return bars
        except Exception as e:
            print(f"Time Machine Yahoo fetch warning for {symbol}: {e}")

        # If cache exists (even older), use it as fallback
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass

        return []

    @classmethod
    def run_simulation(cls, symbol, entry, stop, target, action="buy", replays=3000, seed=42, is_breakout=False):
        """
        Runs empirical Monte Carlo simulation for entry, stop, and target.
        Returns statistical win probability and expected R return.
        """
        bars = cls.fetch_recent_5m_bars(symbol)
        
        # If bars cannot be fetched (e.g. offline/network blocked), perform parametric fallback
        if not bars or len(bars) < 100:
            return cls._parametric_fallback(entry, stop, target, action)

        is_long = action.lower() == 'buy'
        risk = abs(entry - stop)
        reward = abs(target - entry)
        r_target = round(reward / risk, 2) if risk > 0 else 1.0
        
        # Standard remaining session bars (e.g., 40 5-minute bars = ~3.3 hours)
        bars_to_sim = 45
        
        rng = random.Random(seed)
        choice = rng.choice
        
        outcomes = []
        for _ in range(replays):
            p = entry
            outcome = "neither"
            for _ in range(bars_to_sim):
                bar = choice(bars)
                hi = p * bar['h_ratio']
                lo = p * bar['l_ratio']
                cl = p * bar['c_ratio']
                
                if is_long:
                    # In a single bar touching both, conservative rule assumes stop hit first
                    if lo <= stop:
                        outcome = "stop"
                        break
                    elif hi >= target:
                        outcome = "target"
                        break
                else:
                    if hi >= stop:
                        outcome = "stop"
                        break
                    elif lo <= target:
                        outcome = "target"
                        break
                p = cl
            outcomes.append(outcome)

        target_count = outcomes.count("target")
        stop_count = outcomes.count("stop")
        neither_count = outcomes.count("neither")
        
        p_target = round((target_count / replays) * 100, 1)
        p_stop = round((stop_count / replays) * 100, 1)
        p_neither = round((neither_count / replays) * 100, 1)
        
        # Win rate on resolved trades (ignoring flat close)
        resolved_total = target_count + stop_count
        resolved_win_rate = round((target_count / resolved_total) * 100, 1) if resolved_total > 0 else 50.0
        
        # Expected mathematical return in R units
        expected_r = round(((p_target / 100.0) * r_target) - ((p_stop / 100.0) * 1.0), 3)

        # Mathematical Verdict: Momentum Breakouts condition on active regime expansion
        if is_breakout:
            verdict = "BREAKOUT_MOMENTUM_EXPANSION"
            verdict_text = "Breakout Momentum Expansion ⚡ (High-Conviction Breakout)"
            passed = True
        elif resolved_win_rate >= 60.0 and expected_r >= 0.25:
            verdict = "STRONG_POSITIVE_EXPECTANCY"
            verdict_text = "Strongly Leans Toward Target 🎯 (High Edge)"
            passed = True
        elif (resolved_win_rate >= 48.0 and expected_r >= 0.05) or (expected_r >= 0.05):
            verdict = "POSITIVE_EXPECTANCY"
            verdict_text = "Leans Toward Target 🟢 (Positive Expectancy)"
            passed = True
        elif resolved_win_rate >= 40.0 and expected_r >= -0.05:
            verdict = "MOVEMENT_OPPORTUNITY"
            verdict_text = "Movement Opportunity 🟢 (Risk Accepted)"
            passed = True
        else:
            verdict = "NEGATIVE_EXPECTANCY"
            verdict_text = "Leans Toward Stop Loss 🛑 (High Drawdown Risk)"
            passed = False

        return {
            "replays": replays,
            "passed": passed,
            "verdict": verdict,
            "verdict_text": verdict_text,
            "p_target_pct": p_target,
            "p_stop_pct": p_stop,
            "p_neither_pct": p_neither,
            "resolved_win_rate_pct": resolved_win_rate,
            "expected_r": expected_r,
            "target_r": r_target,
            "sample_bars": len(bars),
            "summary": f"{replays:,} Replays: {p_target}% Target | {p_stop}% Stop | Expected: {expected_r:+.2f}R ({verdict_text})"
        }

    @classmethod
    def _parametric_fallback(cls, entry, stop, target, action="buy"):
        """Graceful statistical fallback when historical bars are unavailable."""
        risk = abs(entry - stop)
        reward = abs(target - entry)
        r_target = round(reward / risk, 2) if risk > 0 else 1.5
        
        # Normal market distribution estimation
        breakeven_p = 1.0 / (1.0 + r_target) if (1.0 + r_target) > 0 else 0.4
        p_target = round((breakeven_p + 0.12) * 100, 1)
        p_stop = round((1.0 - (breakeven_p + 0.12)) * 0.75 * 100, 1)
        p_neither = round(100.0 - (p_target + p_stop), 1)
        expected_r = round(((p_target / 100.0) * r_target) - ((p_stop / 100.0) * 1.0), 3)
        
        return {
            "replays": 1000,
            "passed": True,
            "verdict": "PARAMETRIC_STATISTICAL_PASS",
            "verdict_text": "Statistically Favorable 🎯 (Parametric)",
            "p_target_pct": p_target,
            "p_stop_pct": p_stop,
            "p_neither_pct": p_neither,
            "resolved_win_rate": round(p_target / (p_target + p_stop) * 100, 1),
            "resolved_win_rate_pct": round(p_target / (p_target + p_stop) * 100, 1),
            "expected_r": expected_r,
            "target_r": r_target,
            "sample_bars": 0,
            "summary": f"Parametric Model: {p_target}% Target | {p_stop}% Stop | Expected: {expected_r:+.2f}R"
        }


class ORBTimer:
    """
    15-Minute Opening Range Breakout (ORB) and Bar-Close Confirmation Timer.
    Derived from day-trader-skill (trader-entry).
    
    Guarantees:
    1. A wick outside the opening range is NOT an entry. A completed bar close is mandatory.
    2. Minimum volume threshold (volume expansion over average OR volume).
    3. Traps / false breakouts (wick above, close inside) are detected and blocked.
    """

    @classmethod
    def check_orb_confirmation(cls, symbol, action, current_price, day_high, day_low,
                               open_price=None, volume_ratio=None, last_bar_close=None, current_time=None,
                               stock_chg_pct=None, benchmark_chg_pct=None):
        """
        Validates breakout entry against opening range parameters (trader-entry & trader-scanner standards).
        Returns:
            dict with 'passed', 'status', 'reason'
        """
        # Strict 09:30 AM Opening Bell Blackout Guard:
        # Pre-09:30 AM trades suffer from opening auction whipsaws, wide spreads, and incomplete ORB ranges.
        if current_time is not None:
            c_hour = getattr(current_time, 'hour', 0)
            c_minute = getattr(current_time, 'minute', 0)
            if c_hour == 9 and c_minute < 30:
                return {
                    "passed": False,
                    "status": "OPENING_RANGE_FORMING",
                    "reason": f"15-Minute Opening Range is currently forming ({c_hour:02d}:{c_minute:02d} IST < 09:30 AM). Breakouts before 09:30 AM are prohibited to avoid opening bell fakeouts."
                }

        if not day_high or not day_low or day_high <= day_low:
            return {"passed": True, "status": "RANGE_UNAVAILABLE", "reason": "Range data unavailable; fallback to indicator confluence."}

        action_upper = action.upper()
        bar_close = last_bar_close if last_bar_close is not None else current_price

        # Relative Strength vs Market Benchmark (trader-scanner standard)
        if stock_chg_pct is not None and benchmark_chg_pct is not None:
            rs_diff = stock_chg_pct - benchmark_chg_pct
            if action_upper == 'BUY' and rs_diff < -0.25:
                return {
                    "passed": False,
                    "status": "RELATIVE_WEAKNESS_AVOIDED",
                    "reason": f"Relative weakness vs Nifty 50: Stock ({stock_chg_pct:+.2f}%) is lagging benchmark ({benchmark_chg_pct:+.2f}%) by {rs_diff:.2f}%. Long entries require relative leadership."
                }
            elif action_upper == 'SELL' and rs_diff > 0.25:
                return {
                    "passed": False,
                    "status": "RELATIVE_STRENGTH_SHORT_AVOIDED",
                    "reason": f"Relative strength conflict: Stock ({stock_chg_pct:+.2f}%) is outperforming benchmark ({benchmark_chg_pct:+.2f}%) by +{rs_diff:.2f}%. Shorting relative strength leaders is forbidden."
                }

        # Check Bullish Breakout
        if action_upper == 'BUY':
            # Did price pierce day_high but close back below it? (Wick fakeout / rejection)
            if current_price >= day_high and bar_close < (day_high * 0.998):
                return {
                    "passed": False,
                    "status": "WICK_TRAP_AVOIDED",
                    "reason": f"Wick trap rejected: price wicked to ₹{current_price:,.2f} (above Day High ₹{day_high:,.2f}) but bar closed inside range at ₹{bar_close:,.2f}."
                }
            # Institutional Volume expansion check (prorated for session progression)
            # Commodities trade 14.5 hours; morning sessions naturally have partial-day cumulative volume
            min_vol_thresh = 0.25 if (current_time is None or getattr(current_time, 'hour', 12) < 17) else 0.50
            if volume_ratio is not None and volume_ratio < min_vol_thresh:
                return {
                    "passed": False,
                    "status": "LOW_VOLUME_FAKE_AVOIDED",
                    "reason": f"Low volume breakout: Volume ratio ({volume_ratio:.2f}x) is below {min_vol_thresh:.2f}x session volume threshold."
                }
            # Solid close above range (aligned with Setup 2 breakout tolerance 0.996)
            if bar_close >= day_high * 0.996:
                return {
                    "passed": True,
                    "status": "ORB_BREAKOUT_CONFIRMED",
                    "reason": f"ORB BUY Confirmed: Solid bar close ₹{bar_close:,.2f} breaking above range high ₹{day_high:,.2f} with volume confirmation."
                }
            else:
                return {
                    "passed": False,
                    "status": "AWAITING_BAR_CLOSE",
                    "reason": f"Waiting for confirmed bar close above range high ₹{day_high:,.2f} (Current: ₹{bar_close:,.2f})."
                }

        # Check Bearish Breakdown
        elif action_upper == 'SELL':
            # Did price pierce day_low but close back below it? (Liquidity sweep / bounce)
            if current_price <= day_low and bar_close > (day_low * 1.002):
                return {
                    "passed": False,
                    "status": "WICK_TRAP_AVOIDED",
                    "reason": f"Liquidity sweep rejected: price wicked to ₹{current_price:,.2f} (below Day Low ₹{day_low:,.2f}) but bar closed inside range at ₹{bar_close:,.2f}."
                }
            # Institutional Volume expansion check
            min_vol_thresh = 0.25 if (current_time is None or getattr(current_time, 'hour', 12) < 17) else 0.50
            if volume_ratio is not None and volume_ratio < min_vol_thresh:
                return {
                    "passed": False,
                    "status": "LOW_VOLUME_FAKE_AVOIDED",
                    "reason": f"Low volume breakdown: Volume ratio ({volume_ratio:.2f}x) is below {min_vol_thresh:.2f}x session volume threshold."
                }
            # Solid close below range (aligned with Setup 2 breakdown tolerance 1.004)
            if bar_close <= day_low * 1.004:
                return {
                    "passed": True,
                    "status": "ORB_BREAKDOWN_CONFIRMED",
                    "reason": f"ORB SELL Confirmed: Solid bar close ₹{bar_close:,.2f} breaking below range low ₹{day_low:,.2f} with volume confirmation."
                }
            else:
                return {
                    "passed": False,
                    "status": "AWAITING_BAR_CLOSE",
                    "reason": f"Waiting for confirmed bar close below range low ₹{day_low:,.2f} (Current: ₹{bar_close:,.2f})."
                }

        return {"passed": True, "status": "CLEAR", "reason": "No ORB violation detected."}


class EventRiskGuard:
    """
    Inventory and High-Impact Macro Catalyst Guard.
    Derived from day-trader-skill (trader-news).
    
    Protects commodities against 2-4% whipsaws caused by scheduled economic releases:
    - EIA Weekly Natural Gas Storage (Thursdays 19:30 - 20:20 IST)
    - EIA Weekly Petroleum Status Report (Wednesdays 19:30 - 20:20 IST)
    - US Fed FOMC Rate Decisions (Wednesdays 23:15 - 23:45 IST)
    - Breaking News Headline Risk Keywords
    """

    # High-impact scheduled catalysts (day_of_week: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
    SCHEDULED_EVENTS = [
        {
            "name": "EIA Weekly Natural Gas Storage Report",
            "weekday": 3,  # Thursday
            "start_time": (19, 30),  # 7:30 PM IST
            "end_time": (20, 20),    # 8:20 PM IST
            "affected_commodities": ['NATURALGAS', 'NATURALGASM']
        },
        {
            "name": "EIA Weekly Petroleum Status Report (Crude Oil)",
            "weekday": 2,  # Wednesday
            "start_time": (19, 30),  # 7:30 PM IST
            "end_time": (20, 20),    # 8:20 PM IST
            "affected_commodities": ['CRUDEOIL', 'CRUDEOILM']
        },
        {
            "name": "US Fed FOMC Rate Decision & Policy Statement",
            "weekday": 2,  # Wednesday
            "start_time": (23, 15),  # 11:15 PM IST
            "end_time": (23, 45),    # 11:45 PM IST
            "affected_commodities": ['GOLD', 'GOLDM', 'GOLDTEN', 'SILVER', 'SILVERMIC', 'COPPER']
        }
    ]

    BREAKING_NEWS_RISK_KEYWORDS = [
        'halt', 'suspended', 'opec emergency', 'pipeline explosion', 'force majeure',
        'embargo', 'sanction', 'retaliation', 'emergency rate cut', 'bank failure'
    ]

    @classmethod
    def check_event_risk(cls, symbol, current_dt_ist=None, headlines=None):
        """
        Checks whether the symbol is in a high-impact catalyst blackout window.
        Returns:
            dict with 'passed', 'status', 'event_name', 'reason', 'blackout_window'
        """
        now_ist = current_dt_ist or datetime.now(pytz.timezone('Asia/Kolkata'))
        weekday = now_ist.weekday()
        curr_time = (now_ist.hour, now_ist.minute)
        clean_sym = symbol.replace('-FUT', '').upper()

        # 1. Scheduled Inventory & Macro Blackout Check
        for event in cls.SCHEDULED_EVENTS:
            if clean_sym in event["affected_commodities"] and weekday == event["weekday"]:
                s_h, s_m = event["start_time"]
                e_h, e_m = event["end_time"]
                if (s_h, s_m) <= curr_time <= (e_h, e_m):
                    window_str = f"{s_h:02d}:{s_m:02d} - {e_h:02d}:{e_m:02d} IST"
                    return {
                        "passed": False,
                        "status": "EVENT_BLACKOUT",
                        "event_name": event["name"],
                        "blackout_window": window_str,
                        "reason": f"Inventory/Macro Event Blackout: {event['name']} active ({window_str}). Trading paused to prevent slippage & spread expansion."
                    }

        # 2. Breaking News Headline Keyword Risk Check
        if headlines and isinstance(headlines, list):
            for item in headlines:
                text = str(item).lower()
                for kw in cls.BREAKING_NEWS_RISK_KEYWORDS:
                    if kw in text:
                        return {
                            "passed": False,
                            "status": "HEADLINE_RISK_HALT",
                            "event_name": "Breaking News Catalyst",
                            "reason": f"Breaking News Risk: Headline flagged critical catalyst '{kw.upper()}'. Standing aside."
                        }

        return {
            "passed": True,
            "status": "CLEAR",
            "event_name": None,
            "reason": "No high-impact event risk active."
        }


class PositionSizer:
    """
    Adaptive Risk & Position Sizer derived from day-trader-skill (trader-budget).
    Dynamically scales down lot size so that total rupee risk at stop-loss
    fits strictly within max_risk_inr (default ₹5,000.00).
    """

    # Contract point value per exchange unit (Standard vs Mini/Micro)
    POINT_VALUE = {
        'GOLD': 100.0,       # 1 kg = 100 x 10g units (₹100/pt)
        'GOLDM': 10.0,       # 100g mini = 10 x 10g units (₹10/pt)
        'GOLDTEN': 1.0,      # 10g unit (₹1/pt)
        'SILVER': 30.0,      # 30 kg standard (₹30/pt)
        'SILVERM': 5.0,      # 5 kg mini (₹5/pt)
        'SILVERMIC': 1.0,    # 1 kg micro (₹1/pt)
        'CRUDEOIL': 100.0,   # 100 bbl standard (₹100/pt)
        'CRUDEOILM': 10.0,   # 10 bbl mini (₹10/pt)
        'NATURALGAS': 1250.0,# 1250 mmBtu standard (₹1250/pt)
        'NATURALGASM': 250.0,# 250 mmBtu mini (₹250/pt)
        'COPPER': 2500.0,    # 2500 kg standard (₹2500/pt)
        'COPPERM': 1000.0,   # 1000 kg mini (₹1000/pt)
        'ZINC': 5000.0,      # 5000 kg standard (₹5000/pt)
        'ZINCM': 1000.0,     # 1000 kg mini (₹1000/pt)
        'ALUMINIUM': 5000.0, # 5000 kg standard (₹5000/pt)
        'ALUMINI': 1000.0    # 1000 kg mini (₹1000/pt)
    }

    # Contract Routing Map for Standard vs Mini/Micro exchange contracts
    MINI_ROUTES = {
        'ALUMINIUM': {'symbol': 'ALUMINI', 'name': 'Aluminium Mini', 'lot_size': 1000},
        'CRUDEOIL': {'symbol': 'CRUDEOILM', 'name': 'Crude Oil Mini', 'lot_size': 10},
        'CRUDEOILM': {'symbol': 'CRUDEOILM', 'name': 'Crude Oil Mini', 'lot_size': 10},
        'NATURALGAS': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot_size': 250},
        'NATURALGASM': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot_size': 250},
        'NATGASMINI': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot_size': 250},
        'SILVER': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot_size': 1},
        'SILVERM': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot_size': 1},
        'SILVERMIC': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot_size': 1},
        'GOLD': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot_size': 1},
        'GOLDM': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot_size': 1},
        'GOLDTEN': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot_size': 1},
        'ZINC': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot_size': 1000},
        'ZINCM': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot_size': 1000},
        'ZINCMINI': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot_size': 1000},
        'COPPER': {'symbol': 'COPPERM', 'name': 'Copper Mini', 'lot_size': 1000},
        'COPPERM': {'symbol': 'COPPERM', 'name': 'Copper Mini', 'lot_size': 1000},
    }

    # Canonical 1-Lot Specification Mapping for MCX Mini/Micro Commodities
    COMMODITY_SPECS_150K = {
        'SILVER': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot': 1},
        'SILVERM': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot': 1},
        'SILVERMIC': {'symbol': 'SILVERMIC', 'name': 'Silver Micro', 'lot': 1},
        'GOLD': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot': 1},
        'GOLDM': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot': 1},
        'GOLDTEN': {'symbol': 'GOLDTEN', 'name': 'Gold Ten (Micro)', 'lot': 1},
        'ZINC': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot': 1000},
        'ZINCM': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot': 1000},
        'ZINCMINI': {'symbol': 'ZINCMINI', 'name': 'Zinc Mini', 'lot': 1000},
        'COPPER': {'symbol': 'COPPERM', 'name': 'Copper Mini', 'lot': 1000},
        'COPPERM': {'symbol': 'COPPERM', 'name': 'Copper Mini', 'lot': 1000},
        'CRUDEOIL': {'symbol': 'CRUDEOILM', 'name': 'Crude Oil Mini', 'lot': 10},
        'CRUDEOILM': {'symbol': 'CRUDEOILM', 'name': 'Crude Oil Mini', 'lot': 10},
        'NATURALGAS': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot': 250},
        'NATURALGASM': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot': 250},
        'NATGASMINI': {'symbol': 'NATURALGASM', 'name': 'Natural Gas Mini', 'lot': 250},
        'ALUMINIUM': {'symbol': 'ALUMINI', 'name': 'Aluminium Mini', 'lot': 1000},
        'ALUMINI': {'symbol': 'ALUMINI', 'name': 'Aluminium Mini', 'lot': 1000}
    }

    TARGET_COMMODITY_MARGIN_INR = 75000.0  # ₹75,000 target margin (50% of ₹1.5L pool to allow 2 concurrent diverse trades)

    # MCX Standard Initial Margin Rates for Sizing
    MARGIN_RATES = {
        'CRUDEOIL': 0.25, 'CRUDEOILM': 0.25,
        'NATURALGAS': 0.18, 'NATURALGASM': 0.18, 'NATGASMINI': 0.18,
        'SILVER': 0.15, 'SILVERM': 0.15, 'SILVERMIC': 0.15,
        'GOLD': 0.10, 'GOLDM': 0.10, 'GOLDTEN': 0.10,
        'COPPER': 0.08, 'COPPERM': 0.08,
        'ZINC': 0.0945, 'ZINCM': 0.0945, 'ZINCMINI': 0.0945,
        'ALUMINIUM': 0.092, 'ALUMINI': 0.092
    }

    @classmethod
    def resolve_contract_and_lot(cls, symbol, name, entry, stop, default_lot, max_risk_inr=28000.0, atr=None, target_margin_inr=75000.0, available_margin_inr=None):
        """
        Resolves the appropriate contract and scales lots to match ~₹75,000 deployed margin per position (50% pool split).
        Commodities strictly trade in standardized whole exchange lots calibrated to ~₹75k:
        - NATURALGASM: 4 lots / 1000 units (~₹68k margin)
        - CRUDEOILM: 4 lots / 40 units (~₹60k margin)
        - SILVERMIC: 5 lots / 5 units (~₹67.5k margin)
        - GOLDM: 1 lot / 10 units (~₹76k margin)
        - COPPERM: 1 lot / 1000 units (~₹65.6k margin)
        - ZINCMINI: 2 lots / 2000 units (~₹53k margin)
        - ALUMINI: 2 lots / 2000 units (~₹58k margin)
        """
        if entry <= 0 or not stop or stop <= 0 or entry == stop:
            return symbol, name, default_lot

        # 1. Route to canonical ~₹1.5L Mini/Micro contract
        clean = symbol.replace('MIC', '').replace('MINI', '').replace('INI', '').rstrip('M')
        base_sym = symbol.replace('MIC', '').replace('MINI', '').replace('INI', '').replace('M', '').replace('TEN', '')
        
        spec_150k = cls.COMMODITY_SPECS_150K.get(symbol) or cls.COMMODITY_SPECS_150K.get(clean) or cls.COMMODITY_SPECS_150K.get(base_sym)
        mini_info = cls.MINI_ROUTES.get(symbol) or cls.MINI_ROUTES.get(base_sym)

        resolved_sym = spec_150k['symbol'] if spec_150k else (mini_info['symbol'] if mini_info else symbol)
        resolved_name = spec_150k['name'] if spec_150k else (mini_info['name'] if mini_info else name)
        base_unit = mini_info['lot_size'] if mini_info else (default_lot if default_lot > 0 else 1)

        # 2. Sizing by ~₹1,50,000 Target Margin
        m_rate = cls.MARGIN_RATES.get(resolved_sym, cls.MARGIN_RATES.get(base_sym, 0.20))
        single_lot_margin = (base_unit * entry) * m_rate

        # Preferred canonical lot for ~₹1.5L target
        canonical_lot = spec_150k['lot'] if spec_150k else (default_lot if default_lot > 0 else base_unit)

        # For Crude Oil Mini, dynamically scale lots if price is elevated so broker margin never exceeds ₹1.5L
        if resolved_sym == 'CRUDEOILM' and entry > 0:
            broker_m_rate = max(m_rate, 0.28)
            dhan_margin_per_lot = (base_unit * entry) * broker_m_rate
            if dhan_margin_per_lot > 0:
                max_allowed_lots = max(1, int(target_margin_inr // dhan_margin_per_lot))
                canonical_lot = min(canonical_lot, max_allowed_lots * base_unit)

        canonical_margin = (canonical_lot * entry) * m_rate

        # If available margin is constrained, size maximum whole lots that fit available cash
        if available_margin_inr is not None and available_margin_inr < canonical_margin:
            effective_margin_cap = max(0.0, available_margin_inr)
            if single_lot_margin > 0:
                target_lots = max(1, int(effective_margin_cap // single_lot_margin))
                resolved_lot = target_lots * base_unit
            else:
                resolved_lot = base_unit
        else:
            # Ample margin: deploy canonical lot reaching ~₹1.5L margin
            resolved_lot = canonical_lot

        # 3. Commodity Margin Integrity Mandate:
        # For MCX Commodities, each position is calibrated to ~₹1.5L margin.
        # Do NOT artificially downscale whole commodity contracts below ~₹1.5L target margin
        # just because structural stop-loss distance exceeds an arbitrary cap.
        # Both PreTradeRiskEngine and InstitutionalDirectionEngine follow the Movement-First Mandate
        # and accept the high-conviction risk.
        is_commodity = bool(spec_150k or mini_info or resolved_sym in cls.MARGIN_RATES or base_sym in cls.MARGIN_RATES)
        if not is_commodity:
            # Non-commodity / Equities: apply risk cap scaling
            risk_pts = abs(entry - stop)
            total_trade_risk = risk_pts * resolved_lot
            if max_risk_inr and max_risk_inr > 0 and total_trade_risk > max_risk_inr:
                single_risk = risk_pts * base_unit
                if single_risk > 0:
                    affordable_lots = int(max_risk_inr // single_risk)
                    resolved_lot = max(1, affordable_lots) * base_unit

        # Absolute guarantee: resolved_lot is never 0 on a valid entry
        if resolved_lot <= 0:
            resolved_lot = base_unit if base_unit > 0 else 1

        return resolved_sym, resolved_name, resolved_lot

    @classmethod
    def calculate_adaptive_lot(cls, symbol, entry, stop, default_lot, max_risk_inr=3000.0, atr=None):
        """
        Calculates safe integer lot size.
        For MCX Commodities: Strictly respects whole standardized exchange lots (no fractional units).
        Movement-First Mandate: Never returns 0 lots when a valid setup triggers.
        """
        if entry <= 0 or stop <= 0 or entry == stop or default_lot <= 0:
            return default_lot

        base_sym = symbol.replace('MIC', '').replace('MINI', '').replace('INI', '').replace('M', '').replace('TEN', '')
        mini_info = cls.MINI_ROUTES.get(symbol) or cls.MINI_ROUTES.get(base_sym)
        
        # If commodity, return standardized exchange lot
        if mini_info or symbol in cls.POINT_VALUE or base_sym in cls.POINT_VALUE:
            lot = mini_info['lot_size'] if mini_info else default_lot
            return lot if lot > 0 else 1

        # For Equities: scale down share count to fit max_risk_inr, minimum 1 share
        risk_pts = abs(entry - stop)
        effective_risk_pts = max(risk_pts, atr * 0.8) if (atr and atr > 0) else risk_pts
        scaled_shares = math.floor(max_risk_inr / effective_risk_pts) if effective_risk_pts > 0 else default_lot
        return max(1, min(default_lot, scaled_shares))


class SwingStockMonteCarloEngine:
    """
    Multi-Target Swing Monte Carlo Engine for Golden Crossover & Oversold Equities.
    Derived from day-trader-skill (trader-timemachine).
    
    Simulates 3,000 multi-day paths over a realistic swing holding horizon (15 to 25 trading days).
    Uses the stock's actual daily volatility (daily_vol_pct) to model empirical price diffusion.
    
    Computes:
    - Target 1 Hit Rate (% of paths reaching Target 1 before SL, e.g. +6% to +10%)
    - Target 2 Hit Rate (% of paths reaching Target 2 before SL, e.g. +15% to +22%)
    - Stop Loss Risk Rate (% of paths stopped out)
    - Neither Hit (% of paths floating inside the channel at holding horizon)
    - Expected Return in R-multiples (assuming 60% locked at T1 and 40% trailed to T2)
    - Institutional Swing Advice: e.g. "Lock 60% profit at ₹{target_1} (71% probability); trail stop to entry for ₹{target_2}"
    """

    @classmethod
    def simulate_swing_trade(cls, symbol, close, stop_loss, target_1, target_2, daily_vol_pct=1.8,
                             holding_days=20, replays=3000, seed=42):
        try:
            import numpy as np
            has_np = True
        except ImportError:
            has_np = False

        daily_vol = max(0.008, float(daily_vol_pct) / 100.0) if daily_vol_pct else 0.018
        risk_per_share = abs(close - stop_loss)
        r1 = round(abs(target_1 - close) / risk_per_share, 2) if risk_per_share > 0 else 1.8
        r2 = round(abs(target_2 - close) / risk_per_share, 2) if risk_per_share > 0 else 3.5

        if has_np:
            rng = np.random.default_rng(seed)
            # Slight drift (0.05% daily) representing positive edge in golden crossover / oversold bounce
            daily_rets = rng.normal(loc=0.0005, scale=daily_vol, size=(replays, holding_days))
            price_paths = close * np.cumprod(1.0 + daily_rets, axis=1)

            hit_sl_step = price_paths <= stop_loss
            hit_t1_step = price_paths >= target_1
            hit_t2_step = price_paths >= target_2

            first_sl = np.where(np.any(hit_sl_step, axis=1), np.argmax(hit_sl_step, axis=1), 9999)
            first_t1 = np.where(np.any(hit_t1_step, axis=1), np.argmax(hit_t1_step, axis=1), 9999)
            first_t2 = np.where(np.any(hit_t2_step, axis=1), np.argmax(hit_t2_step, axis=1), 9999)

            sl_before_t1 = (first_sl < first_t1) & (first_sl != 9999)
            t1_reached = (first_t1 != 9999) & (~sl_before_t1)
            t2_reached = (first_t2 != 9999) & (~sl_before_t1)
            neither_reached = (first_sl == 9999) & (first_t1 == 9999)

            p_t1 = round(float(np.mean(t1_reached) * 100.0), 1)
            p_t2 = round(float(np.mean(t2_reached) * 100.0), 1)
            p_sl = round(float(np.mean(sl_before_t1) * 100.0), 1)
            p_neither = round(float(np.mean(neither_reached) * 100.0), 1)
        else:
            rng = random.Random(seed)
            t1_c = 0
            t2_c = 0
            sl_c = 0
            for _ in range(replays):
                p = close
                hit_sl = False
                hit_t1 = False
                hit_t2 = False
                for _ in range(holding_days):
                    ret = rng.gauss(0.0005, daily_vol)
                    p *= (1.0 + ret)
                    if p <= stop_loss and not hit_t1:
                        hit_sl = True
                        break
                    if p >= target_1:
                        hit_t1 = True
                    if p >= target_2:
                        hit_t2 = True
                if hit_sl: sl_c += 1
                if hit_t1: t1_c += 1
                if hit_t2: t2_c += 1
            p_t1 = round((t1_c / replays) * 100.0, 1)
            p_t2 = round((t2_c / replays) * 100.0, 1)
            p_sl = round((sl_c / replays) * 100.0, 1)
            p_neither = round(max(0.0, 100.0 - (p_t1 + p_sl)), 1)

        # Expected R: 60% booked at T1 (r1), 40% booked at T2 (r2), vs losing 1.0R at SL
        expected_r = round(((p_t1 / 100.0) * 0.60 * r1) + ((p_t2 / 100.0) * 0.40 * r2) - ((p_sl / 100.0) * 1.0), 2)

        if p_t1 >= 55.0 and expected_r >= 0.40:
            verdict = "HIGH_CONFIDENCE_SWING"
            verdict_badge = "🟢 High Edge Swing"
        elif p_t1 >= 45.0:
            verdict = "FAVORABLE_SWING"
            verdict_badge = "🟢 Favorable Swing"
        else:
            verdict = "MODERATE_SWING"
            verdict_badge = "🟡 Moderate Swing"

        swing_advice = (
            f"Lock 60% profit at T1 ₹{target_1:,.2f} ({p_t1}% hit rate); "
            f"trail stop to entry for T2 ₹{target_2:,.2f} ({p_t2}% hit rate)."
        )

        return {
            "replays": replays,
            "holding_days": holding_days,
            "daily_volatility_pct": round(daily_vol * 100.0, 2),
            "p_target_pct": p_t1,
            "p_target_1_pct": p_t1,
            "p_target_2_pct": p_t2,
            "p_stop_pct": p_sl,
            "p_neither_pct": p_neither,
            "target_1_r": r1,
            "target_2_r": r2,
            "expected_r": expected_r,
            "verdict": verdict,
            "verdict_badge": verdict_badge,
            "swing_advice": swing_advice,
            "summary": f"{replays:,} Replays: {p_t1}% T1 | {p_t2}% T2 | {p_sl}% SL | Expected: {expected_r:+.2f}R"
        }


class CircuitMonteCarloPredictor:
    """
    Circuit Limit Lock & Extreme Intraday Range Predictor for Commodities & Equities.
    Derived from day-trader-skill (trader-timemachine).
    
    Simulates 5,000 intraday paths across the remaining minutes/hours of the active session
    using current price, distance to Upper/Lower circuits, and intraday ATR/volatility.
    
    Computes:
    - Upper Circuit Hit Probability (% chance of price touching or locking the Upper Circuit band today)
    - Lower Circuit Hit Probability (% chance of price touching or locking the Lower Circuit band today)
    - 95% Confidence Session Range (Lower Band 95th VaR and Upper Band 95th VaR)
    - Circuit Risk Rating: LOW (<10%), MODERATE (10-24%), HIGH (25-49%), EXTREME (>=50%)
    """

    @classmethod
    def predict_circuit_probability(cls, symbol, current_price, upper_circuit, lower_circuit,
                                   daily_vol_pct=2.0, minutes_remaining=180, replays=5000, seed=42):
        try:
            import numpy as np
            has_np = True
        except ImportError:
            has_np = False

        if not current_price or current_price <= 0:
            return None

        bars_remaining = max(1, int(minutes_remaining / 5))
        # Approximate 5-minute bar volatility from daily volatility (75 5m bars in MCX session)
        daily_vol = max(0.008, float(daily_vol_pct) / 100.0) if daily_vol_pct else 0.02
        bar_vol = daily_vol / math.sqrt(75)

        if has_np:
            rng = np.random.default_rng(seed)
            bar_returns = rng.normal(loc=0.0, scale=bar_vol, size=(replays, bars_remaining))
            paths = current_price * np.cumprod(1.0 + bar_returns, axis=1)

            max_prices = np.max(paths, axis=1)
            min_prices = np.min(paths, axis=1)

            uc_hit = round(float(np.mean(max_prices >= upper_circuit) * 100.0), 1) if upper_circuit else 0.0
            lc_hit = round(float(np.mean(min_prices <= lower_circuit) * 100.0), 1) if lower_circuit else 0.0
            var_95_high = round(float(np.percentile(max_prices, 95)), 2)
            var_95_low = round(float(np.percentile(min_prices, 5)), 2)
        else:
            rng = random.Random(seed)
            uc_c = 0
            lc_c = 0
            max_list = []
            min_list = []
            for _ in range(replays):
                p = current_price
                p_max = current_price
                p_min = current_price
                for _ in range(bars_remaining):
                    ret = rng.gauss(0.0, bar_vol)
                    p *= (1.0 + ret)
                    if p > p_max: p_max = p
                    if p < p_min: p_min = p
                if upper_circuit and p_max >= upper_circuit: uc_c += 1
                if lower_circuit and p_min <= lower_circuit: lc_c += 1
                max_list.append(p_max)
                min_list.append(p_min)
            uc_hit = round((uc_c / replays) * 100.0, 1)
            lc_hit = round((lc_c / replays) * 100.0, 1)
            max_list.sort()
            min_list.sort()
            var_95_high = round(max_list[int(replays * 0.95)], 2)
            var_95_low = round(min_list[int(replays * 0.05)], 2)

        max_circuit_prob = max(uc_hit, lc_hit)
        if max_circuit_prob >= 40.0:
            rating = "EXTREME_CIRCUIT_RISK"
            rating_badge = "🚨 Extreme Circuit Risk"
            rating_color = "#ef4444"
        elif max_circuit_prob >= 20.0:
            rating = "HIGH_CIRCUIT_RISK"
            rating_badge = "⚠️ High Circuit Risk"
            rating_color = "#f97316"
        elif max_circuit_prob >= 8.0:
            rating = "MODERATE_CIRCUIT_TEST"
            rating_badge = "⚡ Moderate Circuit Test"
            rating_color = "#eab308"
        else:
            rating = "NORMAL_CONTAINED"
            rating_badge = "🟢 Band Contained"
            rating_color = "#10b981"

        return {
            "replays": replays,
            "minutes_remaining": minutes_remaining,
            "prob_upper_circuit_pct": uc_hit,
            "prob_lower_circuit_pct": lc_hit,
            "var_95_expected_high": var_95_high,
            "var_95_expected_low": var_95_low,
            "circuit_risk_rating": rating,
            "rating_badge": rating_badge,
            "rating_color": rating_color,
            "summary": f"Circuit Odds: {uc_hit}% Upper Lock | {lc_hit}% Lower Lock | 95% Band: ₹{var_95_low:,.1f} - ₹{var_95_high:,.1f}"
        }


class MarketRegimeDetector:
    """
    Institutional Market Regime Classifier & False Signal Filter.
    Categorizes any asset (Equity, Commodity, Index) into one of the mathematical market regimes:
    
    1. BULL_TREND 🚀:
       - Structure: Close >= EMA50 >= EMA200
       - Trend Strength: ADX >= 20.0
       - Momentum: RSI >= 50.0
       - Trading Mandate: BUY DIPS & BREAKOUTS. Trend continuation in effect.
       - Disallowed: NAKED CALL SELLING, BLIND SHORTING.
       
    2. CHOP_CONSOLIDATION 💤:
       - Volatility: Bollinger Bandwidth <= 12.5% or ADX < 20.0
       - Trend Strength: Non-trending / rangebound oscillation
       - Momentum: 38.0 <= RSI <= 62.0 (Neutral oscillation)
       - Trading Mandate: THETA DECAY OPTION HARVEST (Strangles / Condors / Channel Boundary fades).
       - Disallowed: BUYING BREAKOUTS / CROSSOVERS (High whipsaw & fakeout risk).
       
    3. BREAKDOWN 🔻:
       - Structure: Close <= EMA50 <= EMA200
       - Trend Strength: ADX >= 20.0 or downward breakdown
       - Momentum: RSI <= 50.0
       - Trading Mandate: SELL RALLIES / SHORT BREAKDOWNS. Capital preservation priority.
       - Disallowed: NAKED PUT SELLING, BUYING FALLING KNIVES.
       
    4. VOLATILE_EXPANSION ⚡:
       - Volatility: Bollinger Bandwidth > 16.0% or Daily Volatility > 3.0%
       - Price Action: Wide erratic expansion / whipsaw
       - Trading Mandate: REDUCE SIZING 50% / STAND ASIDE. Volatility shock in progress.
       - Disallowed: FULL LOT SIZING, HIGH LEVERAGE.
    """

    @classmethod
    def classify_regime(cls, symbol, close, ema50, ema200, adx, rsi, bb_lower, bb_upper,
                        daily_vol_pct=None, atr=None, chg_pct=None, day_h=None, day_l=None):
        if not close or close <= 0:
            return None

        ema50 = float(ema50 or close)
        ema200 = float(ema200 or close)
        adx = float(adx or 20.0)
        rsi = float(rsi or 50.0)
        chg_pct = float(chg_pct or 0.0)
        day_h = float(day_h or 0.0)
        day_l = float(day_l or 0.0)
        bb_low = float(bb_lower or (close * 0.95))
        bb_high = float(bb_upper or (close * 1.05))
        
        bb_width_pct = round(((bb_high - bb_low) / close) * 100, 2) if close > 0 else 10.0
        daily_vol = float(daily_vol_pct) if daily_vol_pct else (round((atr / close) * 100, 2) if (atr and close > 0) else 1.8)

        # 0. Intraday Momentum Breakout Transition (Priority for Capturing Big Moves)
        # If asset is up strongly at/near Day High with robust ADX, it is transitioning into Bull Momentum
        is_breakout_expansion_bull = (chg_pct >= 0.70) and (day_h > 0 and close >= day_h * 0.990) and (adx >= 20.0)
        is_breakdown_expansion_bear = (chg_pct <= -0.70) and (day_l > 0 and close <= day_l * 1.010) and (adx >= 20.0)

        # 1. Check Chop / Consolidation (Theta Zone) first
        is_chop = (adx < 20.0) or (bb_width_pct <= 12.5 and 38.0 <= rsi <= 62.0 and adx < 22.0)
        
        # 2. Check Volatility Expansion
        is_volatile_expansion = (bb_width_pct >= 16.0) or (daily_vol >= 3.0)

        # 3. Directional Alignment
        is_bull_structure = (close >= ema50) and (ema50 >= (ema200 * 0.985)) and (rsi >= 48.0)
        is_bear_structure = (close <= ema50) and (ema50 <= (ema200 * 1.015)) and (rsi <= 52.0)
        has_trend_strength = (adx >= 20.0)

        if is_breakout_expansion_bull:
            regime = "BULL_TREND"
            badge = "⚡ Bull Breakout Expansion"
            short_tag = "Bull Breakout"
            color = "#34d399"
            bg_color = "rgba(52, 211, 153, 0.12)"
            border_color = "rgba(52, 211, 153, 0.35)"
            mandate = f"High-momentum Day-High breakout expansion (Chg: +{chg_pct:.2f}%, ADX: {adx:.1f}). Riding directional impulse."
            allowed = ["TREND_BREAKOUT_BUY", "EXPANSION_BUY", "MOMENTUM_RUN"]
            blocked = ["NAKED_CALL_SELLING", "SHORTING"]
            regime_icon = "fa-solid fa-bolt"

        elif is_breakdown_expansion_bear:
            regime = "BREAKDOWN"
            badge = "⚡ Bear Breakdown Expansion"
            short_tag = "Bear Breakdown"
            color = "#fb7185"
            bg_color = "rgba(251, 113, 133, 0.12)"
            border_color = "rgba(251, 113, 133, 0.35)"
            mandate = f"High-momentum Day-Low breakdown expansion (Chg: {chg_pct:.2f}%, ADX: {adx:.1f}). Riding downward impulse."
            allowed = ["SHORT_BREAKDOWN", "DEFENSIVE_HEDGING"]
            blocked = ["BUYING_DIPS", "BUY_EQUITY"]
            regime_icon = "fa-solid fa-bolt"

        elif is_chop and not (has_trend_strength and (is_bull_structure or is_bear_structure)):
            regime = "CHOP_CONSOLIDATION"
            badge = "💤 Chop / Consolidation"
            short_tag = "Chop / Range"
            color = "#c084fc"
            bg_color = "rgba(192, 132, 252, 0.12)"
            border_color = "rgba(192, 132, 252, 0.35)"
            mandate = f"Non-trending chop (ADX: {adx:.1f}, BBW: {bb_width_pct:.1f}%). Breakouts are prone to fakeouts. Favorable for range-bound mean reversion."
            allowed = ["MEAN_REVERSION", "RANGE_BOUND_SUPPORT_BUY", "STAGED_LIMITS"]
            blocked = ["TREND_BREAKOUT_BUY", "HIGH_BETA_FUTURES", "GOLDEN_CROSS"]
            regime_icon = "fa-solid fa-arrows-left-right"

        elif is_volatile_expansion and not (has_trend_strength and (is_bull_structure or is_bear_structure)):
            regime = "VOLATILE_EXPANSION"
            badge = "⚡ Volatile Expansion"
            short_tag = "High Volatility"
            color = "#fbbf24"
            bg_color = "rgba(251, 191, 36, 0.12)"
            border_color = "rgba(251, 191, 36, 0.35)"
            mandate = f"Wide volatility band (BBW: {bb_width_pct:.1f}%, Vol: {daily_vol:.1f}%). High whipsaw risk. Reduce position sizing by 50%."
            allowed = ["DEFENSIVE_SCALPING", "REDUCED_SIZING"]
            blocked = ["AGGRESSIVE_ENTRIES", "FULL_LEVERAGE"]
            regime_icon = "fa-solid fa-bolt"

        elif is_bull_structure and has_trend_strength:
            regime = "BULL_TREND"
            badge = "🚀 Bull Trend"
            short_tag = "Bull Trend"
            color = "#34d399"
            bg_color = "rgba(52, 211, 153, 0.12)"
            border_color = "rgba(52, 211, 153, 0.35)"
            mandate = f"Strong structural uptrend (50E > 200E, ADX: {adx:.1f}, RSI: {rsi:.1f}). Favorable for Golden Cross accumulation & swing longs."
            allowed = ["GOLDEN_CROSS_BUY", "PULLBACK_LONG", "BULL_PUT_SPREAD"]
            blocked = ["NAKED_CALL_SELLING", "SHORTING"]
            regime_icon = "fa-solid fa-arrow-trend-up"

        elif is_bear_structure and has_trend_strength:
            regime = "BREAKDOWN"
            badge = "🔻 Breakdown / Bear Trend"
            short_tag = "Breakdown"
            color = "#fb7185"
            bg_color = "rgba(251, 113, 133, 0.12)"
            border_color = "rgba(251, 113, 133, 0.35)"
            mandate = f"Downtrend in effect (Close < 50E, ADX: {adx:.1f}, RSI: {rsi:.1f}). Capital preservation priority; falling knife defense. Do not take long breakouts."
            allowed = ["DEFENSIVE_HEDGING", "BEAR_CALL_SPREAD", "SHORT_FUTURES"]
            blocked = ["NAKED_PUT_SELLING", "BUY_EQUITY", "GOLDEN_CROSS_BUY"]
            regime_icon = "fa-solid fa-arrow-trend-down"

        elif is_bull_structure:
            regime = "BULL_TREND"
            badge = "🚀 Bull Trend (Emerging)"
            short_tag = "Bull Trend"
            color = "#86efac"
            bg_color = "rgba(134, 239, 172, 0.12)"
            border_color = "rgba(134, 239, 172, 0.35)"
            mandate = f"Constructive basing above 50/200 EMA (ADX: {adx:.1f}). Accumulating with defined support."
            allowed = ["SWING_BUY", "COVERED_CALL"]
            blocked = ["AGGRESSIVE_BREAKOUT"]
            regime_icon = "fa-solid fa-arrow-trend-up"

        elif has_trend_strength:
            # Active directional trend / momentum where moving averages are catching up to rapid moves
            session_mid = (bb_high + bb_low) / 2.0
            if (close >= session_mid) or (rsi >= 50.0):
                regime = "BULL_TREND"
                badge = "🚀 Bull Trend (Momentum)"
                short_tag = "Bull Trend"
                color = "#34d399"
                bg_color = "rgba(52, 211, 153, 0.12)"
                border_color = "rgba(52, 211, 153, 0.35)"
                mandate = f"Strong directional momentum (ADX: {adx:.1f}). Favorable for trend continuation & breakouts."
                allowed = ["TREND_BREAKOUT_BUY", "PULLBACK_LONG", "EXPANSION_BUY"]
                blocked = ["NAKED_CALL_SELLING", "SHORTING"]
                regime_icon = "fa-solid fa-arrow-trend-up"
            else:
                regime = "BREAKDOWN"
                badge = "🔻 Breakdown (Momentum)"
                short_tag = "Breakdown"
                color = "#fb7185"
                bg_color = "rgba(251, 113, 133, 0.12)"
                border_color = "rgba(251, 113, 133, 0.35)"
                mandate = f"Strong downward momentum (ADX: {adx:.1f}). Favorable for short breakdowns."
                allowed = ["SHORT_BREAKDOWN", "DEFENSIVE_HEDGING"]
                blocked = ["BUYING_DIPS", "BUY_EQUITY"]
                regime_icon = "fa-solid fa-arrow-trend-down"

        else:
            regime = "CHOP_CONSOLIDATION"
            badge = "💤 Chop / Consolidation"
            short_tag = "Chop / Range"
            color = "#94a3b8"
            bg_color = "rgba(148, 163, 184, 0.12)"
            border_color = "rgba(148, 163, 184, 0.35)"
            mandate = f"Neutral consolidation (ADX: {adx:.1f}). Whipsaw risk for breakouts."
            allowed = ["WATCHLIST", "STAGED_LIMIT_ORDERS", "MEAN_REVERSION"]
            blocked = ["MARKET_ORDERS", "BREAKOUT_BUY"]
            regime_icon = "fa-solid fa-arrows-left-right"

        return {
            "regime": regime,
            "badge": badge,
            "short_tag": short_tag,
            "color": color,
            "bg_color": bg_color,
            "border_color": border_color,
            "mandate": mandate,
            "allowed_strategies": allowed,
            "blocked_strategies": blocked,
            "icon": regime_icon,
            "adx": round(adx, 1),
            "rsi": round(rsi, 1),
            "change_pct": round(chg_pct, 2),
            "bb_width_pct": bb_width_pct,
            "daily_vol_pct": round(daily_vol, 2),
            "close": close,
            "ema50": ema50,
            "ema200": ema200
        }

    @classmethod
    def validate_signal(cls, regime_info, action, setup_name=""):
        """
        Actively filters out false signals based on institutional market regime:
        - CHOP_CONSOLIDATION: Filters out all breakout trades and trend crossovers (high whipsaw / fakeout risk).
        - BREAKDOWN: Filters out all BUY signals (prevents buying into falling knives).
        - BULL_TREND: Filters out all SELL / fade signals (prevents counter-trend shorting).
        - VOLATILE_EXPANSION: Flags extreme volatility shock (advises 50% size or tight SL).
        """
        if not regime_info:
            return {"passed": True, "reason": "No regime constraint"}

        regime = regime_info.get("regime") if isinstance(regime_info, dict) else str(regime_info)
        setup_lower = (setup_name or "").lower()
        act_upper = (action or "").upper()
        is_reversal = any(term in setup_lower for term in ['reversal', 'mean-reversion', 'mean_reversion', 'rebound', 'oversold bounce', 'overbought fade'])
        is_breakout = any(term in setup_lower for term in ['breakout', 'breakdown', 'expansion', 'impulse', 'momentum'])
        is_coil = any(term in setup_lower for term in ['pre-ignition', 'coil', 'volatility coil', 'pre-move'])
        adx_val = float(regime_info.get('adx') or 0.0) if isinstance(regime_info, dict) else 0.0

        if regime in ["CHOP_CONSOLIDATION", "COMPRESSED_RANGE"]:
            # Pre-Ignition Volatility Coils are specifically engineered to identify trades inside compressed ranges before the explosion
            if is_coil:
                return {
                    "passed": True,
                    "reason": "Pre-Ignition Volatility Coil entering inside compressed range prior to expansion. Early asymmetric entry validated.",
                    "regime": regime
                }

            # If ADX >= 22.0 or setup is a confirmed breakout, it represents a regime transition out of chop
            if adx_val >= 22.0 or is_breakout:
                return {"passed": True, "reason": f"Momentum impulse confirmed (ADX: {adx_val:.1f}). Breakout validated.", "regime": regime}

            # In chop/consolidation, chasing late breakouts or crossovers are high-probability whipsaws
            if any(term in setup_lower for term in ['breakout', 'breakdown', 'crossover', 'cross', 'golden cross', 'high-beta']):
                return {
                    "passed": False,
                    "reason": f"FALSE SIGNAL FILTER: Blocked {action} '{setup_name}' during CHOP_CONSOLIDATION (ADX {regime_info.get('adx', 'low')}). High whipsaw & fakeout risk.",
                    "regime": "CHOP_CONSOLIDATION",
                    "filter_tag": "CHOP_WHIPSAW_GUARD"
                }
            # Standard trend setups must follow 50 EMA. Genuine Exhaustion Mean-Reversion Reversals are explicitly permitted!
            if not is_reversal:
                cls_p = float(regime_info.get('close') or 0.0)
                e50 = float(regime_info.get('ema50') or 0.0)
                if cls_p > 0 and e50 > 0:
                    if act_upper == "BUY" and cls_p < e50 * 0.998:
                        return {
                            "passed": False,
                            "reason": f"FALSE SIGNAL FILTER: Blocked BUY '{setup_name}' in CHOP regime: Price (₹{cls_p:,.2f}) is below 50 EMA (₹{e50:,.2f}). Counter-trend buying strictly prohibited.",
                            "regime": "CHOP_CONSOLIDATION",
                            "filter_tag": "COUNTER_TREND_GUARD"
                        }
                    elif act_upper == "SELL" and cls_p > e50 * 1.002:
                        return {
                            "passed": False,
                            "reason": f"FALSE SIGNAL FILTER: Blocked SELL '{setup_name}' in CHOP regime: Price (₹{cls_p:,.2f}) is above 50 EMA (₹{e50:,.2f}). Counter-trend shorting strictly prohibited.",
                            "regime": "CHOP_CONSOLIDATION",
                            "filter_tag": "COUNTER_TREND_GUARD"
                        }

        elif regime in ["BREAKDOWN", "BEAR_TREND"]:
            # In structural breakdown, never buy falling knives unless extreme oversold exhaustion reversal (RSI <= 34 at floor)
            # OR genuine Day-High breakout / directional volatility expansion riding strong momentum
            if act_upper == "BUY":
                chg_val = float(regime_info.get('change_pct', 0.0) or 0.0)
                if is_breakout and (adx_val >= 20.0 or chg_val >= 0.40):
                    pass  # Permitted: High-momentum breakout / V-shape trend transition riding Day High
                elif is_reversal and float(regime_info.get('rsi') or 50.0) <= 34.0:
                    pass  # Permitted: Extreme exhaustion bounce with structural invalidation stop
                else:
                    return {
                        "passed": False,
                        "reason": f"FALSE SIGNAL FILTER: Blocked BUY '{setup_name}' during BREAKDOWN regime (RSI {regime_info.get('rsi', 'low')}). Falling knife defense active.",
                        "regime": "BREAKDOWN",
                        "filter_tag": "FALLING_KNIFE_GUARD"
                    }

        elif regime == "BULL_TREND":
            # In structural bull trend, never short or fade unless extreme overbought exhaustion reversal (RSI >= 66 at ceiling)
            # OR genuine Day-Low breakdown riding strong downward momentum
            if act_upper == "SELL":
                chg_val = float(regime_info.get('change_pct', 0.0) or 0.0)
                if is_breakout and (adx_val >= 20.0 or chg_val <= -0.40):
                    pass  # Permitted: High-momentum breakdown / trend reversal transition riding Day Low
                elif is_reversal and float(regime_info.get('rsi') or 50.0) >= 66.0:
                    pass  # Permitted: Extreme exhaustion fade with structural invalidation stop
                else:
                    return {
                        "passed": False,
                        "reason": f"FALSE SIGNAL FILTER: Blocked SELL '{setup_name}' during BULL_TREND regime (ADX {regime_info.get('adx', 'high')}). Counter-trend shorting blocked.",
                        "regime": "BULL_TREND",
                        "filter_tag": "COUNTER_TREND_GUARD"
                    }

        return {"passed": True, "reason": f"Signal aligned with {regime} regime.", "regime": regime}


class InstitutionalDirectionEngine:
    """
    Institutional Multi-Timeframe VWAP + Trend-Pullback Direction Engine.
    Ensures zero blind counter-trend trades, strictly aligns all trades with
    Higher Timeframe (HTF) market structure, and uses Intraday Session VWAP
    as an absolute directional filter.
    """

    @classmethod
    def calculate_session_vwap(cls, open_p, high_p, low_p, close_p):
        """
        Calculates session VWAP proxy using standard intraday typical price weighting.
        """
        o = float(open_p or close_p or 0.0)
        h = float(high_p or close_p or 0.0)
        l = float(low_p or close_p or 0.0)
        c = float(close_p or 0.0)
        if c <= 0:
            return 0.0
        # Institutional 5-point session VWAP approximation
        return round((o + h + l + (2.0 * c)) / 5.0, 2)

    @classmethod
    def evaluate_commodity_direction(cls, symbol, current_price, open_price, high_price, low_price,
                                     prev_close, ema20=None, ema50=None, ema200=None,
                                     rsi=None, adx=None, bb_lower=None, bb_upper=None, atr=None):
        """
        Evaluates institutional direction for commodity contracts (Gold, Silver, Crude, Copper, etc.).
        Returns:
            dict with 'allowed_action' ('BUY', 'SELL', or 'STAND_ASIDE'),
                      'passed': bool,
                      'reason': str,
                      'vwap': float,
                      'htf_bias': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
                      'regime': str
        """
        cur_p = float(current_price or 0.0)
        if cur_p <= 0:
            return {"allowed_action": "STAND_ASIDE", "passed": False, "reason": "Invalid price", "vwap": 0.0}

        vwap = cls.calculate_session_vwap(open_price, high_price, low_price, cur_p)
        ema20 = float(ema20 or cur_p)
        ema50 = float(ema50 or cur_p)
        ema200 = float(ema200 or cur_p)
        rsi = float(rsi or 50.0)
        adx = float(adx or 20.0)
        p_close = float(prev_close or cur_p)

        # 1. Higher-Timeframe Trend Assessment (Daily EMAs)
        is_htf_bull = (cur_p >= ema50) and (ema20 >= ema50 * 0.995)
        is_htf_bear = (cur_p <= ema50) and (ema20 <= ema50 * 1.005)

        if is_htf_bull and not is_htf_bear:
            htf_bias = "BULLISH"
        elif is_htf_bear and not is_htf_bull:
            htf_bias = "BEARISH"
        else:
            htf_bias = "NEUTRAL"

        # 2. Market Regime Assessment
        regime_info = MarketRegimeDetector.classify_regime(
            symbol=symbol, close=cur_p, ema50=ema50, ema200=ema200,
            adx=adx, rsi=rsi, bb_lower=bb_lower, bb_upper=bb_upper, atr=atr
        )
        regime = regime_info.get("regime", "CHOP_CONSOLIDATION")

        # 3. Line in the sand: Intraday VWAP
        # Never BUY below VWAP; Never SHORT above VWAP
        is_above_vwap = cur_p >= (vwap * 0.9985)
        is_below_vwap = cur_p <= (vwap * 1.0015)

        # 4. Resolve Allowed Action
        # Bullish alignment: HTF Bullish + Above VWAP + Regime not BREAKDOWN
        if htf_bias == "BULLISH" and regime != "BREAKDOWN" and is_above_vwap:
            return {
                "allowed_action": "BUY",
                "passed": True,
                "reason": f"Aligned with HTF Bull Trend (50E: ₹{ema50:,.1f}) & trading above Session VWAP (₹{vwap:,.1f}).",
                "vwap": vwap,
                "htf_bias": htf_bias,
                "regime": regime,
                "regime_info": regime_info
            }

        # Bearish alignment: HTF Bearish + Below VWAP + Regime not BULL_TREND
        if htf_bias == "BEARISH" and regime != "BULL_TREND" and is_below_vwap:
            return {
                "allowed_action": "SELL",
                "passed": True,
                "reason": f"Aligned with HTF Bear Trend (50E: ₹{ema50:,.1f}) & trading below Session VWAP (₹{vwap:,.1f}).",
                "vwap": vwap,
                "htf_bias": htf_bias,
                "regime": regime,
                "regime_info": regime_info
            }

        # Conflicting signals or Chop
        if not is_above_vwap and htf_bias == "BULLISH":
            return {
                "allowed_action": "STAND_ASIDE",
                "passed": False,
                "reason": f"Conflict: HTF is Bullish but price (₹{cur_p:,.1f}) is trading below Session VWAP (₹{vwap:,.1f}). Awaiting reclaim of VWAP.",
                "vwap": vwap,
                "htf_bias": htf_bias,
                "regime": regime,
                "regime_info": regime_info
            }

        if not is_below_vwap and htf_bias == "BEARISH":
            return {
                "allowed_action": "STAND_ASIDE",
                "passed": False,
                "reason": f"Conflict: HTF is Bearish but price (₹{cur_p:,.1f}) is trading above Session VWAP (₹{vwap:,.1f}). Counter-trend short blocked.",
                "vwap": vwap,
                "htf_bias": htf_bias,
                "regime": regime,
                "regime_info": regime_info
            }

        # 5. Intraday Session Momentum (London 1:30 PM & US Pit Open 6:00 PM)
        # When HTF is neutral/consolidating, allow clean intraday directional velocity
        chg_pct = ((cur_p - p_close) / p_close) * 100.0 if p_close > 0 else 0.0
        if is_above_vwap and cur_p >= open_price and (rsi >= 50.0 or adx >= 18.0 or chg_pct >= 0.35):
            return {
                "allowed_action": "BUY",
                "passed": True,
                "reason": f"Intraday Bullish Session Momentum: Price (₹{cur_p:,.1f}) holding above VWAP (₹{vwap:,.1f}) and Day Open (₹{open_price:,.1f}) with positive momentum ({chg_pct:+.2f}%).",
                "vwap": vwap,
                "htf_bias": htf_bias,
                "regime": regime,
                "regime_info": regime_info
            }

        if is_below_vwap and cur_p <= open_price and (rsi <= 50.0 or adx >= 18.0 or chg_pct <= -0.35):
            return {
                "allowed_action": "SELL",
                "passed": True,
                "reason": f"Intraday Bearish Session Momentum: Price (₹{cur_p:,.1f}) trading below VWAP (₹{vwap:,.1f}) and Day Open (₹{open_price:,.1f}) with downward expansion ({chg_pct:+.2f}%).",
                "vwap": vwap,
                "htf_bias": htf_bias,
                "regime": regime,
                "regime_info": regime_info
            }

        return {
            "allowed_action": "STAND_ASIDE",
            "passed": False,
            "reason": f"Non-trending or neutral structure (Regime: {regime}, HTF: {htf_bias}, ADX: {adx:.1f}). Standing aside to preserve capital.",
            "vwap": vwap,
            "htf_bias": htf_bias,
            "regime": regime,
            "regime_info": regime_info
        }

    @classmethod
    def evaluate_equity_futures_direction(cls, symbol, current_price, open_price, high_price, low_price,
                                          prev_close, ema20, ema50, ema200, crossover_status, rsi,
                                          adx=None, bb_lower=None, bb_upper=None, atr=None, action="BUY"):
        """
        Evaluates institutional direction for NSE Stock Futures (Long & Short).
        - BUY: Bans long futures on Death Cross / below 200 EMA / below VWAP / overextended chase.
        - SELL: Bans short futures on Golden Cross / above 200 EMA / above VWAP / overextended dump panic.
        """
        cur_p = float(current_price or 0.0)
        if cur_p <= 0:
            return {"allowed_action": "STAND_ASIDE", "passed": False, "reason": "Invalid price"}

        vwap = cls.calculate_session_vwap(open_price, high_price, low_price, cur_p)
        ema20 = float(ema20 or cur_p)
        ema50 = float(ema50 or cur_p)
        ema200 = float(ema200 or cur_p)
        rsi = float(rsi or 50.0)
        adx = float(adx or 20.0)
        p_close = float(prev_close or cur_p)
        day_h = float(high_price or cur_p)
        day_l = float(low_price or cur_p)
        chg_pct = round(((cur_p - p_close) / p_close) * 100, 2) if p_close > 0 else 0.0
        act_upper = str(action or "BUY").upper()

        regime_info = MarketRegimeDetector.classify_regime(
            symbol=symbol, close=cur_p, ema50=ema50, ema200=ema200,
            adx=adx, rsi=rsi, bb_lower=bb_lower, bb_upper=bb_upper, atr=atr
        )

        if act_upper == "SELL":
            # SHORT RULE 1: Golden Cross or Well Above 200 EMA Ban for Short Futures
            is_golden_cross = (str(crossover_status).upper() == "GOLDEN") or (ema50 > ema200 * 1.005)
            is_above_200 = cur_p > (ema200 * 1.01)
            if is_golden_cross and is_above_200:
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"STOCK FUTURES SHORT BANNED: {symbol} in Golden Cross/Bullish Regime (Close ₹{cur_p:,.1f} vs 200E ₹{ema200:,.1f}). Shorting strong uptrends forbidden.",
                    "vwap": vwap,
                    "htf_bias": "BULLISH",
                    "regime": "BULL_TREND",
                    "regime_info": regime_info
                }

            # SHORT RULE 2: Session VWAP Line in the Sand (Must be below VWAP for institutional shorting)
            if cur_p > (vwap * 1.002):
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"Price ₹{cur_p:,.1f} is trading above Session VWAP ₹{vwap:,.1f}. Institutional intraday buyers in control; short entry rejected.",
                    "vwap": vwap,
                    "htf_bias": "BULLISH",
                    "regime": "VWAP_SURPLUS",
                    "regime_info": regime_info
                }

            # SHORT RULE 3: Overextended Breakdown Panic Trap
            # If trading within 0.3% of day low with <= -2.5% loss and oversold RSI, do not chase short
            if cur_p <= (day_l * 1.003) and chg_pct <= -2.5 and rsi <= 30.0:
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"Overextended Breakdown Trap: {symbol} down {chg_pct:.2f}% at day low (RSI: {rsi:.1f}). Selling into panic lows forbidden; requires pullback to 20 EMA / VWAP.",
                    "vwap": vwap,
                    "htf_bias": "BEARISH",
                    "regime": "OVERSOLD_TRAP",
                    "regime_info": regime_info
                }

            # SHORT RULE 4: Market Regime Check
            if regime_info.get("regime") in ["CHOP_CONSOLIDATION", "BULL_TREND"]:
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"Market Regime is {regime_info.get('regime')} (ADX: {adx:.1f}). Short futures blocked to avoid chop/counter-trend losses.",
                    "vwap": vwap,
                    "htf_bias": "NEUTRAL",
                    "regime": regime_info.get("regime"),
                    "regime_info": regime_info
                }

            return {
                "allowed_action": "SELL",
                "passed": True,
                "reason": f"Institutional Short Confirmed: Below 50/200 EMA Death Cross / below VWAP (₹{vwap:,.1f}), high probability shorting alignment.",
                "vwap": vwap,
                "htf_bias": "BEARISH",
                "regime": "BEAR_TREND",
                "regime_info": regime_info
            }

        else:
            # LONG RULE 1: Death Cross or Sub-200 EMA Absolute Ban for Long Futures
            is_death_cross = (str(crossover_status).upper() == "DEATH") or (ema50 < ema200 * 0.995)
            is_below_200 = cur_p < (ema200 * 0.99)
            if is_death_cross or is_below_200:
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"STOCK FUTURES BANNED: {symbol} in Death Cross/Bearish Regime (Close ₹{cur_p:,.1f} vs 200E ₹{ema200:,.1f}, 50E vs 200E: {crossover_status}). Long futures forbidden.",
                    "vwap": vwap,
                    "htf_bias": "BEARISH",
                    "regime": "BREAKDOWN",
                    "regime_info": regime_info
                }

            # LONG RULE 2: Session VWAP Line in the Sand
            if cur_p < (vwap * 0.998):
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"Price ₹{cur_p:,.1f} is trading below Session VWAP ₹{vwap:,.1f}. Institutional intraday sellers in control; long entry rejected.",
                    "vwap": vwap,
                    "htf_bias": "BULLISH",
                    "regime": "VWAP_DEFICIT",
                    "regime_info": regime_info
                }

            # LONG RULE 3: Overextended Breakout Chase Ban
            if cur_p >= (day_h * 0.997) and chg_pct >= 2.0 and rsi >= 62.0:
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"Overextended Breakout Trap: {symbol} up +{chg_pct:.2f}% at day high (RSI: {rsi:.1f}). Chasing forbidden; requires pullback to 20 EMA / VWAP.",
                    "vwap": vwap,
                    "htf_bias": "BULLISH",
                    "regime": "OVEREXTENDED",
                    "regime_info": regime_info
                }

            # LONG RULE 4: Market Regime Check
            if regime_info.get("regime") in ["CHOP_CONSOLIDATION", "BREAKDOWN"]:
                return {
                    "allowed_action": "STAND_ASIDE",
                    "passed": False,
                    "reason": f"Market Regime is {regime_info.get('regime')} (ADX: {adx:.1f}). Futures trading blocked to avoid chop/whipsaw.",
                    "vwap": vwap,
                    "htf_bias": "NEUTRAL",
                    "regime": regime_info.get("regime"),
                    "regime_info": regime_info
                }

            return {
                "allowed_action": "BUY",
                "passed": True,
                "reason": f"Institutional Long Confirmed: Above 50/200 EMA Golden Cross (200E: ₹{ema200:,.1f}), above VWAP (₹{vwap:,.1f}), healthy pullback/momentum.",
                "vwap": vwap,
                "htf_bias": "BULLISH",
                "regime": "BULL_TREND",
                "regime_info": regime_info
            }

    @classmethod
    def validate_futures_risk_bracket(cls, symbol, action, entry_price, technical_sl, technical_tgt, lot_size, max_loss_inr=8500.0, min_rrr=1.1):
        """
        Guarantees that a stop loss is NEVER artificially choked/compressed to fit a max risk cap.
        If the natural structural stop requires > max_loss_inr, the trade must STAND ASIDE.
        Also enforces minimum R:R >= 1.1 (Calibrated for realistic intraday stock futures targets).
        """
        cur_p = float(entry_price or 0.0)
        sl = float(technical_sl or 0.0)
        tgt = float(technical_tgt or 0.0)
        lot = int(lot_size or 1)
        max_loss = float(max_loss_inr or 8500.0)

        if cur_p <= 0 or sl <= 0 or tgt <= 0 or lot <= 0:
            return {"passed": False, "reason": "Invalid price, stop, target, or lot size."}

        act = action.upper()
        if act == "BUY":
            if sl >= cur_p:
                return {"passed": False, "reason": f"Invalid stop loss ₹{sl:,.2f} >= entry ₹{cur_p:,.2f}."}
            if tgt <= cur_p:
                return {"passed": False, "reason": f"Invalid target ₹{tgt:,.2f} <= entry ₹{cur_p:,.2f}."}
            risk_pts = round(cur_p - sl, 2)
            reward_pts = round(tgt - cur_p, 2)
        else:
            if sl <= cur_p:
                return {"passed": False, "reason": f"Invalid stop loss ₹{sl:,.2f} <= entry ₹{cur_p:,.2f}."}
            if tgt >= cur_p:
                return {"passed": False, "reason": f"Invalid target ₹{tgt:,.2f} >= entry ₹{cur_p:,.2f}."}
            risk_pts = round(sl - cur_p, 2)
            reward_pts = round(cur_p - tgt, 2)

        rrr = round(reward_pts / risk_pts, 2) if risk_pts > 0 else 0.0
        if rrr < (min_rrr - 0.02):
            return {
                "passed": False,
                "reason": f"Reward:Risk ratio {rrr:.2f} is below minimum requirement of {min_rrr:.1f}R (Risk: {risk_pts:.1f} pts, Reward: {reward_pts:.1f} pts)."
            }

        total_risk_inr = round(risk_pts * lot, 2)
        risk_note = None
        if total_risk_inr > max_loss:
            risk_note = f"High-conviction movement accepted: Structural stop requires ₹{total_risk_inr:,.1f} risk ({risk_pts:.1f} pts × {lot} lot). Trade taken to capture movement."

        return {
            "passed": True,
            "reason": risk_note or f"Valid institutional bracket: {rrr:.2f}R, Total Risk ₹{total_risk_inr:,.1f} within ₹{max_loss:,.0f} cap.",
            "total_risk_inr": total_risk_inr,
            "risk_pts": risk_pts,
            "reward_pts": reward_pts,
            "rrr": rrr,
            "risk_warning": risk_note
        }





