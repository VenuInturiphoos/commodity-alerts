#!/usr/bin/env python3
"""
Pre-Move Volatility Coil Scanner.
Scans commodities and equities for coiled setups right before explosive moves ignite.
Usage:
    ./venv/bin/python scan_pre_move.py
"""

import json
import os
import sys
from pre_move_detector import PreMoveDetector

def run_pre_move_scan():
    circuits_path = os.path.join(os.path.dirname(__file__), 'web', 'circuits_data.json')
    if not os.path.exists(circuits_path):
        print(f"File not found: {circuits_path}")
        return

    with open(circuits_path) as f:
        data = json.load(f)

    commodities = data.get('commodities', [])
    print("=" * 95)
    print("🚀 INSTITUTIONAL PRE-MOVE DETECTION SCANNER: COILED ENERGY BEFORE BIG MOVES")
    print("=" * 95)
    print(f"{'SYMBOL':<12} | {'PMRI':<6} | {'ACTION':<7} | {'CURRENT':<10} | {'STOP-LOSS':<10} | {'TARGET 1':<10} | {'RRR':<5} | {'STATUS'}")
    print("-" * 95)

    results = []
    for c in commodities:
        sym = c.get('symbol')
        cur_p = float(c.get('current_price') or 0)
        day_h = float(c.get('day_high') or cur_p)
        day_l = float(c.get('day_low') or cur_p)
        p_close = float(c.get('prev_close') or cur_p)
        chg = float(c.get('change_pct') or 0)
        adx = c.get('adx_14')
        rsi = c.get('rsi_14')
        atr = c.get('atr_14')
        ema20 = c.get('ema_20')
        ema50 = c.get('ema_50')
        vol = c.get('volume', 0)
        avg_vol = c.get('avg_volume_10d', 0)

        sig = PreMoveDetector.evaluate_live_snapshot(
            sym, cur_p, day_h, day_l, p_close, chg, adx=adx, rsi=rsi,
            atr=atr, ema20=ema20, ema50=ema50, volume=vol, avg_volume=avg_vol
        )
        results.append(sig)

    # Sort results by PMRI score (highest probability coil first)
    results.sort(key=lambda x: x.pmri_score, reverse=True)

    for r in results:
        action_color = "🟢 BUY " if r.action == "BUY" else ("🔴 SELL" if r.action == "SELL" else "⏳ WAIT")
        sl_str = f"₹{r.stop_loss:,.2f}" if r.stop_loss > 0 else "-"
        tgt_str = f"₹{r.target_1:,.2f}" if r.target_1 > 0 else "-"
        rrr_str = f"{r.reward_risk_ratio:.1f}R" if r.reward_risk_ratio > 0 else "-"
        print(f"{r.symbol:<12} | {r.pmri_score:>5.1f}% | {action_color:<7} | ₹{r.current_price:>9,.2f} | {sl_str:>10} | {tgt_str:>10} | {rrr_str:>5} | {r.status}")
        if r.status in ["PRE_MOVE_BUY_TRIGGER", "PRE_MOVE_SELL_TRIGGER"]:
            for reason in r.reasons[:2]:
                print(f"   ↳ 🎯 {reason}")

    print("=" * 95)

if __name__ == "__main__":
    run_pre_move_scan()
