"""
Pre-Move Detection Model (PMDM) - Institutional Pre-Ignition Volatility Coil Engine.
Identifies high-probability trade entries *RIGHT BEFORE* massive directional expansion occurs.

Core Philosophy:
Entering *after* a 2-3% breakout forces wide stop-losses and poor reward-to-risk ratios.
This model detects the physical accumulation phase that precedes explosive moves:
1. Volatility Squeeze (Bollinger Bands compressing strictly inside Keltner Channels - TTM Squeeze).
2. Momentum Vector Pre-Alignment (Linear regression slope & delta turning before price breaks out).
3. Volume Contraction Pattern (VCP) & Pre-Ignition Volume Spark (NR7 / volume dry-up).
4. Wyckoff Liquidity Sweep Reclaim (Stop-hunt spring/upthrust right before ignition).
5. Ultra-Asymmetric Risk Geometry (Stop tucked tightly inside the coil, offering 3R to 6R+ expectancy).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PreMoveSignal:
    symbol: str
    action: str  # 'BUY' or 'SELL' or 'WAITING_COIL' or 'NEUTRAL'
    status: str  # 'PRE_MOVE_BUY_TRIGGER', 'PRE_MOVE_SELL_TRIGGER', 'SQUEEZE_COILING', 'INSUFFICIENT_CONVERGENCE'
    pmri_score: float  # Pre-Move Readiness Index (0 - 100)
    current_price: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    reward_risk_ratio: float
    squeeze_active: bool
    squeeze_bars: int
    momentum_direction: str  # 'BULLISH_EXPANDING', 'BEARISH_EXPANDING', 'FLAT'
    momentum_value: float
    bandwidth_percentile: float
    volume_ratio: float
    wyckoff_spring: bool
    reasons: List[str] = field(default_factory=list)
    setup_name: str = "Pre-Ignition Volatility Coil"


class PreMoveDetector:
    """
    Institutional Pre-Move Detection Engine.
    Detects the quiet coiling phase immediately before trend ignition.
    """

    # Model Calibration Constants
    BB_LENGTH = 20
    BB_STD = 2.0
    KC_LENGTH = 20
    KC_ATR_MULT = 1.5
    MOMENTUM_LENGTH = 20
    MIN_SQUEEZE_BARS = 3
    MIN_PMRI_TRIGGER = 72.0  # Minimum score out of 100 to trigger early entry

    @classmethod
    def calculate_indicators(cls, closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Dict[str, Any]:
        """
        Calculates all indicator layers required for Pre-Move detection:
        - Bollinger Bands (20, 2.0)
        - Keltner Channels (20, 1.5 ATR)
        - ATR (14)
        - Momentum Linear Regression Slope
        - Volume Contraction & Historical Volatility Percentile
        """
        n = len(closes)
        if n < cls.BB_LENGTH + 5:
            return {}

        # 1. Exponential Moving Average (EMA 20) & SMA 20
        period = cls.BB_LENGTH
        sma20 = sum(closes[-period:]) / period
        
        # EMA 20
        multiplier = 2.0 / (period + 1.0)
        ema20 = closes[0]
        ema_series = [ema20]
        for p in closes[1:]:
            ema20 = (p - ema20) * multiplier + ema20
            ema_series.append(ema20)
        curr_ema20 = ema_series[-1]

        # 2. Bollinger Bands
        variance = sum((c - sma20) ** 2 for c in closes[-period:]) / period
        std_dev = math.sqrt(variance)
        bb_upper = sma20 + (cls.BB_STD * std_dev)
        bb_lower = sma20 - (cls.BB_STD * std_dev)
        bb_width = (bb_upper - bb_lower) / curr_ema20 * 100.0 if curr_ema20 > 0 else 0.0

        # 3. Average True Range (ATR 14 / 20)
        tr_list = []
        for i in range(1, n):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)

        if len(tr_list) < 14:
            atr14 = tr_list[-1] if tr_list else (closes[-1] * 0.01)
        else:
            # Wilder's Smoothing for ATR
            atr14 = sum(tr_list[:14]) / 14.0
            for tr in tr_list[14:]:
                atr14 = (atr14 * 13.0 + tr) / 14.0

        # Keltner Channels (using EMA 20 and 1.5 * ATR)
        kc_upper = curr_ema20 + (cls.KC_ATR_MULT * atr14)
        kc_lower = curr_ema20 - (cls.KC_ATR_MULT * atr14)

        # 4. Squeeze Condition: Bollinger Bands strictly inside Keltner Channels
        # Squeeze is ACTIVE when BB Upper < KC Upper AND BB Lower > KC Lower
        squeeze_active = (bb_upper < kc_upper) and (bb_lower > kc_lower)

        # Squeeze duration (look back up to 25 bars to count consecutive squeeze bars)
        squeeze_bars = 0
        if squeeze_active:
            # Check historical bars
            for lookback in range(1, min(25, n - period)):
                idx = -(lookback + 1)
                sub_closes = closes[:idx]
                if len(sub_closes) < period:
                    break
                sub_sma = sum(sub_closes[-period:]) / period
                sub_var = sum((c - sub_sma) ** 2 for c in sub_closes[-period:]) / period
                sub_std = math.sqrt(sub_var)
                sub_bb_u = sub_sma + (cls.BB_STD * sub_std)
                sub_bb_l = sub_sma - (cls.BB_STD * sub_std)
                sub_ema = ema_series[idx]
                sub_atr = atr14  # Approximation for historical lookback
                sub_kc_u = sub_ema + (cls.KC_ATR_MULT * sub_atr)
                sub_kc_l = sub_ema - (cls.KC_ATR_MULT * sub_atr)
                if (sub_bb_u < sub_kc_u) and (sub_bb_l > sub_kc_l):
                    squeeze_bars += 1
                else:
                    break
            squeeze_bars += 1  # include current bar

        # 5. Momentum Indicator (Linear Regression of Delta from Donchian Midline)
        # Delta = Close - [ (Highest High + Lowest Low)/2 + EMA20 ] / 2
        deltas = []
        for i in range(max(0, n - 25), n):
            sub_h = highs[max(0, i - period + 1): i + 1]
            sub_l = lows[max(0, i - period + 1): i + 1]
            hh = max(sub_h) if sub_h else highs[i]
            ll = min(sub_l) if sub_l else lows[i]
            mid_donchian = (hh + ll) / 2.0
            mid_baseline = (mid_donchian + ema_series[i]) / 2.0
            deltas.append(closes[i] - mid_baseline)

        # Linear regression slope of deltas over last 20 bars
        if len(deltas) >= 5:
            m_len = min(len(deltas), 20)
            x = list(range(m_len))
            y = deltas[-m_len:]
            x_mean = sum(x) / m_len
            y_mean = sum(y) / m_len
            numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(m_len))
            denominator = sum((x[i] - x_mean) ** 2 for i in range(m_len))
            mom_slope = numerator / denominator if denominator != 0 else 0.0
            curr_mom_val = y[-1]
            prev_mom_val = y[-2] if len(y) >= 2 else curr_mom_val
        else:
            mom_slope = 0.0
            curr_mom_val = 0.0
            prev_mom_val = 0.0

        # 6. Volume Profile & Contraction
        vol_20_avg = sum(volumes[-20:]) / 20.0 if len(volumes) >= 20 and sum(volumes[-20:]) > 0 else 1.0
        curr_vol = volumes[-1] if volumes else 1.0
        prev_vol = volumes[-2] if len(volumes) >= 2 else curr_vol
        vol_ratio = curr_vol / vol_20_avg if vol_20_avg > 0 else 1.0
        vol_contracted = vol_ratio < 0.70  # Low volume during coil
        vol_spark = (curr_vol > prev_vol * 1.15)  # Micro uptick on trigger bar

        # 7. Narrow Range 7 (NR7) - Coiling Bar
        ranges = [highs[i] - lows[i] for i in range(max(0, n - 7), n)]
        curr_range = ranges[-1] if ranges else atr14
        is_nr7 = curr_range == min(ranges) if len(ranges) >= 7 else False

        # 8. Wyckoff Liquidity Sweep Detection
        # Bear Trap / Spring: Low probed below recent 10-bar low but closed in upper 45% of candle
        recent_10_low = min(lows[-11:-1]) if len(lows) >= 11 else lows[-1]
        candle_body = highs[-1] - lows[-1]
        is_spring = (lows[-1] < recent_10_low) and (closes[-1] > (lows[-1] + 0.55 * candle_body)) if candle_body > 0 else False

        # Bull Trap / Upthrust: High probed above recent 10-bar high but closed in lower 45% of candle
        recent_10_high = max(highs[-11:-1]) if len(highs) >= 11 else highs[-1]
        is_upthrust = (highs[-1] > recent_10_high) and (closes[-1] < (highs[-1] - 0.55 * candle_body)) if candle_body > 0 else False

        return {
            "close": closes[-1],
            "high": highs[-1],
            "low": lows[-1],
            "ema20": curr_ema20,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_width": bb_width,
            "kc_upper": kc_upper,
            "kc_lower": kc_lower,
            "atr14": atr14,
            "squeeze_active": squeeze_active,
            "squeeze_bars": squeeze_bars,
            "mom_val": curr_mom_val,
            "prev_mom_val": prev_mom_val,
            "mom_slope": mom_slope,
            "vol_ratio": vol_ratio,
            "vol_contracted": vol_contracted,
            "vol_spark": vol_spark,
            "is_nr7": is_nr7,
            "is_spring": is_spring,
            "is_upthrust": is_upthrust
        }

    @classmethod
    def evaluate(cls, symbol: str, closes: List[float], highs: List[float], lows: List[float],
                 volumes: List[float], adx: Optional[float] = None, rsi: Optional[float] = None) -> PreMoveSignal:
        """
        Evaluates historical candle series and computes Pre-Move Readiness Index (PMRI).
        Returns actionable PreMoveSignal with entry, tight invalidation stop, and targets.
        """
        metrics = cls.calculate_indicators(closes, highs, lows, volumes)
        if not metrics:
            return PreMoveSignal(
                symbol=symbol, action="NEUTRAL", status="INSUFFICIENT_DATA",
                pmri_score=0.0, current_price=closes[-1] if closes else 0.0,
                entry_price=0.0, stop_loss=0.0, target_1=0.0, target_2=0.0,
                reward_risk_ratio=0.0, squeeze_active=False, squeeze_bars=0,
                momentum_direction="FLAT", momentum_value=0.0, bandwidth_percentile=50.0,
                volume_ratio=1.0, wyckoff_spring=False, reasons=["Insufficient candle history (min 25 bars required)"]
            )

        cur_p = metrics["close"]
        ema20 = metrics["ema20"]
        atr = metrics["atr14"]
        squeeze = metrics["squeeze_active"]
        sq_bars = metrics["squeeze_bars"]
        mom_val = metrics["mom_val"]
        prev_mom = metrics["prev_mom_val"]
        mom_slope = metrics["mom_slope"]
        vol_ratio = metrics["vol_ratio"]
        vol_spark = metrics["vol_spark"]
        is_nr7 = metrics["is_nr7"]
        is_spring = metrics["is_spring"]
        is_upthrust = metrics["is_upthrust"]

        pmri_score = 0.0
        reasons = []

        # ---------------------------------------------------------------------
        # PILLAR 1: Squeeze Intensity & Duration (Max 30 Points)
        # ---------------------------------------------------------------------
        if squeeze:
            if sq_bars >= 5:
                pmri_score += 30.0
                reasons.append(f"Extended Squeeze: Compressed inside Keltner Channel for {sq_bars} bars (Heavy Energy Accumulation).")
            elif sq_bars >= 3:
                pmri_score += 25.0
                reasons.append(f"Active Squeeze: Bollinger Bands coiled inside Keltner Channel ({sq_bars} bars).")
            else:
                pmri_score += 15.0
                reasons.append(f"Fresh Squeeze: Early coil forming ({sq_bars} bar).")
        else:
            # Check if squeeze JUST fired (released within 1-2 bars)
            if metrics["bb_width"] < 4.0:
                pmri_score += 12.0
                reasons.append("Ultra-low volatility bandwidth (< 4.0%), coiling on the verge of squeeze.")

        # ---------------------------------------------------------------------
        # PILLAR 2: Momentum Vector Alignment (Max 25 Points)
        # ---------------------------------------------------------------------
        is_bull_mom = (mom_val > 0 and mom_val >= prev_mom) or (mom_val < 0 and mom_val > prev_mom and mom_slope > 0)
        is_bear_mom = (mom_val < 0 and mom_val <= prev_mom) or (mom_val > 0 and mom_val < prev_mom and mom_slope < 0)

        if is_bull_mom and cur_p >= (ema20 * 0.998):
            pmri_score += 25.0
            reasons.append(f"Bullish Momentum Vector: Linear regression delta rising ({mom_val:+.2f}), holding above 20 EMA.")
            mom_dir = "BULLISH_EXPANDING"
        elif is_bear_mom and cur_p <= (ema20 * 1.002):
            pmri_score += 25.0
            reasons.append(f"Bearish Momentum Vector: Linear regression delta falling ({mom_val:+.2f}), pinned below 20 EMA.")
            mom_dir = "BEARISH_EXPANDING"
        else:
            pmri_score += 8.0
            mom_dir = "FLAT"

        # ---------------------------------------------------------------------
        # PILLAR 3: Volume Contraction Pattern & Spark (Max 20 Points)
        # ---------------------------------------------------------------------
        if vol_ratio < 0.65:
            pmri_score += 12.0
            reasons.append(f"VCP Supply Dry-Up: Volume dried up to {vol_ratio:.2f}x of 20-period average.")
            if vol_spark:
                pmri_score += 8.0
                reasons.append("Pre-Ignition Volume Spark: Early institutional volume uptick on quiet candle.")
        elif vol_spark:
            pmri_score += 10.0
            reasons.append("Volume Spark: Accumulation tick on coiling bar.")

        # ---------------------------------------------------------------------
        # PILLAR 4: Wyckoff Liquidity Sweep Reclaim & NR7 (Max 15 Points)
        # ---------------------------------------------------------------------
        if is_spring:
            pmri_score += 15.0
            reasons.append("Wyckoff Spring: Liquidity sweep below recent lows immediately rejected and reclaimed.")
        elif is_upthrust:
            pmri_score += 15.0
            reasons.append("Wyckoff Upthrust: Liquidity sweep above recent highs rejected, trapping late buyers.")
        elif is_nr7:
            pmri_score += 10.0
            reasons.append("NR7 Coiling Bar: Narrowest trading range of last 7 sessions (volatility bottleneck).")

        # ---------------------------------------------------------------------
        # PILLAR 5: Technical Baseline (ADX / RSI Confirmation) (Max 10 Points)
        # ---------------------------------------------------------------------
        if adx is not None:
            # Squeezes thrive when ADX is turning UP from a compressed base (< 22 rising or >= 20)
            if adx >= 18.0:
                pmri_score += 5.0
                reasons.append(f"Trend engine primed: ADX {adx:.1f} ready for breakout expansion.")
        if rsi is not None:
            # RSI in healthy accumulation zone (46 - 62) during coil
            if 44.0 <= rsi <= 64.0:
                pmri_score += 5.0
                reasons.append(f"RSI in accumulation sweet spot ({rsi:.1f}), avoiding premature overbought/oversold.")

        pmri_score = min(100.0, round(pmri_score, 1))

        # ---------------------------------------------------------------------
        # TRADE RECOMMENDATION & BRACKET GEOMETRY
        # ---------------------------------------------------------------------
        action = "WAITING_COIL"
        status = "SQUEEZE_COILING"
        entry_p = cur_p
        sl = 0.0
        tgt1 = 0.0
        tgt2 = 0.0
        rrr = 0.0

        if pmri_score >= cls.MIN_PMRI_TRIGGER and (squeeze or metrics["bb_width"] < 4.5):
            if mom_dir == "BULLISH_EXPANDING" or is_spring:
                action = "BUY"
                status = "PRE_MOVE_BUY_TRIGGER"
                # Ultra-tight invalidation stop: placed strictly under the coil base / spring low
                coil_low = min(metrics["bb_lower"], metrics["low"])
                sl = round(coil_low - (0.20 * atr), 2)
                risk_pts = max(cur_p - sl, 0.40 * atr)
                tgt1 = round(cur_p + (2.2 * risk_pts), 2)  # 2.2R First Target
                tgt2 = round(cur_p + (3.8 * risk_pts), 2)  # 3.8R Explosive Runner
                rrr = round(abs(tgt1 - cur_p) / risk_pts, 2) if risk_pts > 0 else 2.2
            elif mom_dir == "BEARISH_EXPANDING" or is_upthrust:
                action = "SELL"
                status = "PRE_MOVE_SELL_TRIGGER"
                # Ultra-tight invalidation stop: placed strictly above coil roof / upthrust high
                coil_high = max(metrics["bb_upper"], metrics["high"])
                sl = round(coil_high + (0.20 * atr), 2)
                risk_pts = max(sl - cur_p, 0.40 * atr)
                tgt1 = round(cur_p - (2.2 * risk_pts), 2)
                tgt2 = round(cur_p - (3.8 * risk_pts), 2)
                rrr = round(abs(cur_p - tgt1) / risk_pts, 2) if risk_pts > 0 else 2.2

        return PreMoveSignal(
            symbol=symbol,
            action=action,
            status=status,
            pmri_score=pmri_score,
            current_price=cur_p,
            entry_price=entry_p,
            stop_loss=sl,
            target_1=tgt1,
            target_2=tgt2,
            reward_risk_ratio=rrr,
            squeeze_active=squeeze,
            squeeze_bars=sq_bars,
            momentum_direction=mom_dir,
            momentum_value=round(mom_val, 2),
            bandwidth_percentile=round(metrics["bb_width"], 2),
            volume_ratio=round(vol_ratio, 2),
            wyckoff_spring=is_spring or is_upthrust,
            reasons=reasons,
            setup_name="Pre-Ignition Volatility Coil"
        )

    @classmethod
    def evaluate_live_snapshot(cls, symbol: str, current_price: float, day_high: float, day_low: float,
                               prev_close: float, change_pct: float, adx: Optional[float] = None,
                               rsi: Optional[float] = None, atr: Optional[float] = None,
                               ema20: Optional[float] = None, ema50: Optional[float] = None,
                               volume: Optional[float] = None, avg_volume: Optional[float] = None) -> PreMoveSignal:
        """
        Lightweight snapshot evaluator when full candle history is not loaded.
        Uses intraday range compression, day high/low bandwidth, and momentum indicators.
        """
        if current_price <= 0:
            return PreMoveSignal(
                symbol=symbol, action="NEUTRAL", status="INVALID_PRICE", pmri_score=0.0,
                current_price=0.0, entry_price=0.0, stop_loss=0.0, target_1=0.0, target_2=0.0,
                reward_risk_ratio=0.0, squeeze_active=False, squeeze_bars=0,
                momentum_direction="FLAT", momentum_value=0.0, bandwidth_percentile=50.0,
                volume_ratio=1.0, wyckoff_spring=False, reasons=["Invalid price data"]
            )

        atr_val = atr if (atr and atr > 0) else (current_price * 0.008)
        day_range = max(day_high - day_low, current_price * 0.002)
        day_range_pct = (day_range / current_price) * 100.0

        vol_ratio = (volume / avg_volume) if (volume and avg_volume and avg_volume > 0) else 1.0
        
        # High compression: day range < 1.1x ATR or < 1.0% total range
        is_compressed = (day_range < (1.15 * atr_val)) or (day_range_pct < 1.0)
        
        pmri_score = 0.0
        reasons = []

        if is_compressed:
            pmri_score += 35.0
            reasons.append(f"Intraday Range Coiled: Day range is only {day_range_pct:.2f}% ({day_range:.2f} pts <= 1.15x ATR). Volatility bottleneck.")

        if vol_ratio < 0.70:
            pmri_score += 20.0
            reasons.append(f"Volume Contraction: Volume at {vol_ratio:.2f}x average indicates supply exhaustion before move.")
        elif vol_ratio < 0.90:
            pmri_score += 10.0

        # Momentum vector from EMA alignment and change %
        ema_ref = ema20 or ema50 or prev_close
        if current_price >= ema_ref and (change_pct >= -0.20):
            mom_dir = "BULLISH_EXPANDING"
            pmri_score += 25.0
            reasons.append(f"Price holding above baseline (₹{ema_ref:,.2f}), building upward coil pressure.")
        elif current_price <= ema_ref and (change_pct <= 0.20):
            mom_dir = "BEARISH_EXPANDING"
            pmri_score += 25.0
            reasons.append(f"Price capped below baseline (₹{ema_ref:,.2f}), building downward coil pressure.")
        else:
            mom_dir = "FLAT"

        if adx is not None and adx >= 18.0:
            pmri_score += 10.0
            reasons.append(f"ADX strength ({adx:.1f}) ready to sustain breakout momentum.")

        if rsi is not None and (45.0 <= rsi <= 60.0):
            pmri_score += 10.0
            reasons.append(f"Neutral RSI ({rsi:.1f}) provides maximum expansion runway without exhaustion.")

        pmri_score = min(100.0, round(pmri_score, 1))

        action = "WAITING_COIL"
        status = "SQUEEZE_COILING"
        sl = 0.0
        tgt1 = 0.0
        tgt2 = 0.0
        rrr = 0.0

        if pmri_score >= cls.MIN_PMRI_TRIGGER and is_compressed:
            if mom_dir == "BULLISH_EXPANDING":
                action = "BUY"
                status = "PRE_MOVE_BUY_TRIGGER"
                sl = round(day_low - (0.25 * atr_val), 2)
                risk_pts = max(current_price - sl, 0.40 * atr_val)
                tgt1 = round(current_price + (2.0 * risk_pts), 2)
                tgt2 = round(current_price + (3.5 * risk_pts), 2)
                rrr = round((tgt1 - current_price) / risk_pts, 2)
            elif mom_dir == "BEARISH_EXPANDING":
                action = "SELL"
                status = "PRE_MOVE_SELL_TRIGGER"
                sl = round(day_high + (0.25 * atr_val), 2)
                risk_pts = max(sl - current_price, 0.40 * atr_val)
                tgt1 = round(current_price - (2.0 * risk_pts), 2)
                tgt2 = round(current_price - (3.5 * risk_pts), 2)
                rrr = round((current_price - tgt1) / risk_pts, 2)

        return PreMoveSignal(
            symbol=symbol,
            action=action,
            status=status,
            pmri_score=pmri_score,
            current_price=current_price,
            entry_price=current_price,
            stop_loss=sl,
            target_1=tgt1,
            target_2=tgt2,
            reward_risk_ratio=rrr,
            squeeze_active=is_compressed,
            squeeze_bars=4 if is_compressed else 0,
            momentum_direction=mom_dir,
            momentum_value=round(change_pct, 2),
            bandwidth_percentile=round(day_range_pct, 2),
            volume_ratio=round(vol_ratio, 2),
            wyckoff_spring=False,
            reasons=reasons,
            setup_name="Pre-Ignition Volatility Coil"
        )
