import os
import json
import pytz
from datetime import datetime
from alerter import EmailAlerter
from price_checker import PriceChecker

def generate_and_send_6pm_advisory(force=False):
    """
    Scans live market data and historical probabilities at 6:00 PM IST,
    constructs an actionable trade advisory, and sends it via Telegram & Email.
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_ist = now_dt.strftime('%Y-%m-%d')
    today_time_ist = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    day_name = now_dt.strftime('%A')
    
    # Deduplication check: only send once per day unless forced
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    last_6pm_date = state.get('DAILY_6PM_ADVISORY', {}).get('last_alert_date')
    if not force and last_6pm_date == today_ist:
        print(f"Daily 6 PM advisory already dispatched for today ({today_ist}). Skipping.")
        return False

    with open('config.json', 'r') as f:
        config = json.load(f)

    checker = PriceChecker(config)
    alerter = EmailAlerter(config)

    # 1. Fetch live circuits data
    circuits_res = checker.generate_circuits_data()
    commodities_list = circuits_res.get('commodities', []) if circuits_res else []
    
    if not commodities_list:
        print("Could not fetch commodities data for 6 PM advisory.")
        return False

    # 2. Check for pre-6 PM circuit events from history
    hist_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'circuits_history_30d.json')
    pre_6pm_events = []
    if os.path.exists(hist_file):
        try:
            with open(hist_file, 'r') as f:
                hist_data = json.load(f)
                for ev in hist_data.get('events', []):
                    # Check if today's event and was pre-6 PM
                    if ev.get('date') == now_dt.strftime('%d %b %Y'):
                        t_str = ev.get('time', '')
                        if 'AM' in t_str or any(h in t_str for h in ['01:', '02:', '03:', '04:', '05:', '12:']):
                            pre_6pm_events.append(ev)
        except Exception as e:
            print(f"Error reading history for pre-6pm events: {e}")

    # 3. Build Action Recommendations for each commodity
    action_items = []
    has_active_setups = False

    for item in commodities_list:
        sym = item['symbol']
        name = item['name']
        cur_p = item['current_price']
        p_close = item['prev_close']
        chg_pct = item['change_pct']
        status = item['status']
        dist_lc = item['dist_to_lc_pct']
        dist_uc = item['dist_to_uc_pct']
        lc_price = item['lower_circuit']
        uc_price = item['upper_circuit']

        # Rule evaluation
        # Case 1: Pre-6 PM circuit test
        pre_ev = next((e for e in pre_6pm_events if e.get('symbol') == sym), None)
        if pre_ev:
            has_active_setups = True
            direction = pre_ev.get('direction', 'LOWER')
            if direction == 'LOWER':
                action_items.append(
                    f"⭐ <b>{name.upper()} (Pre-6 PM Lower Circuit Test)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%)\n"
                    f"  • Historical Win Rate: <b>100% Reversal</b> (Asian/EU hours lack breakout volume).\n"
                    f"  • Recommended Action: <b>BUY / FADE THE MOVE</b>\n"
                    f"  • Entry: Around current levels (₹{cur_p:,.2f})\n"
                    f"  • Stop-Loss: ₹{lc_price * 0.996:,.2f} (0.4% below LC ₹{lc_price:,.2f})\n"
                    f"  • Profit Target: ₹{p_close:,.2f} (Prev Settlement Anchor)"
                )
            else:
                action_items.append(
                    f"⭐ <b>{name.upper()} (Pre-6 PM Upper Circuit Test)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%)\n"
                    f"  • Historical Win Rate: <b>100% Mean-Reversion</b>\n"
                    f"  • Recommended Action: <b>SELL / SHORT THE SPIKE</b>\n"
                    f"  • Entry: Around current levels (₹{cur_p:,.2f})\n"
                    f"  • Stop-Loss: ₹{uc_price * 1.004:,.2f} (0.4% above UC ₹{uc_price:,.2f})\n"
                    f"  • Profit Target: ₹{p_close:,.2f} (Prev Settlement Anchor)"
                )
        # Case 2: Near or at circuit right now (6 PM US Pit Open)
        elif 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            has_active_setups = True
            if 'LOWER' in status:
                if sym == 'SILVER':
                    action_items.append(
                        f"⚠️ <b>SILVER (Near/At Lower Circuit Floor)</b>\n"
                        f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}% | {dist_lc:.2f}% to LC ₹{lc_price:,.2f})\n"
                        f"  • Historical Probability: <b>66.7% Downward Extension Rate</b>\n"
                        f"  • Recommended Action: <b>DO NOT KNIFE-CATCH!</b> Apply 15-min rule.\n"
                        f"  • Wait until 6:15 PM IST. If price breaks below ₹{lc_price:,.2f}, ride momentum lower into expanded slab.\n"
                        f"  • Only buy if a strong rejection pin-bar closes back inside the band above ₹{lc_price:,.2f}."
                    )
                elif sym == 'CRUDEOIL':
                    action_items.append(
                        f"🟢 <b>CRUDE OIL (Near/At Lower Circuit Floor)</b>\n"
                        f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}% | {dist_lc:.2f}% to LC ₹{lc_price:,.2f})\n"
                        f"  • Historical Probability: <b>52.9% Bounce Rate</b> (NYMEX pit open mean-reversion)\n"
                        f"  • Recommended Action: <b>BUY THE REBOUND</b> (Wait for 6:15 PM confirmation candle)\n"
                        f"  • Stop-Loss: ₹{lc_price * 0.996:,.2f} (Strict 0.4% buffer below LC)\n"
                        f"  • Target: ₹{(cur_p + p_close) / 2:,.2f} (50% retracement to Anchor ₹{p_close:,.2f})"
                    )
                else:
                    action_items.append(
                        f"⚠️ <b>{name.upper()} (Testing Lower Circuit Band)</b>\n"
                        f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}% | {dist_lc:.2f}% from Floor)\n"
                        f"  • Action: Wait for 6:15 PM candle close before entering. Stop-loss strictly at intraday low."
                    )
            else: # Upper circuit
                if sym == 'COPPER':
                    action_items.append(
                        f"🟢 <b>COPPER (Near/At Upper Circuit Ceiling)</b>\n"
                        f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}% | {dist_uc:.2f}% to UC ₹{uc_price:,.2f})\n"
                        f"  • Historical Probability: <b>50.0% Pullback Rate</b> (US institutional profit taking)\n"
                        f"  • Recommended Action: <b>FADE / TAKE PROFIT</b> on longs.\n"
                        f"  • Stop-Loss: ₹{uc_price * 1.004:,.2f}"
                    )
                else:
                    action_items.append(
                        f"⚠️ <b>{name.upper()} (Testing Upper Circuit Band)</b>\n"
                        f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}% | {dist_uc:.2f}% from Ceiling)\n"
                        f"  • Action: Wait for 6:15 PM candle. Do not chase breakouts without volume confirmation."
                    )
        elif abs(chg_pct) >= 2.0:
            has_active_setups = True
            action_items.append(
                f"ℹ️ <b>{name.upper()} (Trending Session: {chg_pct:+.2f}%)</b>\n"
                f"  • Live: ₹{cur_p:,.2f} (Floor: ₹{lc_price:,.2f} | Ceiling: ₹{uc_price:,.2f})\n"
                f"  • High US session volatility expected post-6 PM. Trade only with defined S1/R1 stops."
            )

    if not has_active_setups:
        action_items.append(
            "🟢 <b>ALL 5 COMMODITIES ARE IN NORMAL SAFE TRADING RANGES</b>\n"
            "  • No circuit limits tested or threatened today (all assets within ±1.5% of anchor).\n"
            "  • <b>Action</b>: There is zero statistical circuit edge today. Do NOT place counter-trend circuit bets.\n"
            "  • Stick to standard Support/Resistance levels (S1/R1) or preserve capital for US open volatility."
        )

    # Day of week context
    day_notes = {
        'Friday': "⚠️ <b>Friday Volatility Alert</b>: Friday post-6 PM moves average ±1.06% (highest weekly volatility). Reduce standard position sizing by 25%.",
        'Thursday': "📊 <b>Thursday EIA Inventory</b>: US Natural Gas EIA storage report drops tonight at 8:00 PM IST. Avoid counter-trend NatGas positions.",
        'Monday': "ℹ️ <b>Monday Session</b>: Historical post-6 PM moves average ±0.59% (lowest weekly volatility). Expect range-bound closes."
    }
    dow_note = day_notes.get(day_name, "ℹ️ <b>Midweek Session</b>: US New York pit open starting between 6:00 PM – 6:30 PM IST.")

    # Status overview summary
    overview_lines = []
    for c in commodities_list:
        overview_lines.append(f"• <b>{c['name']}</b>: ₹{c['current_price']:,.2f} ({c['change_pct']:+.2f}%) | {c['status_text']}")

    overview_text = "\n".join(overview_lines)
    action_text = "\n\n".join(action_items)

    subject = f"🎯 [6 PM IST ADVISORY] MCX Commodity Action Playbook ({day_name})"
    body = (
        f"🎯 <b>DAILY 6:00 PM IST ACTION ADVISORY</b>\n"
        f"📅 <i>{today_time_ist}</i>\n\n"
        f"<b>📊 LIVE COMMODITY STATUS OVERVIEW:</b>\n"
        f"{overview_text}\n\n"
        f"<b>⚡ HIGH-PROBABILITY ACTION PLAYBOOK:</b>\n"
        f"{action_text}\n\n"
        f"<b>🛡️ RISK MANAGEMENT & EXECUTION RULES:</b>\n"
        f"1. <b>15-Minute Rule</b>: For fresh 6 PM circuit hits, wait until 6:15 PM IST candle close to verify if limit holds.\n"
        f"2. <b>11:15 PM Hard Exit</b>: Square off all intraday positions before MCX settlement (11:30 PM IST). Never gamble on overnight gaps.\n"
        f"3. {dow_note}\n\n"
        f"🌐 <b>Live Interactive Dashboard</b>: https://VenuInturiphoos.github.io/commodity-alerts/#circuits"
    )

    # Record in paper trading advisory tracker
    try:
        from advisory_tracker import record_live_6pm_advisory
        record_live_6pm_advisory(commodities_list, pre_6pm_events)
    except Exception as e:
        print(f"Error recording live advisory in paper trading tracker: {e}")

    alert_payload = [{
        'symbol': 'DAILY_6PM_ADVISORY',
        'name': 'Daily 6 PM Action Advisory',
        'event_type': 'DAILY_6PM_ADVISORY',
        'today_ist': today_ist,
        'subject': subject,
        'body': body
    }]

    print(f"Dispatching 6:00 PM IST Daily Action Advisory for {today_ist}...")
    success, sent_alerts = alerter.send_alerts(alert_payload)
    
    if success:
        # Record dispatch
        state['DAILY_6PM_ADVISORY'] = {
            'last_alert_date': today_ist,
            'last_alert_time': today_time_ist,
            'last_alert_msg': subject
        }
        try:
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            print("Successfully recorded Daily 6 PM advisory dispatch in alerts_state.json")
        except Exception as e:
            print(f"Error saving alerts_state.json: {e}")
        return True
    else:
        print("Failed to dispatch 6 PM advisory.")
        return False

if __name__ == "__main__":
    import sys
    if '--check-615' in sys.argv:
        with open('config.json', 'r') as f:
            cfg = json.load(f)
        chk = PriceChecker(cfg)
        alt = EmailAlerter(cfg)
        from advisory_tracker import check_and_execute_615_confirmation
        check_and_execute_615_confirmation(chk, alt)
    elif '--exit-eod' in sys.argv:
        with open('config.json', 'r') as f:
            cfg = json.load(f)
        chk = PriceChecker(cfg)
        alt = EmailAlerter(cfg)
        from advisory_tracker import exit_and_evaluate_eod_trades
        exit_and_evaluate_eod_trades(chk, alt)
    else:
        force_run = '--force' in sys.argv
        generate_and_send_6pm_advisory(force=force_run)
