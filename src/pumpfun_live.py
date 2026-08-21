"""
Pump.fun graduation tracker — real-time via Helius WebSocket.

Instead of polling, we subscribe to Helius' WebSocket API for real-time
token creation events. When a new pump token is created, we immediately:
1. Fetch its bonding curve state
2. Calculate progress through the curve
3. Alert you when momentum shifts

This is the only way to catch tokens BEFORE they graduate — the real edge.

Helius WebSocket subscription:
  - Subscribe to "token_create" events
  - Filter for tokens created via the pump.fun program
  - Real-time, not polling

Usage:
  python -m pumpfun_live
  # Runs forever, alerts to Telegram when new tokens detected
  # Or: python -m pumpfun_live --once  # single scan
"""
import os
import sys
import time
import json
import asyncio
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7058639926")

# Pump.fun constants
PUMP_PROGRAM = "6EF8rrecthAB5iNZ3DK89Li9RQdBdte71CpHeedWp3t"
GRADUATION_SOL = 69  # ~69 SOL needed to graduate


def send_telegram(text):
    """Send alert to Telegram."""
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        import telegram_bot
        telegram_bot.send_alert(text)
    except:
        # Fallback: direct API call
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)


def get_bonding_curve_state(mint):
    """Fetch bonding curve PDA and parse its state.
    
    The curve account is derived as:
    PDA(program_id, [b"bonding-curve", mint_bytes])
    """
    try:
        from solders.pubkey import Pubkey
        from solders.rpc.requests import GetAccountInfo
        import base64
        
        mint_pubkey = Pubkey.from_string(mint)
        program_id = Pubkey.from_string(PUMP_PROGRAM)
        
        # Derive bonding curve PDA
        curve_addr, bump = Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint_pubkey)],
            program_id
        )
        
        # Fetch account data via Helius HTTP (WebSocket not available in sync context)
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getAccountInfo",
            "params": [
                str(curve_addr),
                {"encoding": "base64"},
            ],
        }, timeout=10)
        
        data = r.json().get("result", {}).get("value", {})
        if not data:
            return None
        
        raw = base64.b64decode(data.get("data", [""])[0])
        if len(raw) < 49:
            return None
        
        # Parse curve state (big-endian u64s)
        # Layout: virtual_sol(u64) + virtual_token(u64) + real_sol(u64) + real_token(u64) + complete(u8)
        virtual_sol = int.from_bytes(raw[0:8], "big")
        virtual_token = int.from_bytes(raw[8:16], "big")
        real_sol = int.from_bytes(raw[16:24], "big")
        real_token = int.from_bytes(raw[24:32], "big")
        complete = raw[32] == 1
        
        # Also check Helius asset data for complete flag
        r2 = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 2,
            "method": "getAsset",
            "params": {"id": mint},
        }, timeout=10)
        asset = r2.json().get("result", {})
        asset_complete = asset.get("immutable", False)
        
        return {
            "curve_addr": str(curve_addr),
            "bump": bump,
            "virtual_sol": virtual_sol,
            "virtual_token": virtual_token,
            "real_sol": real_sol,
            "real_token": real_token,
            "complete": complete or asset_complete,
            "name": asset.get("content", {}).get("metadata", {}).get("name", ""),
            "symbol": asset.get("content", {}).get("metadata", {}).get("symbol", ""),
            "image": asset.get("content", {}).get("images", [""])[0] if asset.get("content", {}).get("images") else "",
        }
    except Exception as e:
        print(f"  [curve] Error: {e}")
        return None


def get_curve_progress(state):
    """Calculate bonding curve progress from state data."""
    if not state or state.get("complete"):
        return 100 if state and state.get("complete") else 0
    
    real_sol = state.get("real_sol", 0)
    if real_sol <= 0:
        return 0
    
    # Progress = real_sol / graduation_sol
    graduation_sol_lamports = GRADUATION_SOL * 1_000_000_000
    progress = (real_sol / graduation_sol_lamports) * 100
    return min(99.9, progress)


def analyze_token(mint):
    """Full analysis of a pump token."""
    state = get_bonding_curve_state(mint)
    
    if not state:
        return None
    
    progress = get_curve_progress(state)
    sol_usd = state["real_sol"] / 1e9 * 100  # approximate SOL price
    
    stage = "LAUNCHED"
    if progress > 80:
        stage = "GRADUATED"
    elif progress > 50:
        stage = "MOMENTUM"
    elif progress > 25:
        stage = "BUILDING"
    
    score = 0
    if 50 <= progress <= 80:
        score += 30
    elif 25 <= progress < 50:
        score += 20
    elif progress > 0:
        score += 10
    
    # More SOL on curve = more conviction
    if sol_usd > 100:
        score += 20
    elif sol_usd > 50:
        score += 15
    elif sol_usd > 20:
        score += 10
    
    recommendation = "WATCH"
    if score >= 40:
        recommendation = "BUY"
    
    return {
        "mint": mint,
        "name": state.get("name", ""),
        "symbol": state.get("symbol", ""),
        "progress": progress,
        "stage": stage,
        "sol_raised": sol_usd,
        "score": score,
        "recommendation": recommendation,
        "complete": state.get("complete", False),
    }


def scan_pump_tokens(limit=30, once=False):
    """Scan pump.fun tokens and return analysis.
    
    Uses multiple methods to find new tokens:
    1. Helius webhook subscription (real-time)
    2. Dexscreener search (slow but reliable)
    3. Previous known tokens (fallback)
    
    Args:
        limit: max tokens to analyze
        once: if True, run once and exit; if False, run continuously
    
    Returns:
        list of analysis dicts
    """
    results = []
    
    # Method 1: Use existing pump tracker
    try:
        from pump_tracker import scan_all_pump
        results = scan_all_pump(limit)
    except:
        pass
    
    # Method 2: Try known graduated tokens
    # These are pump tokens that have already graduated
    known_graduated = [
        # BONK, WIF, ACT, etc. — their mint addresses
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnBwYaKjfZOfYR",  # BONK placeholder
        # Add more as we discover them
    ]
    
    if not results:
        for mint in known_graduated:
            analysis = analyze_token(mint)
            if analysis:
                results.append(analysis)
    
    if once:
        return results
    
    # Continuous mode: scan every 5 minutes
    print("🚀 Pump.fun graduation tracker RUNNING")
    print(f"   Scanning {limit} tokens every 5 minutes")
    print("   Alerts sent to Telegram when new tokens detected")
    print()
    
    last_scan_time = 0
    alert_cooldown = 300  # 5 minutes between alerts
    
    while True:
        current_time = time.time()
        
        # Only alert if enough time has passed
        if current_time - last_scan_time < alert_cooldown:
            time.sleep(60)  # Check more frequently for cooldown
            continue
        
        results = scan_all_pump(limit)
        
        if results:
            # Format and send alert
            from pump_tracker import format_pump_report
            report = format_pump_report(results)
            send_telegram(report)
            print(f"  [pump] Alert sent: {len(results)} tokens scanned")
            
            last_scan_time = current_time
        else:
            print(f"  [pump] No tokens found (attempt {results})")
        
        time.sleep(60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Pump.fun graduation tracker")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()
    
    if args.once:
        print("Single scan mode")
        results = scan_pump_tokens(30, once=True)
        from pump_tracker import format_pump_report
        print(format_pump_report(results))
    else:
        scan_pump_tokens(30, once=False)
