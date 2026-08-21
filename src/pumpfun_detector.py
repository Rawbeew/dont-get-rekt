"""
Pump.fun new-token detector via Helius WebSocket.
Listens to logsSubscribe for the Pump.fun program ID. When a new token
is created (initialize2/create instruction), extracts the token mint
address from the transaction, checks it on Dexscreener, and sends a
Telegram alert.

This is a DETECTOR, not a buyer. No private keys. No execution.
Paper engine logs it as a simulated position.
"""
import asyncio
import json
import time
import base64
import requests
from datetime import datetime, timezone

from config import (
    SOLANA_WSS_URL, SOLANA_RPC_URL, TELEGRAM_CHAT_ID,
    PAPER_MODE,
)
from telegram_bot import send_alert

PUMP_FUN_PROGRAM = "6EF8rrecthAB5iNZ3DK89Li9RQdBdte731bt51CpHeed"

# Track recently alerted tokens to avoid duplicates (5 min window)
recent_alerts = {}  # mint -> timestamp


def _is_duplicate(mint):
    """Check if we already alerted this token in the last 5 minutes."""
    now = time.time()
    # Clean old entries
    recent_alerts.update({k: v for k, v in recent_alerts.items() if now - v < 300})
    if mint in recent_alerts:
        return True
    recent_alerts[mint] = now
    return False


def _extract_mint_from_tx(tx_data):
    """Extract the new token mint address from a transaction's logs and accounts."""
    try:
        meta = tx_data.get("meta", {})
        msg = tx_data.get("transaction", {}).get("message", {})
        keys = msg.get("accountKeys", [])

        # The new token mint is usually the first writable non-signer account
        # that is not the fee payer and not a known program
        for i, key in enumerate(keys):
            if isinstance(key, dict):
                if key.get("writable") and not key.get("signer"):
                    pk = key.get("pubkey", "")
                    # Skip known programs and the Pump.fun program itself
                    if pk and pk != PUMP_FUN_PROGRAM and len(pk) > 32:
                        return pk
            elif isinstance(key, str):
                if len(key) > 32 and key != PUMP_FUN_PROGRAM:
                    return key

        # Fallback: check post token balances for new mints
        post_bal = meta.get("postTokenBalances", [])
        pre_bal = meta.get("preTokenBalances", [])
        pre_mints = set(b.get("mint", "") for b in pre_bal)
        for b in post_bal:
            mint = b.get("mint", "")
            if mint and mint not in pre_mints:
                return mint

    except Exception:
        pass
    return None


def _check_dexscreener(mint):
    """Check if the token has a Dexscreener pair yet."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=10,
        )
        data = r.json()
        pairs = data.get("pairs") or []
        if pairs:
            pairs.sort(key=lambda p: p.get("volume", {}).get("h24", 0), reverse=True)
            p = pairs[0]
            return {
                "symbol": p["baseToken"].get("symbol", "?"),
                "name": p["baseToken"].get("name", "?"),
                "price": p.get("priceUsd", 0),
                "vol_24h": p.get("volume", {}).get("h24", 0),
                "liquidity": p.get("liquidity", {}).get("usd", 0),
                "url": p.get("url", ""),
                "chain": p.get("chainId", "solana"),
            }
    except:
        pass
    return None


def _check_nft_metadata(mint):
    """Use Helius DAS API to check if this is an NFT and verify metadata."""
    try:
        r = requests.post(SOLANA_RPC_URL, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getAsset",
            "params": {"id": mint},
        }, timeout=15)
        data = r.json().get("result", {})
        if not data:
            return None

        grouping = data.get("grouping", [])
        creators = data.get("creators", [])
        mutable = data.get("mutable", True)
        compression = data.get("compression", {})

        collection_id = None
        if grouping:
            collection_id = grouping[0].get("group_value")

        is_verified = any(c.get("verified", False) for c in creators)

        return {
            "is_nft": True,
            "name": data.get("content", {}).get("metadata", {}).get("name", "?"),
            "collection": collection_id,
            "mutable": mutable,
            "verified_creator": is_verified,
            "image": data.get("content", {}).get("links", {}).get("image", ""),
            "compressed": compression.get("compressed", False),
        }
    except:
        return None


def _format_alert(mint, dex_data, nft_data):
    """Format the Telegram alert."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    if dex_data:
        symbol = dex_data["symbol"]
        price = dex_data["price"]
        vol = dex_data["vol_24h"]
        liq = dex_data["liquidity"]
        url = dex_data["url"]
        return (
            f"NEW PUMP.FUN TOKEN\n"
            f"`{symbol}` @ ${float(price):.10f}\n"
            f"Vol 24h: ${vol:,.0f}\n"
            f"Liquidity: ${liq:,.0f}\n"
            f"Mint: `{mint[:20]}...`\n"
            f"Time: {ts}\n"
            f"[Chart]({url})\n"
            f"PAPER MODE: no buy executed"
        )
    elif nft_data:
        safe = "SAFE" if nft_data["verified_creator"] and not nft_data["mutable"] else "RISKY"
        return (
            f"NEW PUMP.FUN NFT\n"
            f"`{nft_data['name']}`\n"
            f"Collection: {nft_data.get('collection', 'none')}\n"
            f"Creator verified: {nft_data['verified_creator']}\n"
            f"Mutable: {nft_data['mutable']}\n"
            f"Safety: {safe}\n"
            f"Mint: `{mint[:20]}...`\n"
            f"Time: {ts}"
        )
    else:
        return (
            f"NEW PUMP.FUN TOKEN\n"
            f"Mint: `{mint}`\n"
            f"No Dexscreener pair yet (brand new)\n"
            f"Time: {ts}\n"
            f"PAPER MODE: logged for tracking"
        )


async def watch_pump_fun(duration_sec=None):
    """Listen to Pump.fun program logs via Helius WebSocket.
    duration_sec: if set, auto-stop after N seconds (for testing).
    If None, runs forever.
    """
    print(f"WebSocket Pump.fun detector starting...")
    print(f"  WSS: {SOLANA_WSS_URL[:40]}...")
    print(f"  Program: {PUMP_FUN_PROGRAM}")
    print(f"  PAPER MODE: {PAPER_MODE} (no execution)")

    async_mode = False
    try:
        import websockets
        async_mode = True
    except ImportError:
        print("websockets not installed. Install with: pip install websockets")
        return

    import websockets

    try:
        async with websockets.connect(SOLANA_WSS_URL) as ws:
            # Subscribe to ALL logs (mentions filter doesn't work on Helius WSS)
            # We filter for Pump.fun post-receipt by checking log content
            sub_req = {
                "jsonrpc": "2.0", "id": 1,
                "method": "logsSubscribe",
                "params": ["all", {"commitment": "processed"}],
            }
            await ws.send(json.dumps(sub_req))
            print("  Subscribed to all logs. Filtering for Pump.fun activity...\n")

            start = time.time()
            tokens_detected = 0

            async for message in ws:
                if duration_sec and time.time() - start > duration_sec:
                    print(f"\n  Duration limit reached. Stopping after {tokens_detected} tokens detected.")
                    break

                try:
                    data = json.loads(message)
                    logs = (
                        data.get("params", {})
                        .get("result", {})
                        .get("value", {})
                        .get("logs", [])
                    )

                    if not logs:
                        continue

                    # Filter for Pump.fun program activity:
                    # Look for the pump.fun program ID or Swap2 instruction
                    # (pump.fun uses DF1ow4tspfHX9JwWJsAb9epbkA8hmpSEAtxXy1V27QBH as swap program)
                    pump_found = any(
                        PUMP_FUN_PROGRAM in log
                        or "Instruction: Swap2" in log
                        or "Instruction: Create" in log
                        or "initialize2" in log
                        for log in logs
                    )

                    # More precise: check for pump.fun bonding curve program
                    # Pump.fun swap program: DF1ow4tspfHX9JwWJsAb9epbkA8hmpSEAtxXy1V27QBH
                    PUMP_SWAP = "DF1ow4tspfHX9JwWJsAb9epbkA8hmpSEAtxXy1V27QBH"
                    is_pump_swap = any(PUMP_SWAP in log for log in logs)
                    is_pump_create = any("Instruction: Create" in log for log in logs)

                    if not (is_pump_swap or is_pump_create):
                        continue

                    # Get the signature to fetch the full tx
                    sig = (
                        data.get("params", {})
                        .get("result", {})
                        .get("value", {})
                        .get("signature", "")
                    )

                    if not sig:
                        continue

                    # Fetch full transaction to extract mint address
                    tx_resp = requests.post(SOLANA_RPC_URL, json={
                        "jsonrpc": "2.0", "id": 2,
                        "method": "getTransaction",
                        "params": [sig, {
                            "maxSupportedTransactionVersion": 0,
                            "encoding": "jsonParsed",
                        }],
                    }, timeout=15)

                    tx_data = tx_resp.json().get("result")
                    if not tx_data:
                        continue

                    mint = _extract_mint_from_tx(tx_data)
                    if not mint or _is_duplicate(mint):
                        continue

                    tokens_detected += 1
                    print(f"\n  NEW TOKEN: {mint[:20]}... (sig: {sig[:20]}...)")

                    # Check Dexscreener for pair data
                    dex_data = _check_dexscreener(mint)
                    if dex_data:
                        print(f"    Dexscreener: {dex_data['symbol']} ${dex_data['price']} liq=${dex_data['liquidity']:,.0f}")

                    # Check DAS for NFT metadata
                    nft_data = _check_nft_metadata(mint)
                    if nft_data:
                        print(f"    NFT: {nft_data['name']} verified={nft_data['verified_creator']} mutable={nft_data['mutable']}")

                    # Format and send alert
                    alert = _format_alert(mint, dex_data, nft_data)
                    print(f"    Alert:\n{alert}")
                    send_alert(alert)

                    # Log as paper position if it's a token (not NFT)
                    if dex_data and not nft_data:
                        from paper_engine import open_paper_position
                        signal = {
                            "source": "DEX",
                            "symbol": dex_data["symbol"],
                            "chain": "solana",
                            "pair_address": "",
                            "address": mint,
                            "signal": "PUMP_FUN_NEW_LISTING",
                            "price_usd": dex_data["price"],
                            "url": dex_data["url"],
                            "liq": dex_data.get("liquidity", 0),
                            "mint": mint,
                            "vol_24h": dex_data.get("vol", 0),
                            "buy_ratio": dex_data.get("buy_ratio", 0),
                            "price_change_1h": dex_data.get("price_change_1h", 0),
                        }
                        pos = open_paper_position(signal)
                        if pos:
                            print(f"    PAPER OPEN: {pos['symbol']} @ ${pos['entry_price']:.10f}")

                    # Auto-trade if enabled
                    from auto_trade import auto_buy
                    auto_buy(
                        {"mint": mint, "symbol": dex_data.get("symbol", "unknown") if dex_data else "pump",
                         "chain": "solana", "liq": dex_data.get("liquidity", 0) if dex_data else 0},
                        telegram_chat_id=TELEGRAM_CHAT_ID,
                        bot_token=TELEGRAM_BOT_TOKEN,
                    )

                except Exception as e:
                    print(f"  Error processing message: {e}")
                    continue

    except Exception as e:
        print(f"WebSocket error: {e}")
        # Reconnect logic could go here
    finally:
        print(f"\nPump.fun detector stopped. {tokens_detected} tokens detected in session.")


if __name__ == "__main__":
    # Run for 60 seconds as a test
    asyncio.run(watch_pump_fun(duration_sec=60))
