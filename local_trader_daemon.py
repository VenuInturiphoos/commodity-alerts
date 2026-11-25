#!/usr/bin/env python3
"""
Local Autopilot Daemon for Commodity & Equity Intraday Trading
Runs continuously on the local host to bypass Dhan cloud IP whitelisting restrictions.
Monitors MCX & NSE technical setups, manages stops/targets, places Limit orders directly
on Dhan, and pushes state updates to GitHub.
"""

import os
import sys
import socket
import time
import signal
import subprocess
import fcntl
from datetime import datetime
import pytz
import urllib3.util.connection as urllib_conn

# =========================================================================
# MANDATORY SINGLETON PROCESS GUARD
# Prevents duplicate daemon processes from running concurrently, which causes
# race conditions, state clobbering, and duplicate email alert dispatch.
# =========================================================================
LOCK_FILE = '/tmp/commodity_trader_daemon.lock'
lock_fd = None

def acquire_singleton_lock():
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except (IOError, BlockingIOError):
        print(f"🛑 [SINGLETON GUARD] Another instance of local_trader_daemon is already active. Terminating duplicate process (PID {os.getpid()}) immediately.")
        sys.exit(0)

acquire_singleton_lock()

# Force IPv4 routing for Dhan API requests to strictly match whitelisted Primary Static IP
try:
    urllib_conn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv()

from main import load_config
from alerter import EmailAlerter
from price_checker import PriceChecker
from advisory_tracker import (
    check_and_execute_intraday_targets_and_stops,
    evaluate_realtime_technical_triggers
)
from dhan_live_executor import DhanLiveExecutor

IST = pytz.timezone("Asia/Kolkata")
RUNNING = True

def handle_sigterm(signum, frame):
    global RUNNING
    print(f"\n[DAEMON] Received signal {signum}. Shutting down gracefully...")
    RUNNING = False

signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)

def is_market_open():
    """
    Returns True if either NSE Equity (09:15-15:30) or MCX Commodity (09:00-23:30) is open.
    """
    now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5: # Saturday, Sunday
        return False
    
    # 09:00 AM to 11:30 PM (23:30) IST
    hour = now_ist.hour
    minute = now_ist.minute
    total_minutes = hour * 60 + minute
    
    market_open_min = 9 * 60        # 09:00 AM
    market_close_min = 23 * 60 + 35  # 11:35 PM
    
    return market_open_min <= total_minutes <= market_close_min

def git_sync_changes():
    """
    Syncs updated web tracker and circuit files to GitHub origin main.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "web/"],
            capture_output=True,
            text=True,
            check=True
        )
        if status.stdout.strip():
            print("[SYNC] Detected changes in web files. Pushing to GitHub...")
            subprocess.run(["git", "add", "web/"], check=True)
            subprocess.run(["git", "commit", "-m", "Auto-update trades and alerts via Local Daemon [skip ci]"], check=True)
            subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print("[SYNC] Successfully pushed to origin main.")
    except Exception as e:
        print(f"[SYNC ERROR] Git sync failed: {e}")

def run_daemon():
    print("=" * 65)
    print("🚀 Local Autopilot Trading Daemon Starting...")
    print(f"Time (IST): {datetime.now(IST).strftime('%Y-%m-%d %I:%M:%S %p')}")
    print(f"Dhan Client ID: {os.getenv('DHAN_CLIENT_ID', 'Not Set')[:4]}***")
    autopilot_on = os.getenv('DHAN_AUTOPILOT_ENABLED', 'false').lower() in ['true', '1', 'yes']
    print(f"Dhan Autopilot Enabled: {autopilot_on}")
    if not autopilot_on:
        print("🛡️ LIVE BROKER ORDERS DISABLED: System operating in 100% Paper Trading Mode.")
        print("   All trade signals, targets, and stops will be recorded and tracked virtually with ZERO real funds at risk.")
    print("=" * 65)
    
    config = load_config()
    alerter = EmailAlerter(config)
    checker = PriceChecker(config)
    dhan_exec = DhanLiveExecutor()
    
    # Test broker connectivity at startup
    try:
        funds = dhan_exec.get_fund_limits()
        if funds.get("status") == "success":
            print(f"✅ Dhan Broker Connected. Available Balance: ₹{funds.get('available_balance', 0.0):,.2f}")
        else:
            print(f"⚠️ Dhan get_fund_limits warning: {funds}")
    except Exception as e:
        print(f"❌ Dhan initialization error: {e}")

    iteration = 0
    while RUNNING:
        try:
            now_ist = datetime.now(IST)
            now_str = now_ist.strftime("%I:%M:%S %p")
            
            if not is_market_open():
                print(f"[{now_str}] Market is closed (Mon-Fri 09:00 - 23:35 IST). Sleeping for 5 minutes...")
                for _ in range(30):
                    if not RUNNING:
                        break
                    time.sleep(10)
                continue

            iteration += 1
            load_dotenv(override=True)
            try:
                import importlib
                import trader_engine_bridge
                import advisory_tracker
                import dhan_live_executor as dhan_mod
                importlib.reload(trader_engine_bridge)
                importlib.reload(advisory_tracker)
                importlib.reload(dhan_mod)
                check_and_execute_intraday_targets_and_stops = advisory_tracker.check_and_execute_intraday_targets_and_stops
                evaluate_realtime_technical_triggers = advisory_tracker.evaluate_realtime_technical_triggers
                dhan_exec = dhan_mod.DhanLiveExecutor()
            except Exception as e:
                pass
            print(f"\n--- [Cycle #{iteration} | {now_str} IST] Evaluating Trades & Triggers ---")
            
            # 1. Manage Active Positions (Targets, Trailing Stops, Structural Exits)
            check_and_execute_intraday_targets_and_stops(checker, alerter)
            
            # 2. Evaluate Real-Time Technical Triggers (Commodities & Intraday Stocks)
            evaluate_realtime_technical_triggers(checker, alerter)
            
            # 3. Clean up any stale limit orders on Dhan (Only when live autopilot is active)
            if os.getenv('DHAN_AUTOPILOT_ENABLED', 'false').lower() in ['true', '1', 'yes']:
                try:
                    stale_cancels = dhan_exec.check_and_cancel_stale_limit_orders(max_age_seconds=180)
                    if stale_cancels:
                        print(f"🧹 Stale limit orders cancelled on Dhan: {stale_cancels}")
                except Exception as e:
                    print(f"⚠️ Stale order cancel check error: {e}")
                
            # 4. Sync web tracking files to GitHub repository
            git_sync_changes()
            
        except Exception as e:
            print(f"❌ [Cycle Error]: {e}")
            import traceback
            traceback.print_exc()

        # Rapid 25-second cycle during market hours for fresh real-time ticks
        print(f"[{datetime.now(IST).strftime('%I:%M:%S %p')}] Cycle finished. Waiting 25s for next scan...")
        for _ in range(5):
            if not RUNNING:
                break
            time.sleep(5)

    print("\n🛑 Local Autopilot Trading Daemon stopped safely.")

if __name__ == "__main__":
    run_daemon()
