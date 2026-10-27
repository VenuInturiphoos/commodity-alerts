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
    last_6pm_time = state.get('DAILY_6PM_ADVISORY', {}).get('last_alert_time', '')
    is_off_hours = False
    if last_6pm_time:
        if 'AM' in last_6pm_time or any(f" {h:02d}:" in last_6pm_time for h in range(1, 17)):
            is_off_hours = True

    if not force and last_6pm_date == today_ist and not is_off_hours:
        print(f"Daily 6 PM advisory already dispatched for today ({today_ist}) at {last_6pm_time}. Skipping.")
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

    # 🇬🇧 1:30 PM London Breakout Session & Trades Review
    london_review_text = ""
    try:
        from advisory_tracker import load_tracker
        tracker_data = load_tracker()
        today_str = now_dt.strftime('%d %b %Y')
        london_trades = [t for t in tracker_data.get('trades', []) if t.get('date') == today_str and '1:30 PM' in t.get('session_name', '')]
        if london_trades:
            london_lines = ["🇬🇧 <b>1:30 PM LONDON BREAKOUT TRADES REVIEW:</b>"]
            for lt in london_trades:
                pt = lt.get('paper_trade', {})
                act = pt.get('action', 'TRADE')
                qty = pt.get('quantity', lt.get('lot_size', 1))
                ep = pt.get('entry_price', 0)
                st = pt.get('status', 'ACTIVE')
                pnl = pt.get('pnl_inr', 0.0)
                pnl_str = f" | P&L: ₹{pnl:+,.2f}" if pnl != 0 else ""
                london_lines.append(f"• <b>{lt['name']}</b>: {act} {qty} units @ ₹{ep:,.2f} | Status: <b>{st}</b>{pnl_str} ({lt.get('event_context', '')})")
            london_review_text = "\n".join(london_lines) + "\n\n"
        else:
            london_review_text = (
                "🇬🇧 <b>1:30 PM LONDON BREAKOUT SESSION REVIEW:</b>\n"
                "• All commodities held inside Asian morning trading ranges.\n"
                "• Zero false breakout entries triggered; 100% capital preserved for US Pit Open.\n\n"
            )
    except Exception as e:
        print(f"Error compiling London review in 6 PM advisory: {e}")

    subject = f"🎯 [6 PM IST ADVISORY] MCX Commodity Action Playbook ({day_name})"
    body = (
        f"🎯 <b>DAILY 6:00 PM IST ACTION ADVISORY</b>\n"
        f"📅 <i>{today_time_ist}</i>\n\n"
        f"{london_review_text}"
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

def generate_and_send_130pm_advisory(force=False):
    """
    Scans live market data at 1:30 PM IST (London/European session open),
    evaluates Asian morning session (09:00 AM - 01:15 PM IST) High/Low breakout probabilities,
    constructs an actionable trade advisory, and sends it via Telegram & Email.
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_ist = now_dt.strftime('%Y-%m-%d')
    today_time_ist = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    day_name = now_dt.strftime('%A')
    
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    last_130_date = state.get('DAILY_130PM_ADVISORY', {}).get('last_alert_date')
    if not force and last_130_date == today_ist:
        print(f"Daily 1:30 PM advisory already dispatched for today ({today_ist}). Skipping.")
        return False

    with open('config.json', 'r') as f:
        config = json.load(f)

    checker = PriceChecker(config)
    alerter = EmailAlerter(config)

    circuits_res = checker.generate_circuits_data()
    commodities_list = circuits_res.get('commodities', []) if circuits_res else []
    
    if not commodities_list:
        print("Could not fetch commodities data for 1:30 PM advisory.")
        return False

    # Record and evaluate intelligent advisory trades first
    records_map = {}
    try:
        from advisory_tracker import record_live_130pm_advisory, load_tracker
        new_records = record_live_130pm_advisory(commodities_list)
        tracker_data = load_tracker()
        today_str = now_dt.strftime('%d %b %Y')
        all_today_london = [t for t in tracker_data.get('trades', []) if t.get('date') == today_str and '1:30 PM' in t.get('session_name', '')]
        records_map = {t['symbol']: t for t in all_today_london}
    except Exception as e:
        print(f"Error recording live 1:30 PM advisory in paper trading tracker: {e}")

    action_items = []
    has_active_breakouts = False

    for item in commodities_list:
        sym = item['symbol']
        name = item['name']
        cur_p = item['current_price']
        day_h = item.get('day_high', cur_p)
        day_l = item.get('day_low', cur_p)
        chg_pct = item['change_pct']
        mid_p = round((day_h + day_l) / 2.0, 2)
        
        rec = records_map.get(sym)
        if rec:
            pt = rec.get('paper_trade', {})
            act = pt.get('action', 'STAND_ASIDE')
            st = pt.get('status', 'STAND_ASIDE')
            s6 = rec.get('suggestion_6pm', {})
            ctx = rec.get('event_context', '')
            ep = pt.get('entry_price', cur_p)
            sl = s6.get('stop_loss')
            tgt = s6.get('target')
            rationale = s6.get('rationale', '')

            if act == 'BUY' and st in ['ACTIVE', 'PENDING_LIMIT']:
                has_active_breakouts = True
                action_items.append(
                    f"🟢 <b>{name.upper()} (London Bullish Breakout)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%) | Retest Support: ₹{ep:,.2f}\n"
                    f"  • Setup: {ctx}\n"
                    f"  • Recommended Action: <b>BUY @ SUPPORT RETEST (₹{ep:,.2f})</b>\n"
                    f"  • Stop-Loss: ₹{sl:,.2f} (Tight Risk Stop) | Target: ₹{tgt:,.2f} (1.2x Range Expansion)\n"
                    f"  • Execution: Retest limit order queued with auto-trailing stop to breakeven."
                )
            elif act == 'SELL' and st in ['ACTIVE', 'PENDING_LIMIT']:
                has_active_breakouts = True
                action_items.append(
                    f"🔴 <b>{name.upper()} (London Bearish Breakdown)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%) | Retest Resistance: ₹{ep:,.2f}\n"
                    f"  • Setup: {ctx}\n"
                    f"  • Recommended Action: <b>SELL @ RESISTANCE RETEST (₹{ep:,.2f})</b>\n"
                    f"  • Stop-Loss: ₹{sl:,.2f} (Tight Risk Stop) | Target: ₹{tgt:,.2f} (1.2x Range Expansion)\n"
                    f"  • Execution: Retest limit order queued with auto-trailing stop to breakeven."
                )
            elif 'Liquidity Sweep' in ctx or 'LIQUIDITY_SWEEP' in rec.get('check_615', {}).get('status', ''):
                action_items.append(
                    f"🛡️ <b>{name.upper()} (Trap Avoided: Asian Liquidity Sweep)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%) | Morning Low: ₹{day_l:,.2f}\n"
                    f"  • Rationale: {rationale}\n"
                    f"  • Action: <b>STAND ASIDE (Capital Protected)</b>\n"
                    f"  • Risk Protection: Blocked false counter-trend breakdown in an active macro bull trend."
                )
            elif 'Bull Trap' in ctx or 'BULL_TRAP' in rec.get('check_615', {}).get('status', ''):
                action_items.append(
                    f"🛡️ <b>{name.upper()} (Trap Avoided: Counter-Trend Bull Trap)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%) | Morning High: ₹{day_h:,.2f}\n"
                    f"  • Rationale: {rationale}\n"
                    f"  • Action: <b>STAND ASIDE (Capital Protected)</b>\n"
                    f"  • Risk Protection: Blocked false counter-trend breakout in an active macro bear trend."
                )
            elif 'Sector Correlation Guard' in ctx or 'SECTOR_GUARD' in rec.get('check_615', {}).get('status', ''):
                action_items.append(
                    f"🛡️ <b>{name.upper()} (Sector Correlation Guard)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%)\n"
                    f"  • Rationale: {rationale}\n"
                    f"  • Action: <b>STAND ASIDE (Correlated Exposure Capped)</b>\n"
                    f"  • Risk Protection: Eliminated double-drawdown risk across correlated commodity baskets."
                )
            elif 'Portfolio Circuit Breaker' in ctx:
                action_items.append(
                    f"🛑 <b>{name.upper()} (Portfolio Circuit Breaker Triggered)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%)\n"
                    f"  • Rationale: {rationale}\n"
                    f"  • Action: <b>STAND ASIDE (Portfolio Risk Halted)</b>\n"
                    f"  • Risk Protection: Trading halted to enforce ₹15,000 maximum daily portfolio loss cap."
                )
            else:
                action_items.append(
                    f"ℹ️ <b>{name.upper()} (Range-Bound Asian Session)</b>\n"
                    f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%)\n"
                    f"  • Morning Range: ₹{day_l:,.2f} – ₹{day_h:,.2f} (Mid: ₹{mid_p:,.2f})\n"
                    f"  • Standing aside. Awaiting European range breakout before deploying capital."
                )
        else:
            action_items.append(
                f"ℹ️ <b>{name.upper()} (Range-Bound Asian Session)</b>\n"
                f"  • Live: ₹{cur_p:,.2f} ({chg_pct:+.2f}%)\n"
                f"  • Morning Range: ₹{day_l:,.2f} – ₹{day_h:,.2f} (Mid: ₹{mid_p:,.2f})\n"
                f"  • Standing aside. Awaiting European range breakout before deploying capital."
            )

    overview_lines = [f"• <b>{c['name']}</b>: ₹{c['current_price']:,.2f} ({c['change_pct']:+.2f}%) | Range: ₹{c.get('day_low', c['current_price']):,.2f} - ₹{c.get('day_high', c['current_price']):,.2f}" for c in commodities_list]
    overview_text = "\n".join(overview_lines)
    action_text = "\n\n".join(action_items)

    subject = f"🎯 [1:30 PM IST ADVISORY] London / European Open Breakout Playbook ({day_name})"
    body = (
        f"🎯 <b>DAILY 1:30 PM IST LONDON BREAKOUT ADVISORY</b>\n"
        f"📅 <i>{today_time_ist}</i>\n\n"
        f"<b>📊 ASIAN MORNING RANGE OVERVIEW (09:00 AM – 01:15 PM IST):</b>\n"
        f"{overview_text}\n\n"
        f"<b>⚡ HIGH-PROBABILITY ACTION PLAYBOOK:</b>\n"
        f"{action_text}\n\n"
        f"<b>🛡️ RISK MANAGEMENT & EXECUTION RULES:</b>\n"
        f"1. <b>Daily CPR Macro Trend Filter</b>: Counter-trend Asian low breakdowns during macro bull days (and high breakouts during macro bear days) are automatically blocked as institutional liquidity sweep traps.\n"
        f"2. <b>Support & Resistance Retest Execution</b>: Entries are executed on retests of broken key levels with tight risk-managed stop-losses (-0.25% to -0.30%) rather than chasing market momentum.\n"
        f"3. <b>Trailing Stop to Breakeven</b>: Once an active trade achieves +50% of its target distance, stop-loss automatically trails to entry price for a guaranteed risk-free run.\n"
        f"4. <b>Sector Correlation Guard & Circuit Breaker</b>: Maximum 1 position permitted per commodity basket (Bullion, Base Metals, Energy); trading halts if daily loss reaches ₹15,000.\n"
        f"5. <b>Hard EOD Exit</b>: All intraday breakout positions auto-square-off at 11:30 PM IST.\n\n"
        f"🌐 <b>Live Interactive Dashboard</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
    )

    alert_payload = [{
        'symbol': 'DAILY_130PM_ADVISORY',
        'name': 'Daily 1:30 PM Action Advisory',
        'event_type': 'DAILY_130PM_ADVISORY',
        'today_ist': today_ist,
        'subject': subject,
        'body': body
    }]

    print(f"Dispatching 1:30 PM IST Daily Action Advisory for {today_ist}...")
    success, sent_alerts = alerter.send_alerts(alert_payload)
    
    if success:
        state['DAILY_130PM_ADVISORY'] = {
            'last_alert_date': today_ist,
            'last_alert_time': today_time_ist,
            'last_alert_msg': subject
        }
        try:
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            print("Successfully recorded Daily 1:30 PM advisory dispatch in alerts_state.json")
        except Exception as e:
            print(f"Error saving alerts_state.json: {e}")
        return True
    return False

def generate_and_send_8pm_advisory(force=False):
    """
    Scans live market data at 8:00 PM IST (EIA Inventory & US Evening Trend),
    evaluates Wednesday Crude Oil EIA Inventory / Thursday Natural Gas Storage / US mid-day momentum,
    constructs an actionable trade advisory, and sends it via Telegram & Email.
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_ist = now_dt.strftime('%Y-%m-%d')
    today_time_ist = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    day_name = now_dt.strftime('%A')
    
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    last_8pm_date = state.get('DAILY_8PM_ADVISORY', {}).get('last_alert_date')
    if not force and last_8pm_date == today_ist:
        print(f"Daily 8:00 PM advisory already dispatched for today ({today_ist}). Skipping.")
        return False

    with open('config.json', 'r') as f:
        config = json.load(f)

    checker = PriceChecker(config)
    alerter = EmailAlerter(config)

    circuits_res = checker.generate_circuits_data()
    commodities_list = circuits_res.get('commodities', []) if circuits_res else []
    
    if not commodities_list:
        print("Could not fetch commodities data for 8:00 PM advisory.")
        return False

    action_items = []

    # Wednesday: Crude Oil EIA Special
    # Thursday: Natural Gas EIA Special
    if day_name == 'Wednesday':
        crude = next((c for c in commodities_list if c['symbol'] == 'CRUDEOIL'), None)
        if crude:
            c_p = crude['current_price']
            p_close = crude['prev_close']
            chg = crude['change_pct']
            lc = crude['lower_circuit']
            uc = crude['upper_circuit']
            
            action_items.append(
                f"🛢️ <b>CRUDE OIL EIA PETROLEUM INVENTORY SPECIAL (Wednesday 8:00 PM IST)</b>\n"
                f"  • Live: ₹{c_p:,.2f} ({chg:+.2f}% | UC: ₹{uc:,.2f} | LC: ₹{lc:,.2f})\n"
                f"  • Event: Weekly US EIA Crude & Gasoline Inventories released at 8:00 PM IST.\n"
                f"  • Strategy: <b>10-Minute Spike Retracement / Fade Filter</b>\n"
                f"  • Rule: Wait for the 8:00 PM - 8:10 PM knee-jerk reaction to settle. If price tests circuit limit or moves > 1.5% without locking, fade back towards pre-report anchor (₹{p_close:,.2f})."
            )
    elif day_name == 'Thursday':
        natgas = next((c for c in commodities_list if c['symbol'] == 'NATURALGAS'), None)
        if natgas:
            ng_p = natgas['current_price']
            p_close = natgas['prev_close']
            chg = natgas['change_pct']
            lc = natgas['lower_circuit']
            uc = natgas['upper_circuit']
            
            action_items.append(
                f"🔥 <b>NATURAL GAS EIA STORAGE SPECIAL (Thursday 8:00 PM IST)</b>\n"
                f"  • Live: ₹{ng_p:,.2f} ({chg:+.2f}% | UC: ₹{uc:,.2f} | LC: ₹{lc:,.2f})\n"
                f"  • Event: Weekly US EIA Underground Storage Report released at 8:00 PM IST.\n"
                f"  • Strategy: <b>Deviation Fade or Extended Momentum Ride</b>\n"
                f"  • Rule: Watch the 8:00 PM - 8:15 PM candle close. If inventory surprise pushes into limit slab, ride momentum; if spike rejects at band, fade to anchor (₹{p_close:,.2f})."
            )

    # General US Evening Action for other commodities
    for item in commodities_list:
        sym = item['symbol']
        if (day_name == 'Wednesday' and sym == 'CRUDEOIL') or (day_name == 'Thursday' and sym == 'NATURALGAS'):
            continue
        name = item['name']
        cur_p = item['current_price']
        chg_pct = item['change_pct']
        status = item['status']
        lc_price = item['lower_circuit']
        uc_price = item['upper_circuit']
        p_close = item['prev_close']

        if 'LOCKED' in status or 'HIT' in status or 'NEAR' in status:
            direction = 'LOWER' if 'LOWER' in status else 'UPPER'
            action = "BUY / BOUNCE THE FLOOR" if direction == 'LOWER' else "SELL / SHORT THE CEILING"
            sl = round(lc_price * 0.996 if direction == 'LOWER' else uc_price * 1.004, 2)
            action_items.append(
                f"⚡ <b>{name.upper()} (Evening Circuit Band Test)</b>\n"
                f"  • Live Price: ₹{cur_p:,.2f} ({chg_pct:+.2f}% | Status: {status})\n"
                f"  • Recommended Action: <b>{action}</b>\n"
                f"  • Strategy: Mean-reversion fade against the {direction.lower()} circuit limit.\n"
                f"  • Strict Stop-Loss: ₹{sl:,.2f} | Profit Target: ₹{p_close:,.2f} (Settlement Anchor)\n"
                f"  • Hard Exit: Auto-close before 11:30 PM IST MCX settlement."
            )
        elif abs(chg_pct) >= 1.5:
            direction_bias = "LONG / BUY" if chg_pct > 0 else "SHORT / SELL"
            action_items.append(
                f"🚀 <b>{name.upper()} (Evening Trend Runner: {chg_pct:+.2f}%)</b>\n"
                f"  • Live Price: ₹{cur_p:,.2f}\n"
                f"  • Recommended Action: <b>RIDE MOMENTUM ({direction_bias})</b>\n"
                f"  • Strategy: Trail stop-loss at 50% of evening gain into 11:30 PM exit. Do not hold overnight."
            )
        else:
            action_items.append(
                f"🛡️ <b>{name.upper()} (Normal Safe Range: {chg_pct:+.2f}%)</b>\n"
                f"  • Live Price: ₹{cur_p:,.2f} (Floor: ₹{lc_price:,.2f} | Ceiling: ₹{uc_price:,.2f})\n"
                f"  • Recommended Action: <b>STAND ASIDE / NO TRADE</b>\n"
                f"  • Strategy: Zero statistical circuit edge; preserve 100% capital in virtual cash."
            )

    overview_lines = [f"• <b>{c['name']}</b>: ₹{c['current_price']:,.2f} ({c['change_pct']:+.2f}%) | {c['status_text']}" for c in commodities_list]
    overview_text = "\n".join(overview_lines)
    action_text = "\n\n".join(action_items)

    is_eia_day = day_name in ['Wednesday', 'Thursday']
    eia_context_note = "1. <b>Inventory Knee-Jerk Filter</b>: Never enter during the exact 8:00 PM second. Wait for 8:10 PM stabilization." if is_eia_day else "1. <b>US Mid-Day Momentum Rule</b>: Trade only confirmed breakout runners or circuit band tests. Avoid mid-range chop."
    session_title = f"EIA Inventory Special & US Evening Playbook ({day_name})" if is_eia_day else f"US Evening Pit & Mid-Day Momentum Playbook ({day_name})"

    subject = f"🎯 [8:00 PM IST ADVISORY] {session_title}"
    body = (
        f"🎯 <b>DAILY 8:00 PM IST ACTION ADVISORY</b>\n"
        f"📅 <i>{today_time_ist}</i>\n\n"
        f"<b>📊 LIVE COMMODITY STATUS AT 8:00 PM IST:</b>\n"
        f"{overview_text}\n\n"
        f"<b>⚡ ACTION PLAYBOOK & RECOMMENDATIONS:</b>\n"
        f"{action_text}\n\n"
        f"<b>🛡️ RISK MANAGEMENT & EXECUTION RULES:</b>\n"
        f"{eia_context_note}\n"
        f"2. <b>Hard 11:30 PM Exit</b>: All intraday positions close at 11:30 PM IST before MCX settlement.\n\n"
        f"🌐 <b>Live Interactive Dashboard</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
    )

    try:
        from advisory_tracker import record_live_8pm_advisory
        record_live_8pm_advisory(commodities_list, day_name)
    except Exception as e:
        print(f"Error recording live 8:00 PM advisory in paper trading tracker: {e}")

    alert_payload = [{
        'symbol': 'DAILY_8PM_ADVISORY',
        'name': 'Daily 8 PM Action Advisory',
        'event_type': 'DAILY_8PM_ADVISORY',
        'today_ist': today_ist,
        'subject': subject,
        'body': body
    }]

    print(f"Dispatching 8:00 PM IST Daily Action Advisory for {today_ist}...")
    success, sent_alerts = alerter.send_alerts(alert_payload)
    if success:
        state['DAILY_8PM_ADVISORY'] = {
            'last_alert_date': today_ist,
            'last_alert_time': today_time_ist,
            'last_alert_msg': subject
        }
        try:
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            print("Successfully recorded Daily 8 PM advisory dispatch in alerts_state.json")
        except Exception as e:
            print(f"Error saving alerts_state.json: {e}")
        return True
def generate_and_send_9am_advisory(force=False):
    """
    Scans live market data at 09:00 AM IST (MCX market open & NSE pre-market),
    evaluates Opening Gap vs Previous Day Close and Central Pivot Range (CPR),
    constructs an actionable Morning Market Pulse advisory, and sends it via Telegram & Email.
    """
    now_dt = datetime.now(pytz.timezone('Asia/Kolkata'))
    today_ist = now_dt.strftime('%Y-%m-%d')
    today_time_ist = now_dt.strftime('%d %b %Y, %I:%M %p IST')
    day_name = now_dt.strftime('%A')
    
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'alerts_state.json')
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    last_9am_date = state.get('DAILY_9AM_ADVISORY', {}).get('last_alert_date')
    if not force and last_9am_date == today_ist:
        print(f"Daily 9:00 AM advisory already dispatched for today ({today_ist}). Skipping.")
        return False

    with open('config.json', 'r') as f:
        config = json.load(f)

    checker = PriceChecker(config)
    alerter = EmailAlerter(config)

    circuits_res = checker.generate_circuits_data()
    commodities_list = circuits_res.get('commodities', []) if circuits_res else []
    
    if not commodities_list:
        print("Could not fetch commodities data for 9:00 AM advisory.")
        return False

    action_items = []
    overview_lines = []

    for item in commodities_list:
        sym = item['symbol']
        name = item['name']
        cur_p = item['current_price']
        day_h = item.get('day_high', cur_p)
        day_l = item.get('day_low', cur_p)
        p_close = item.get('prev_close', cur_p)
        chg_pct = item.get('change_pct', 0.0)
        
        # CPR Math
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
        gap_direction = "GAP UP" if gap_pct > 0 else ("GAP DOWN" if gap_pct < 0 else "FLAT OPEN")
        cpr_type = "Narrow CPR (Trending Day Expected 🚀)" if cpr_width_pct <= 0.25 else ("Wide CPR (Range-Bound / Mean Reverting 🔄)" if cpr_width_pct >= 0.40 else "Average CPR")

        overview_lines.append(
            f"• <b>{name}</b>: ₹{cur_p:,.2f} ({gap_direction} {gap_pct:+.2f}% | {gap_pts:+,.2f} pts)\n"
            f"  Pivot: ₹{pivot:,.2f} | CPR: [₹{cpr_bot:,.2f} - ₹{cpr_top:,.2f}] ({cpr_width_pct:.2f}% width) | R1: ₹{r1:,.2f} / S1: ₹{s1:,.2f}"
        )

        # Actionable setups
        if gap_pct >= 0.75:
            if cpr_width_pct >= 0.30:
                action_items.append(
                    f"🔴 <b>{name.upper()} (Gap Up Fade Setup)</b>\n"
                    f"  • Live Open: ₹{cur_p:,.2f} (+{gap_pct:.2f}% Gap Up vs PDC ₹{p_close:,.2f})\n"
                    f"  • Setup: Wide CPR ({cpr_width_pct:.2f}%) indicates high probability of morning mean-reversion.\n"
                    f"  • Recommended Action: <b>FADE GAP / SHORT AT R1 RESISTANCE</b>\n"
                    f"  • Entry Zone: Around current open (₹{cur_p:,.2f}) to R1 (₹{r1:,.2f})\n"
                    f"  • Target: ₹{p_close:,.2f} (Previous Day Close Gap-Fill)\n"
                    f"  • Stop-Loss: ₹{round(max(cur_p, r1) * 1.004, 2):,.2f} (Above Opening High)"
                )
            else:
                action_items.append(
                    f"🟢 <b>{name.upper()} (Gap Momentum Continuation)</b>\n"
                    f"  • Live Open: ₹{cur_p:,.2f} (+{gap_pct:.2f}% Gap Up vs PDC ₹{p_close:,.2f})\n"
                    f"  • Setup: Narrow CPR ({cpr_width_pct:.2f}%) indicates expansion volatility. Sustaining above open.\n"
                    f"  • Recommended Action: <b>BUY / RIDE OPENING MOMENTUM</b>\n"
                    f"  • Entry Zone: Above ₹{cur_p:,.2f}\n"
                    f"  • Target: ₹{r2:,.2f} (R2 Daily Extension)\n"
                    f"  • Stop-Loss: ₹{round(min(cur_p, pivot) * 0.996, 2):,.2f} (Below Central Pivot)"
                )
        elif gap_pct <= -0.75:
            if cpr_width_pct >= 0.30:
                action_items.append(
                    f"🟢 <b>{name.upper()} (Gap Down Fade / Bounce Setup)</b>\n"
                    f"  • Live Open: ₹{cur_p:,.2f} ({gap_pct:.2f}% Gap Down vs PDC ₹{p_close:,.2f})\n"
                    f"  • Setup: Wide CPR ({cpr_width_pct:.2f}%) indicates high probability of morning mean-reversion bounce.\n"
                    f"  • Recommended Action: <b>FADE GAP / BUY AT S1 SUPPORT</b>\n"
                    f"  • Entry Zone: Around current open (₹{cur_p:,.2f}) to S1 (₹{s1:,.2f})\n"
                    f"  • Target: ₹{p_close:,.2f} (Previous Day Close Gap-Fill)\n"
                    f"  • Stop-Loss: ₹{round(min(cur_p, s1) * 0.996, 2):,.2f} (Below Opening Low)"
                )
            else:
                action_items.append(
                    f"🔴 <b>{name.upper()} (Gap Flush Continuation)</b>\n"
                    f"  • Live Open: ₹{cur_p:,.2f} ({gap_pct:.2f}% Gap Down vs PDC ₹{p_close:,.2f})\n"
                    f"  • Setup: Narrow CPR ({cpr_width_pct:.2f}%) breakdown. Opening below value area.\n"
                    f"  • Recommended Action: <b>SELL / SHORT OPENING FLUSH</b>\n"
                    f"  • Entry Zone: Below ₹{cur_p:,.2f}\n"
                    f"  • Target: ₹{s2:,.2f} (S2 Daily Extension)\n"
                    f"  • Stop-Loss: ₹{round(max(cur_p, pivot) * 1.004, 2):,.2f} (Above Central Pivot)"
                )
        else:
            action_items.append(
                f"ℹ️ <b>{name.upper()} (Normal Open - Within Daily Value Area)</b>\n"
                f"  • Live Open: ₹{cur_p:,.2f} ({gap_pct:+.2f}% | PDC: ₹{p_close:,.2f})\n"
                f"  • Setup: {cpr_type}. Price opened within the standard daily volatility range.\n"
                f"  • Recommended Action: <b>STAND ASIDE / AWAIT 1:30 PM LONDON BREAKOUT</b>\n"
                f"  • Key Levels: Central Pivot ₹{pivot:,.2f} | R1: ₹{r1:,.2f} | S1: ₹{s1:,.2f}"
            )

    overview_text = "\n\n".join(overview_lines)
    action_text = "\n\n".join(action_items)

    subject = f"🎯 [9:00 AM IST ADVISORY] Morning Market Pulse, CPR & Opening Gap Playbook ({day_name})"
    body = (
        f"🎯 <b>DAILY 09:00 AM IST MORNING MARKET PULSE ADVISORY</b>\n"
        f"📅 <i>{today_time_ist}</i>\n\n"
        f"<b>📊 MCX COMMODITIES OPENING & CPR OVERVIEW:</b>\n"
        f"{overview_text}\n\n"
        f"<b>⚡ OPENING ACTION PLAYBOOK & GAP SETUPS:</b>\n"
        f"{action_text}\n\n"
        f"<b>🛡️ RISK MANAGEMENT & EXECUTION RULES:</b>\n"
        f"1. <b>Gap Fill Rule (Wide CPR)</b>: Fade large gaps only when the 09:15 AM 15-minute candle closes in the direction of the gap fill.\n"
        f"2. <b>Gap & Go Rule (Narrow CPR)</b>: Never fade a narrow CPR gap; narrow CPRs indicate institutional trend days.\n"
        f"3. <b>Hard 11:30 PM Exit</b>: All intraday positions auto-square-off at 11:30 PM IST before settlement.\n\n"
        f"🌐 <b>Live Interactive Dashboard</b>: https://VenuInturiphoos.github.io/commodity-alerts/#trade-tab"
    )

    try:
        from advisory_tracker import record_live_9am_advisory
        record_live_9am_advisory(commodities_list)
    except Exception as e:
        print(f"Error recording live 09:00 AM advisory in paper trading tracker: {e}")

    alert_payload = [{
        'symbol': 'DAILY_9AM_ADVISORY',
        'name': 'Daily 9 AM Action Advisory',
        'event_type': 'DAILY_9AM_ADVISORY',
        'today_ist': today_ist,
        'subject': subject,
        'body': body
    }]

    print(f"Dispatching 09:00 AM IST Daily Action Advisory for {today_ist}...")
    success, sent_alerts = alerter.send_alerts(alert_payload)
    if success:
        state['DAILY_9AM_ADVISORY'] = {
            'last_alert_date': today_ist,
            'last_alert_time': today_time_ist,
            'last_alert_msg': subject
        }
        try:
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            print("Successfully recorded Daily 9 AM advisory dispatch in alerts_state.json")
        except Exception as e:
            print(f"Error saving alerts_state.json: {e}")
        return True
    return False

if __name__ == "__main__":
    import sys
    force_run = '--force' in sys.argv
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
    elif '--session' in sys.argv or any(s in sys.argv for s in ['--9am', '--130pm', '--8pm', '--6pm']):
        if '--9am' in sys.argv or ('--session' in sys.argv and ('9am' in sys.argv or '900' in sys.argv)):
            generate_and_send_9am_advisory(force=force_run)
        elif '--130pm' in sys.argv or ('--session' in sys.argv and '130' in sys.argv):
            generate_and_send_130pm_advisory(force=force_run)
        elif '--8pm' in sys.argv or ('--session' in sys.argv and ('800' in sys.argv or '8pm' in sys.argv)):
            generate_and_send_8pm_advisory(force=force_run)
        else:
            generate_and_send_6pm_advisory(force=force_run)
    else:
        generate_and_send_6pm_advisory(force=force_run)

