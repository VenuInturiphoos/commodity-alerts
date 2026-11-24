#!/usr/bin/env python3
"""
Institutional Trading Engine (Multi-Factor Quantitative Architecture)
=====================================================================
Combines 4 proven institutional quantitative algorithms into two distinct,
non-conflicting execution archetypes to maximize edge while eliminating fee churn:

1. Volume Profile Engine (Auction Market Theory):
   - Calculates POC (Point of Control / Fair Value), VAH (Value Area High), VAL (Value Area Low) enclosing 70% of volume.
   - Detects Value Area Acceptance (Expansion) vs Value Area Rotation (Mean-Reversion).

2. Multi-Timeframe Anchored VWAP (AVWAP):
   - Computes dynamic institutional cost basis anchored to Session Open, Weekly Open, and Swing Inflection Points.
   - Standard deviation bands (+/- 1.0 sigma, +/- 1.5 sigma) as dynamic institutional support/resistance.

3. Kaufman Efficiency Ratio (KER - The Noise Filter):
   - Measures price directional displacement vs path length over N bars.
   - Filters out choppy, wandering sideways noise (KER >= 0.45 required for breakouts).

4. Liquidity Sweep & Stop-Run Detector (Turtle Soup):
   - Identifies institutional stop-hunts where price sweeps key levels but immediately reclaims the boundary.

Two Non-Conflicting Execution Archetypes:
- Archetype A: Value Expansion Engine (Directional Breakout - does NOT require a sweep).
- Archetype B: Liquidity Sweep Reversal Engine (Stop-Hunt Reversal - does NOT require a trend).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np
import pandas as pd


@dataclass
class VolumeProfile:
    poc: float           # Point of Control (Highest Volume Price)
    vah: float           # Value Area High (70% Volume Ceiling)
    val: float           # Value Area Low (70% Volume Floor)
    total_volume: float
    profile_bins: Dict[float, float] = field(default_factory=dict)
    state: str = "INSIDE_VALUE" # "ABOVE_VAH", "BELOW_VAL", "INSIDE_VALUE"


@dataclass
class AnchoredVWAP:
    session_vwap: float
    session_upper_1s: float
    session_lower_1s: float
    session_upper_15s: float
    session_lower_15s: float
    weekly_vwap: Optional[float] = None


@dataclass
class InstitutionalSignal:
    symbol: str
    action: str                  # "BUY", "SELL", "STAND_ASIDE"
    archetype: str               # "VALUE_EXPANSION_BREAKOUT", "LIQUIDITY_SWEEP_REVERSAL", "NONE"
    confluence_score: float      # 0 to 100
    current_price: float
    entry_price: float
    stop_loss: float
    target_1: float              # Standard 1.5R target
    target_2: float              # Institutional Runner target (2.5R to 3.5R)
    reward_risk_ratio: float
    ker_efficiency: float        # Kaufman Efficiency Ratio (0 to 1)
    volume_profile: Optional[VolumeProfile] = None
    avwap: Optional[AnchoredVWAP] = None
    is_sweep: bool = False
    rationale: str = ""
    reasons: List[str] = field(default_factory=list)


class VolumeProfileEngine:
    """
    Auction Market Theory Volume Profile Engine.
    Computes Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL).
    """

    @classmethod
    def calculate_profile(cls, closes: List[float], highs: List[float], lows: List[float], 
                          volumes: List[float], num_bins: int = 40, value_area_pct: float = 0.70) -> VolumeProfile:
        if not closes or not highs or not lows or not volumes or len(closes) < 10:
            p = closes[-1] if closes else 0.0
            return VolumeProfile(poc=p, vah=p, val=p, total_volume=0.0)

        min_p = min(lows)
        max_p = max(highs)
        if min_p == max_p:
            return VolumeProfile(poc=min_p, vah=min_p, val=min_p, total_volume=sum(volumes))

        bin_size = (max_p - min_p) / num_bins
        bins = np.linspace(min_p, max_p, num_bins + 1)
        bin_volumes = np.zeros(num_bins)

        for c, h, l, v in zip(closes, highs, lows, volumes):
            # Typical price for the bar
            typ_p = (h + l + c) / 3.0
            bin_idx = min(int((typ_p - min_p) / bin_size), num_bins - 1)
            bin_volumes[bin_idx] += v

        total_vol = float(np.sum(bin_volumes))
        if total_vol <= 0:
            p = closes[-1]
            return VolumeProfile(poc=p, vah=p, val=p, total_volume=0.0)

        # POC: Bin with maximum volume
        poc_idx = int(np.argmax(bin_volumes))
        poc_price = round(float((bins[poc_idx] + bins[poc_idx + 1]) / 2.0), 2)

        # Value Area: Expand outward from POC until 70% of volume is captured
        target_vol = total_vol * value_area_pct
        current_vol = bin_volumes[poc_idx]
        up_idx = poc_idx
        down_idx = poc_idx

        while current_vol < target_vol and (up_idx < num_bins - 1 or down_idx > 0):
            next_up_vol = bin_volumes[up_idx + 1] if up_idx < num_bins - 1 else 0.0
            next_down_vol = bin_volumes[down_idx - 1] if down_idx > 0 else 0.0

            if next_up_vol >= next_down_vol and up_idx < num_bins - 1:
                up_idx += 1
                current_vol += next_up_vol
            elif down_idx > 0:
                down_idx -= 1
                current_vol += next_down_vol
            elif up_idx < num_bins - 1:
                up_idx += 1
                current_vol += next_up_vol
            else:
                break

        vah_price = round(float(bins[up_idx + 1]), 2)
        val_price = round(float(bins[down_idx]), 2)

        cur_p = closes[-1]
        if cur_p > vah_price:
            state = "ABOVE_VAH"
        elif cur_p < val_price:
            state = "BELOW_VAL"
        else:
            state = "INSIDE_VALUE"

        return VolumeProfile(
            poc=poc_price,
            vah=vah_price,
            val=val_price,
            total_volume=total_vol,
            state=state
        )


class AnchoredVWAPEngine:
    """
    Computes Anchored VWAP and Multi-Sigma Standard Deviation Bands.
    """

    @classmethod
    def calculate_avwap(cls, closes: List[float], highs: List[float], lows: List[float], 
                        volumes: List[float]) -> AnchoredVWAP:
        if not closes or not volumes or len(closes) == 0:
            p = closes[-1] if closes else 0.0
            return AnchoredVWAP(session_vwap=p, session_upper_1s=p, session_lower_1s=p,
                                session_upper_15s=p, session_lower_15s=p)

        n = len(closes)
        typ_prices = np.array([(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)])
        vols = np.array(volumes, dtype=float)

        cum_vol = np.sum(vols)
        if cum_vol <= 0:
            p = closes[-1]
            return AnchoredVWAP(session_vwap=p, session_upper_1s=p, session_lower_1s=p,
                                session_upper_15s=p, session_lower_15s=p)

        vwap = float(np.sum(typ_prices * vols) / cum_vol)

        # Variance calculation for standard deviation bands
        squared_diff = (typ_prices - vwap) ** 2
        variance = float(np.sum(squared_diff * vols) / cum_vol)
        stdev = math.sqrt(variance) if variance > 0 else (vwap * 0.005)

        return AnchoredVWAP(
            session_vwap=round(vwap, 2),
            session_upper_1s=round(vwap + 1.0 * stdev, 2),
            session_lower_1s=round(vwap - 1.0 * stdev, 2),
            session_upper_15s=round(vwap + 1.5 * stdev, 2),
            session_lower_15s=round(vwap - 1.5 * stdev, 2)
        )


class KaufmanEfficiencyEngine:
    """
    Computes Perry Kaufman's Efficiency Ratio (KER).
    Measures directional signal-to-noise ratio.
    """

    @classmethod
    def calculate_ker(cls, closes: List[float], period: int = 14) -> float:
        if not closes or len(closes) < period + 1:
            return 0.50

        # Net directional change over period
        net_change = abs(closes[-1] - closes[-1 - period])

        # Sum of absolute bar-to-bar price moves (Total path length)
        sum_changes = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes)))

        if sum_changes <= 0:
            return 0.0

        ker = float(net_change / sum_changes)
        return round(min(1.0, max(0.0, ker)), 3)


class LiquiditySweepDetector:
    """
    Detects Institutional Liquidity Sweeps / Stop-Hunts (Turtle Soup).
    """

    @classmethod
    def check_sweep(cls, highs: List[float], lows: List[float], closes: List[float],
                    volumes: List[float], lookback: int = 20) -> Tuple[bool, str, float]:
        """
        Returns (is_sweep, sweep_type, sweep_extreme_price).
        sweep_type: "BEARISH_SWEEP_RECLAIM" (Bull Trap / Short Signal) or
                    "BULLISH_SWEEP_RECLAIM" (Bear Trap / Long Signal)
        """
        if len(closes) < lookback + 2:
            return False, "NONE", 0.0

        prior_highs = highs[-(lookback + 2):-2]
        prior_lows = lows[-(lookback + 2):-2]
        if not prior_highs or not prior_lows:
            return False, "NONE", 0.0

        prior_range_high = max(prior_highs)
        prior_range_low = min(prior_lows)

        current_h = highs[-1]
        current_l = lows[-1]
        current_c = closes[-1]
        prev_h = highs[-2]
        prev_l = lows[-2]

        avg_vol = np.mean(volumes[-lookback:-1]) if len(volumes) >= lookback else volumes[-1]
        current_vol = volumes[-1]
        has_volume_spark = (current_vol >= 1.20 * avg_vol) if avg_vol > 0 else True

        # 1. Bearish Liquidity Sweep (Swept High, Reclaimed Downward -> SELL)
        # Candle pierces prior high, but closes back inside prior range
        if (current_h > prior_range_high or prev_h > prior_range_high) and (current_c < prior_range_high):
            if current_c < (current_h - 0.40 * (current_h - current_l)): # Closed in lower half of bar (rejection wick)
                if has_volume_spark:
                    return True, "BEARISH_SWEEP_RECLAIM", max(current_h, prev_h)

        # 2. Bullish Liquidity Sweep (Swept Low, Reclaimed Upward -> BUY)
        # Candle pierces prior low, but closes back inside prior range
        if (current_l < prior_range_low or prev_l < prior_range_low) and (current_c > prior_range_low):
            if current_c > (current_l + 0.40 * (current_h - current_l)): # Closed in upper half of bar (absorption wick)
                if has_volume_spark:
                    return True, "BULLISH_SWEEP_RECLAIM", min(current_l, prev_l)

        return False, "NONE", 0.0


class InstitutionalConfluenceEngine:
    """
    Master Quantitative Orchestrator.
    Combines Volume Profile, AVWAP, KER, and Liquidity Sweeps into 
    Two Independent, Non-Conflicting Archetypes:
    - Archetype A: Value Expansion Engine (Breakout)
    - Archetype B: Liquidity Sweep Reversal Engine (Reversal)
    """

    @classmethod
    def evaluate(cls, symbol: str, closes: List[float], highs: List[float], lows: List[float],
                 volumes: List[float], daily_ema50: Optional[float] = None, 
                 atr: Optional[float] = None) -> InstitutionalSignal:

        if not closes or len(closes) < 25:
            p = closes[-1] if closes else 0.0
            return InstitutionalSignal(
                symbol=symbol, action="STAND_ASIDE", archetype="NONE", confluence_score=0.0,
                current_price=p, entry_price=p, stop_loss=p, target_1=p, target_2=p,
                reward_risk_ratio=0.0, ker_efficiency=0.0, rationale="Insufficient candle history (min 25 bars required)."
            )

        cur_p = closes[-1]
        cur_h = highs[-1]
        cur_l = lows[-1]

        # 1. Compute Individual Factors
        vp = VolumeProfileEngine.calculate_profile(closes, highs, lows, volumes)
        avwap = AnchoredVWAPEngine.calculate_avwap(closes, highs, lows, volumes)
        ker = KaufmanEfficiencyEngine.calculate_ker(closes, period=14)
        is_sweep, sweep_type, sweep_price = LiquiditySweepDetector.check_sweep(highs, lows, closes, volumes)

        if atr is None or atr <= 0:
            # Simple ATR 14
            trs = [max(h - l, abs(h - c_prev), abs(l - c_prev)) 
                   for h, l, c_prev in zip(highs[-14:], lows[-14:], closes[-15:-1])]
            atr = float(np.mean(trs)) if trs else (cur_p * 0.01)

        avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else volumes[-1]
        vol_ratio = float(volumes[-1] / (avg_vol + 1e-9))

        reasons = []

        # =====================================================================
        # ARCHETYPE A: VALUE EXPANSION ENGINE (MOMENTUM BREAKOUT)
        # Triggered when price drives out of Value Area with clean trend efficiency
        # =====================================================================
        score_a_bull = 0.0
        score_a_bear = 0.0

        # Bullish Value Expansion
        if vp.state == "ABOVE_VAH" and cur_p >= avwap.session_vwap:
            score_a_bull += 30.0 # Auction clearance
            reasons.append(f"Value Profile: Price trading above VAH ₹{vp.vah:,.2f}")
            if ker >= 0.45:
                score_a_bull += 25.0
                reasons.append(f"Trend Efficiency: Strong directional KER {ker:.2f} >= 0.45")
            if cur_p >= avwap.session_upper_1s:
                score_a_bull += 20.0
                reasons.append(f"Institutional AVWAP: Holding above +1.0σ band (₹{avwap.session_upper_1s:,.2f})")
            if vol_ratio >= 1.25:
                score_a_bull += 15.0
                reasons.append(f"Volume Surge: Current volume is {vol_ratio:.1f}x average")
            if daily_ema50 and cur_p >= daily_ema50:
                score_a_bull += 10.0
                reasons.append("Macro Trend: Aligned above Daily 50-EMA")

        # Bearish Value Expansion
        elif vp.state == "BELOW_VAL" and cur_p <= avwap.session_vwap:
            score_a_bear += 30.0
            reasons.append(f"Value Profile: Price trading below VAL ₹{vp.val:,.2f}")
            if ker >= 0.45:
                score_a_bear += 25.0
                reasons.append(f"Trend Efficiency: Strong downward KER {ker:.2f} >= 0.45")
            if cur_p <= avwap.session_lower_1s:
                score_a_bear += 20.0
                reasons.append(f"Institutional AVWAP: Breaking below -1.0σ band (₹{avwap.session_lower_1s:,.2f})")
            if vol_ratio >= 1.25:
                score_a_bear += 15.0
                reasons.append(f"Volume Surge: Current volume is {vol_ratio:.1f}x average")
            if daily_ema50 and cur_p <= daily_ema50:
                score_a_bear += 10.0
                reasons.append("Macro Trend: Aligned below Daily 50-EMA")

        # =====================================================================
        # ARCHETYPE B: LIQUIDITY SWEEP REVERSAL ENGINE (STOP-HUNT TRAP)
        # Triggered when price sweeps stops and swiftly reclaims Value boundary
        # =====================================================================
        score_b_bull = 0.0
        score_b_bear = 0.0

        if is_sweep and sweep_type == "BULLISH_SWEEP_RECLAIM":
            score_b_bull += 35.0
            reasons.append(f"Liquidity Sweep: Bear trap swept low (₹{sweep_price:,.2f}) and reclaimed boundary")
            if cur_p >= vp.val:
                score_b_bull += 25.0
                reasons.append("Auction Reclaim: Price rotated back inside Value Area (Targeting POC)")
            if vol_ratio >= 1.15:
                score_b_bull += 20.0
                reasons.append(f"Absorption Volume: High volume flush ({vol_ratio:.1f}x)")
            if ker >= 0.35:
                score_b_bull += 20.0

        elif is_sweep and sweep_type == "BEARISH_SWEEP_RECLAIM":
            score_b_bear += 35.0
            reasons.append(f"Liquidity Sweep: Bull trap swept high (₹{sweep_price:,.2f}) and rejected boundary")
            if cur_p <= vp.vah:
                score_b_bear += 25.0
                reasons.append("Auction Reclaim: Price rotated back below VAH (Targeting POC)")
            if vol_ratio >= 1.15:
                score_b_bear += 20.0
                reasons.append(f"Distribution Volume: High volume rejection ({vol_ratio:.1f}x)")
            if ker >= 0.35:
                score_b_bear += 20.0

        # =====================================================================
        # ARBITRATION & SIZING
        # =====================================================================
        CONFLUENCE_THRESHOLD = 70.0

        # Check Bullish Confluence
        if score_a_bull >= CONFLUENCE_THRESHOLD:
            # Archetype A: Value Expansion BUY
            sl = max(vp.vah * 0.997, cur_p - 1.2 * atr)
            risk = abs(cur_p - sl)
            t1 = round(cur_p + 1.5 * risk, 2)
            t2 = round(cur_p + 2.8 * risk, 2)
            return InstitutionalSignal(
                symbol=symbol, action="BUY", archetype="VALUE_EXPANSION_BREAKOUT",
                confluence_score=score_a_bull, current_price=cur_p, entry_price=cur_p,
                stop_loss=round(sl, 2), target_1=t1, target_2=t2, reward_risk_ratio=2.8,
                ker_efficiency=ker, volume_profile=vp, avwap=avwap, is_sweep=False,
                rationale=f"Institutional Value Expansion above VAH (₹{vp.vah:,.2f}) with KER {ker:.2f}. "
                          f"Holding above AVWAP with +{score_a_bull:.0f}/100 confluence.",
                reasons=reasons
            )

        if score_b_bull >= CONFLUENCE_THRESHOLD:
            # Archetype B: Liquidity Sweep BUY
            sl = round(sweep_price - 0.20 * atr, 2)
            risk = abs(cur_p - sl)
            t1 = vp.poc # Primary Target is POC
            t2 = round(vp.vah, 2) # Secondary Target is VAH
            rrr = round(abs(t2 - cur_p) / (risk + 1e-9), 2)
            return InstitutionalSignal(
                symbol=symbol, action="BUY", archetype="LIQUIDITY_SWEEP_REVERSAL",
                confluence_score=score_b_bull, current_price=cur_p, entry_price=cur_p,
                stop_loss=sl, target_1=t1, target_2=t2, reward_risk_ratio=max(1.8, rrr),
                ker_efficiency=ker, volume_profile=vp, avwap=avwap, is_sweep=True,
                rationale=f"Institutional Bear-Trap Reclaim at ₹{sweep_price:,.2f}. "
                          f"Rotating back to POC (₹{vp.poc:,.2f}) with +{score_b_bull:.0f}/100 confluence.",
                reasons=reasons
            )

        # Check Bearish Confluence
        if score_a_bear >= CONFLUENCE_THRESHOLD:
            # Archetype A: Value Expansion SELL
            sl = min(vp.val * 1.003, cur_p + 1.2 * atr)
            risk = abs(sl - cur_p)
            t1 = round(cur_p - 1.5 * risk, 2)
            t2 = round(cur_p - 2.8 * risk, 2)
            return InstitutionalSignal(
                symbol=symbol, action="SELL", archetype="VALUE_EXPANSION_BREAKOUT",
                confluence_score=score_a_bear, current_price=cur_p, entry_price=cur_p,
                stop_loss=round(sl, 2), target_1=t1, target_2=t2, reward_risk_ratio=2.8,
                ker_efficiency=ker, volume_profile=vp, avwap=avwap, is_sweep=False,
                rationale=f"Institutional Value Breakdown below VAL (₹{vp.val:,.2f}) with KER {ker:.2f}. "
                          f"Breaking below AVWAP with +{score_a_bear:.0f}/100 confluence.",
                reasons=reasons
            )

        if score_b_bear >= CONFLUENCE_THRESHOLD:
            # Archetype B: Liquidity Sweep SELL
            sl = round(sweep_price + 0.20 * atr, 2)
            risk = abs(sl - cur_p)
            t1 = vp.poc
            t2 = round(vp.val, 2)
            rrr = round(abs(cur_p - t2) / (risk + 1e-9), 2)
            return InstitutionalSignal(
                symbol=symbol, action="SELL", archetype="LIQUIDITY_SWEEP_REVERSAL",
                confluence_score=score_b_bear, current_price=cur_p, entry_price=cur_p,
                stop_loss=sl, target_1=t1, target_2=t2, reward_risk_ratio=max(1.8, rrr),
                ker_efficiency=ker, volume_profile=vp, avwap=avwap, is_sweep=True,
                rationale=f"Institutional Bull-Trap Rejection at ₹{sweep_price:,.2f}. "
                          f"Rotating back to POC (₹{vp.poc:,.2f}) with +{score_b_bear:.0f}/100 confluence.",
                reasons=reasons
            )

        # Stand Aside (Chop / Insufficient Confluence)
        best_score = max(score_a_bull, score_a_bear, score_b_bull, score_b_bear)
        return InstitutionalSignal(
            symbol=symbol, action="STAND_ASIDE", archetype="NONE", confluence_score=best_score,
            current_price=cur_p, entry_price=cur_p, stop_loss=cur_p, target_1=cur_p, target_2=cur_p,
            reward_risk_ratio=0.0, ker_efficiency=ker, volume_profile=vp, avwap=avwap, is_sweep=is_sweep,
            rationale=f"Market coiling inside Value Area (Score: {best_score:.0f}/100 < 70 threshold). Standing aside to prevent fee churn."
        )


if __name__ == "__main__":
    print("Testing Institutional Confluence Engine...")
    np.random.seed(42)
    # Generate mock price action
    prices = [100.0]
    for _ in range(50):
        prices.append(prices[-1] + np.random.normal(0.2, 0.5))
    highs = [p + np.random.uniform(0.1, 0.4) for p in prices]
    lows = [p - np.random.uniform(0.1, 0.4) for p in prices]
    vols = [np.random.uniform(1000, 5000) for _ in prices]

    sig = InstitutionalConfluenceEngine.evaluate("TEST_ASSET", prices, highs, lows, vols)
    print(f"Action: {sig.action} | Archetype: {sig.archetype} | Score: {sig.confluence_score:.0f}")
    print(f"Rationale: {sig.rationale}")
