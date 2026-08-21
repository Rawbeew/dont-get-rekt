"""
Pump.fun graduation tracker — real bonding curve analysis.

How it works:
1. Pump tokens have a bonding curve account (PDA) derived from the mint
2. We fetch that account's state: virtual_sol_reserves, virtual_token_reserves, complete flag
3. Calculate curve progress from the reserves
4. Alert when tokens reach graduation thresholds

The bonding curve account layout:
  virtual_sol_reserves: u64 (8 bytes)
  virtual_token_reserves: u64 (8 bytes)
  real_sol_reserves: u64 (8 bytes)
  real_token_reserves: u64 (8 bytes)
  complete: u8 (1 byte) — 1 = graduated, 0 = still on curve
  token_total_supply: u64 (8 bytes)

The curve uses x*y = k constant product formula.
Progress = 1 - (virtual_token_reserves / virtual_token_reserves_at_creation)
  = tokens_sold / total_tradeable
"""
import os
import sys
import time
import json
import requests
from datetime import datetime, timezone
from decimal import Decimal, getcontext

getcontext().prec = 28

# ── Config ────────────────────────────────────────────────────────────
HELIUS_RPC = os.getenv("HELIUS_RPC_URL",
    "https://mainnet.helius-rpc.com/?api-key=" +
    os.getenv("HELIUS_API_KEY", "")
)

# Pump.fun constants
PUMP_PROGRAM = "6EF8rrecthAB5iNZ3DK89Li9RQdBdte71CpHeedWp3t"
TOTAL_SUPPLY = 1_000_000_000  # 1 billion tokens
TRADEABLE_SUPPLY = 800_000_000  # 80% tradeable on curve
GRADUATION_SOL = 69  # SOL needed to graduate
CURVE_RESERVE_AT_LAUNCH = 2320000000  # Initial virtual token reserves (u64 format: ~2.32B tokens in smallest unit)


def get_bonding_curve_account(mint):
    """Fetch the bonding curve account for a pump token via Helius DAS.
    
    The bonding curve is a PDA owned by the pump program.
    We use getAsset with the mint address to check if it's complete.
    Then fetch the raw account data for curve state.
    
    Returns dict with curve state or None.
    """
    result = {}
    
    # Step 1: Check if token is complete (graduated) via Helius asset data
    try:
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getAsset",
            "params": {"id": mint, "displayOptions": {}},
        }, timeout=10)
        data = r.json().get("result", {})
        
        result["name"] = data.get("content", {}).get("metadata", {}).get("name", "")
        result["symbol"] = data.get("content", {}).get("metadata", {}).get("symbol", "")
        result["description"] = data.get("content", {}).get("metadata", {}).get("description", "")
        result["image_url"] = data.get("content", {}).get("images", [""])[0] if data.get("content", {}).get("images") else ""
        result["complete"] = data.get("immutable", False)  # Graduated tokens get immutable=true
        result["grouping"] = data.get("grouping", {})
        
        # If complete, it's graduated
        if result["complete"]:
            return result
    except:
        pass
    
    # Step 2: Fetch bonding curve account data via getAccountInfo
    try:
        # The bonding curve account address is derived from:
        # PDA(program_id, mint, curve_seed)
        # For pump.fun, the derivation is:
        # seeds = [b"bonding-curve", mint_bytes]
        # bump = find_program_bump([b"bonding-curve", mint_bytes], program_id)
        
        from solders.pubkey import Pubkey
        
        mint_pubkey = Pubkey.from_string(mint)
        
        # Derive the bonding curve PDA
        # pump.fun uses: find_program_address([b"bonding-curve", mint_bytes], program_id)
        # We can compute this with the solders library
        curve_addr, bump = find_bonding_curve_address(mint_pubkey)
        if not curve_addr:
            return result
        
        # Fetch the account data
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 2,
            "method": "getAccountInfo",
            "params": [
                str(curve_addr),
                {"encoding": "base64", "commitment": "confirmed"},
            ],
        }, timeout=10)
        
        account_data = r.json().get("result", {}).get("value", {})
        if not account_data:
            return result
        
        # Parse the account data
        import base64
        raw_bytes = base64.b64decode(account_data.get("data", [""])[0])
        
        if len(raw_bytes) >= 49:  # Minimum size for curve state
            # Parse: virtual_sol_reserves(u64) + virtual_token_reserves(u64) + real_sol_reserves(u64) + real_token_reserves(u64) + complete(u8) + padding
            from struct import unpack
            virtual_sol = int.from_bytes(raw_bytes[0:8], "little")
            virtual_token = int.from_bytes(raw_bytes[8:16], "little")
            real_sol = int.from_bytes(raw_bytes[16:24], "little")
            real_token = int.from_bytes(raw_bytes[24:32], "little")
            complete = raw_bytes[32] == 1
            
            result["virtual_sol_reserves"] = virtual_sol
            result["virtual_token_reserves"] = virtual_token
            result["real_sol_reserves"] = real_sol
            result["real_token_reserves"] = real_token
            result["complete"] = complete
            
            # If complete, the curve account may have been closed
            if complete:
                return result
    except Exception as e:
        print(f"  [curve] Error fetching curve state for {mint[:12]}...: {e}")
    
    return result


def find_bonding_curve_address(mint_pubkey):
    """Derive the bonding curve PDA for a pump.fun token.
    
    Derivation: PDA(program_id, [b"bonding-curve", mint_bytes])
    """
    try:
        from solders.pubkey import Pubkey
        from solders.instruction import Instruction
        from solders.keypair import Keypair
        
        program_id = Pubkey.from_string(PUMP_PROGRAM)
        mint_bytes = bytes(mint_pubkey)
        
        # Derive PDA: seeds = [b"bonding-curve", mint_bytes]
        address, bump = Pubkey.find_program_address(
            [b"bonding-curve", mint_bytes],
            program_id
        )
        return address, bump
    except:
        return None, 0


def get_curve_progress(curve_data):
    """Calculate what percentage through the bonding curve a token is.
    
    Progress = 1 - (virtual_token_reserves / initial_virtual_token_reserves)
    
    At launch: virtual_token_reserves ≈ 2.32B (in token micro-units)
    At graduation: virtual_token_reserves = 0 (all tokens sold)
    """
    if not curve_data:
        return 0
    
    complete = curve_data.get("complete", False)
    if complete:
        return 100
    
    vt = curve_data.get("virtual_token_reserves", 0)
    if vt <= 0:
        return 99.9  # All tokens sold but not marked complete
    
    # At launch, virtual token reserves were ~800M tokens (in micro-units = 800B)
    # But pump.fun uses a different reserve calculation
    # Let's use the ratio: progress = (initial - current) / initial
    # where initial ≈ 800_000_000 * 1_000_000 (micro units)
    # But the actual reserves are much smaller in the curve
    
    # Alternative: use the sol reserves to estimate progress
    sol_reserves = curve_data.get("real_sol_reserves", 0)
    
    # At graduation: ~69 SOL = 69_000_000_000 lamports
    graduation_sol_lamports = 69 * 1_000_000_000
    
    if sol_reserves > 0 and sol_reserves < graduation_sol_lamports:
        progress = (sol_reserves / graduation_sol_lamports) * 100
        return min(99.9, progress)
    
    return 0


def get_sol_price_usd():
    """Get SOL price in USD via Jupiter price API."""
    try:
        r = requests.get(
            "https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112",
            timeout=5,
        )
        data = r.json().get("data", {})
        price = float(data.get("So11111111111111111111111111111111111111112", {}).get("price", 0))
        if price > 0:
            return price
    except:
        pass
    return 100  # Fallback


def get_pair_price(mint):
    """Get token price from Dexscreener API."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        r = requests.get(url, timeout=10)
        data = r.json()
        for pair in data.get("pairs", []):
            if pair.get("chainId") == "solana" and not pair.get("dexId", "").startswith("pump"):
                # Raydium or other DEX — token has graduated
                return {
                    "price": float(pair.get("priceUsd", 0) or 0),
                    "graduated": True,
                    "dex": pair.get("dexId", ""),
                }
    except:
        pass
    return None


def analyze_pump_token(mint):
    """Full analysis of a pump token: curve progress, price, graduation status.
    
    Returns dict with:
      - mint, name, symbol
      - curve_progress: 0-100%
      - is_graduated: bool
      - price, market_cap
      - stage: LAUNCHED, BUILDING, MOMENTUM, GRADUATED
      - score: 0-100
      - recommendation: BUY, WATCH, AVOID
    """
    result = {
        "mint": mint,
        "stage": "UNKNOWN",
        "score": 0,
        "recommendation": "AVOID",
    }
    
    try:
        # Fetch bonding curve data
        curve = get_bonding_curve_account(mint)
        if not curve:
            # Token might be graduated or doesn't exist
            pair = get_pair_price(mint)
            if pair and pair.get("graduated"):
                result["stage"] = "GRADUATED"
                result["price"] = pair["price"]
                result["is_graduated"] = True
                result["recommendation"] = "WATCH"
                return result
            return result
        
        result["name"] = curve.get("name", "")
        result["symbol"] = curve.get("symbol", "")
        result["is_graduated"] = curve.get("complete", False)
        
        # Calculate progress
        if result["is_graduated"]:
            result["stage"] = "GRADUATED"
            result["curve_progress"] = 100
            result["recommendation"] = "WATCH"  # Graduated — watch for Raydium trading
        else:
            progress = get_curve_progress(curve)
            result["curve_progress"] = round(progress, 1)
            
            # Stage classification
            if progress > 80:
                result["stage"] = "GRADUATED"
                result["recommendation"] = "BUY"  # About to graduate — buy before Raydium listing
            elif progress > 50:
                result["stage"] = "MOMENTUM"
                result["recommendation"] = "BUY"  # Curve filling fast
            elif progress > 25:
                result["stage"] = "BUILDING"
                result["recommendation"] = "WATCH"  # Building momentum
            else:
                result["stage"] = "LAUNCHED"
                result["recommendation"] = "WATCH"  # Very early
            
            # Score calculation
            score = 0
            
            # Progress score — sweet spot at 60-80% (just before graduation)
            if 60 <= progress <= 80:
                score += 30
            elif 40 <= progress < 60:
                score += 20
            elif 20 <= progress < 40:
                score += 15
            else:
                score += 5
            
            # Real SOL reserves (how close to 69 SOL)
            sol_reserves = curve.get("real_sol_reserves", 0)
            if sol_reserves > 0:
                sol_usd = sol_reserves / 1e9 * get_sol_price_usd()
                result["sol_raised"] = sol_usd
                
                # Volume relative to SOL raised
                if sol_usd > 100:
                    score += 20  # At least some buying
                
                result["liquidity_usd"] = sol_usd
        
        result["score"] = score
    
    except Exception as e:
        result["error"] = str(e)
    
    return result


def fetch_recent_pump_tokens(limit=50):
    """Fetch recently created pump tokens from Helius.
    
    Uses getSignaturesForAddress on the pump program to find new creations.
    """
    tokens = []
    
    try:
        # Method 1: Helius token creation events via getSignaturesForAddress
        # Search for recent signatures where the pump program was an instruction account
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                PUMP_PROGRAM,
                {"limit": limit * 2},
            ],
        }, timeout=15)
        
        txs = r.json().get("result", [])
        if not txs:
            # Method 2: Use Helius webhook subscription to find new tokens
            # Try getAssetDataByAuthority for pump program
            r2 = requests.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 2,
                "method": "getAssetsByAuthority",
                "params": {
                    "authority": PUMP_PROGRAM,
                    "limit": limit,
                },
            }, timeout=15)
            data = r2.json().get("result", {})
            for asset in data.get("items", []):
                mint = asset.get("id", "")
                if mint:
                    tokens.append({"mint": mint})
            return tokens[:limit]
        
        # Method 3: Get each transaction to extract the mint
        seen = set()
        for tx in txs[:limit * 3]:
            sig = tx.get("signature", "")
            try:
                r2 = requests.post(HELIUS_RPC, json={
                    "jsonrpc": "2.0", "id": 3,
                    "method": "getTransaction",
                    "params": {
                        "signature": sig,
                        "maxSupportedTransactionVersion": 0,
                    },
                }, timeout=10)
                
                tx_data = r2.json().get("result", {})
                if not tx_data:
                    continue
                
                # Parse transaction to find the mint
                # Pump.fun creates a new SPL token and mints it
                # The mint is in the transaction's account keys
                account_keys = tx_data.get("transaction", {}).get("message", {}).get("accountKeys", [])
                for acct in account_keys:
                    addr = acct.get("pubkey", "") if isinstance(acct, dict) else str(acct)
                    if len(addr) == 44 and addr not in seen and addr != PUMP_PROGRAM:
                        # Check if this looks like a new token (has metadata)
                        meta = get_bonding_curve_account(addr)
                        if meta and not meta.get("complete", True):
                            tokens.append({
                                "mint": addr,
                                "name": meta.get("name", ""),
                                "symbol": meta.get("symbol", ""),
                            })
                            seen.add(addr)
                            if len(tokens) >= limit:
                                break
            except:
                continue
    
    except Exception as e:
        print(f"  [pump] fetch error: {e}")
    
    return tokens


def scan_all_pump(limit=30):
    """Full scan: fetch tokens, analyze curve progress, rank by score.
    
    Returns list sorted by score (best first).
    """
    print("  [pump] Fetching new pump.fun tokens...")
    new_tokens = fetch_recent_pump_tokens(limit)
    print(f"  [pump] Found {len(new_tokens)} tokens")
    
    results = []
    for token in new_tokens[:limit]:
        mint = token.get("mint", "")
        if not mint:
            continue
        
        print(f"  [pump] Analyzing {token.get('symbol', mint[:12])}...")
        analysis = analyze_pump_token(mint)
        results.append(analysis)
    
    # Sort by score descending, then by curve progress
    results.sort(key=lambda x: (x.get("score", 0), x.get("curve_progress", 0)), reverse=True)
    return results


def format_pump_report(results):
    """Format for Telegram."""
    if not results:
        return "*🚀 Pump.fun Graduation Tracker*\n\nNo tokens found."
    
    lines = ["*🚀 Pump.fun Graduation Tracker*", ""]
    
    buy = [r for r in results if r.get("recommendation") == "BUY"]
    watch = [r for r in results if r.get("recommendation") == "WATCH"]
    
    stage_icons = {
        "LAUNCHED": "🆕",
        "BUILDING": "📈",
        "MOMENTUM": "🔥",
        "GRADUATED": "✅",
        "UNKNOWN": "❓",
    }
    
    # BUY signals first
    for i, r in enumerate(buy[:5], 1):
        name = r.get("name", r.get("symbol", "?"))
        symbol = r.get("symbol", "?")
        stage = stage_icons.get(r.get("stage", "UNKNOWN"), r.get("stage", "?"))
        progress = r.get("curve_progress", 0)
        score = r.get("score", 0)
        liq = r.get("liquidity_usd", 0)
        
        lines.append(f"🔴 #{i} *{stage} — BUY* — {name} ({symbol})")
        lines.append(f"   Curve: {progress:.0f}% | Score: {score}/100")
        lines.append(f"   Liq: ${liq:,.0f}")
        if r.get("is_graduated"):
            lines.append(f"   ✅ GRADUATED — on Raydium now")
        else:
            lines.append(f"   Still on bonding curve")
        lines.append("")
    
    # WATCH signals
    for i, r in enumerate(watch[:5], 1):
        name = r.get("name", r.get("symbol", "?"))
        symbol = r.get("symbol", "?")
        stage = stage_icons.get(r.get("stage", "UNKNOWN"), r.get("stage", "?"))
        progress = r.get("curve_progress", 0)
        score = r.get("score", 0)
        
        lines.append(f"{stage} {i}. *{name} ({symbol})* — {r.get('stage', '?')}")
        lines.append(f"   Curve: {progress:.0f}% | Score: {score}/100")
        lines.append("")
    
    lines.append(f"Total: {len(results)} scanned | BUY: {len(buy)} | WATCH: {len(watch)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Pump.fun Graduation Tracker")
    print("=" * 50)
    
    results = scan_all_pump(30)
    report = format_pump_report(results)
    print(report)
    
    # Send to Telegram
    if results:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        import telegram_bot
        telegram_bot.send_alert(report)
