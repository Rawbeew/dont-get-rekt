"""
Auto-trade engine — runs on detected signals, executes via trading.py.

Flow:
1. Signal detected (DEX, Pump.fun, Raydium)
2. Safety check: mint auth revoked, freeze disabled, liq > $10k
3. If safety passes -> buy with configured amount
4. Start auto-sell timer: sell at 4h or manual /sell
5. Notify user via Telegram

Configuration (from .env):
- HERMES_WALLET_PRIVATE_KEY: cold wallet key (B58 encoded)
- AUTO_TRADE_AMOUNT_SOL: SOL per trade (default 0.1)
- AUTO_TRADE_ENABLED: "true" or "false"
- MAX_TRADES_PER_HOUR: 5
"""
import os
import time
import json
import threading
from datetime import datetime, timezone

from config import STATE_DIR, TELEGRAM_CHAT_ID
from buy_engine import check_safety
from llm_reasoner import should_buy
from exchange import swap_any, auto_sell, WALLET_PRIVATE_KEY

AUTO_TRADE_ENABLED = os.getenv("HERMES_AUTO_TRADE", "true").lower() == "true"
WALLET_PRIVATE_KEY = os.getenv("HERMES_WALLET_PRIVATE_KEY", "")
TRADE_AMOUNT_SOL = float(os.getenv("HERMES_TRADE_AMOUNT_SOL", "0.1"))
MAX_TRADES_PER_HOUR = int(os.getenv("HERMES_MAX_TRADES_PER_HOUR", "8"))
MIN_LIQUIDITY = float(os.getenv("HERMES_MIN_LIQUIDITY", "12500"))

# Honeypot detection: track tokens that failed to sell
HONEYPOT_LIST_PATH = os.path.join(STATE_DIR, "honeypot_list.json")
ACTIVE_TRADES = os.path.join(STATE_DIR, "active_trades.json")
TRADE_RATE_LIMIT = os.path.join(STATE_DIR, "trade_rate.json")


def load_active_trades():
    if os.path.exists(ACTIVE_TRADES):
        with open(ACTIVE_TRADES) as f:
            return json.load(f)
    return {}


def save_active_trades(trades):
    with open(ACTIVE_TRADES, "w") as f:
        json.dump(trades, f, indent=2)


def get_trade_count_last_hour():
    now = time.time()
    if os.path.exists(TRADE_RATE_LIMIT):
        with open(TRADE_RATE_LIMIT) as f:
            timestamps = json.load(f)
        recent = [t for t in timestamps if now - t < 3600]
        return len(recent)
    return 0


def log_trade_count():
    now = time.time()
    timestamps = []
    if os.path.exists(TRADE_RATE_LIMIT):
        with open(TRADE_RATE_LIMIT) as f:
            timestamps = json.load(f)
    timestamps.append(now)
    timestamps = [t for t in timestamps if now - t < 7200]
    with open(TRADE_RATE_LIMIT, "w") as f:
        json.dump(timestamps, f)


def auto_buy(signal_data, telegram_chat_id="", bot_token=""):
    """Execute an auto-buy if conditions are met.
    
    signal_data: dict with token info from DEX signal
    telegram_chat_id: where to send alert
    bot_token: bot API key
    """
    if not AUTO_TRADE_ENABLED:
        print("  [autobuy] Disabled (HERMES_AUTO_TRADE=false)")
        return

    if get_trade_count_last_hour() >= MAX_TRADES_PER_HOUR:
        print(f"  [autobuy] Rate limit: {MAX_TRADES_PER_HOUR}/hr")
        return

    mint = signal_data.get("mint", signal_data.get("address", ""))
    symbol = signal_data.get("symbol", "unknown")
    chain = signal_data.get("chain", "solana")
    liq = float(signal_data.get("liq", signal_data.get("liquidity_usd", 0)))

    if not mint:
        return

    # Safety check
    print(f"  [autobuy] Safety check: {symbol} ({mint[:12]}...)")
    safety = check_safety(mint)

    if "ACTIVE" in safety.get("mint_authority", ""):
        print(f"  [autobuy] REJECTED: mint authority active")
        return

    if "ENABLED" in safety.get("freeze_authority", ""):
        print(f"  [autobuy] REJECTED: freeze authority enabled")
        return

    if liq < MIN_LIQUIDITY:
        print(f"  [autobuy] REJECTED: liq ${liq:,.0f} < ${MIN_LIQUIDITY:,.0f}")
        return

    # Honeypot detection: check if the token has transfer restrictions
    # Honeypots have sell_tax > 0 or transfer hooks that prevent selling
    print(f"  [autobuy] Checking honeypot status...")
    is_honeypot = _check_honeypot(mint)
    if is_honeypot:
        print(f"  [autobuy] REJECTED: honeypot detected")
        return

    # Freshness check: token must have recent buy activity (within 30 mins)
    # Dead tokens or rugs that already dumped get no buy pressure
    print(f"  [autobuy] Checking recency...")
    buy_ratio = signal_data.get("buy_ratio", 0)
    ch_1h = signal_data.get("price_change_1h", 0)
    
    # If buy ratio is very low and price is down significantly, likely dead
    if buy_ratio < 0.35 and ch_1h < -30:
        print(f"  [autobuy] REJECTED: low activity (buy_ratio {buy_ratio:.0%}, 1h {ch_1h:+.1f}%)")
        return
    
    # Check 1h volume relative to 24h — if 1h vol is < 5% of 24h, no recent activity
    vol_1h = signal_data.get("vol_1h", 0)
    vol_24h = signal_data.get("vol_24h", 0)
    if vol_1h > 0 and vol_24h > 0:
        ratio = vol_1h / vol_24h
        if ratio < 0.02:  # less than 2% of daily volume in last hour
            print(f"  [autobuy] REJECTED: stale activity (1h/24h vol ratio {ratio:.2f})")
            return

    # LLM reasoning layer — ask if the trade makes sense
    print(f"  [autobuy] LLM reasoning...")
    llm_result = should_buy(signal_data, safety)
    print(f"  [autobuy] LLM: {llm_result['action']} — {llm_result['reason'][:100]}")
    
    if llm_result["action"] == "skip":
        print(f"  [autobuy] LLM SKIPPED: {llm_result['reason'][:100]}")
        return

    # Execute buy — route to correct chain
    print(f"  [autobuy] EXECUTING: {TRADE_AMOUNT_SOL} SOL -> {symbol} ({chain})")
    try:
        result = swap_any(chain, mint, TRADE_AMOUNT_SOL)
        
        if result.get("status") != "success":
            print(f"  [autobuy] FAILED: {result.get('error', 'unknown')}")
            return

        log_trade_count()

        # Estimate token amount from amount spent and current price
        from trading import check_token_price
        current_price = check_token_price(mint)
        token_amount = int(TRADE_AMOUNT_SOL / current_price * 1e6) if current_price and current_price > 0 else 0
        
        # Record position
        trades = load_active_trades()
        trades[mint] = {
            "symbol": symbol,
            "chain": chain,
            "mint": mint,
            "entry_sol": TRADE_AMOUNT_SOL,
            "entry_price_sol": current_price if current_price else 0,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "tx_hash": result.get("tx_hash", ""),
            "signal": signal_data.get("signal", "signal"),
            "token_amount": token_amount,  # for auto-sell
            "sell_bag_sold": False,
            "moon_5x_sold": False,
            "moon_10x_sold": False,
            "trailing_triggered": False,
        }
        save_active_trades(trades)

        # Start auto-sell monitor
        t = threading.Thread(
            target=auto_sell_monitor,
            args=(mint, symbol, TRADE_AMOUNT_SOL),
            daemon=True,
        )
        t.start()

        # Alert user
        if telegram_chat_id:
            from telegram_bot import send_alert
            alert = (
                "*AUTO-BOUGHT " + symbol + "*\n"
                + "`" + mint[:20] + "..." + "`\n"
                + "Amount: " + str(TRADE_AMOUNT_SOL) + " SOL\n"
                + "TX: " + str(result.get('tx_hash', 'N/A')[:30]) + "...\n"
                + "Auto-sell: time-stop in 4h\n"
                + "Liq: $" + f"{liq:,.0f}"
            )
            try:
                send_alert(alert)
            except Exception:
                pass

        print(f"  [autobuy] DONE: {result.get('tx_hash', '')}")
    except Exception as e:
        print(f"  [autobuy] ERROR: {e}")


def auto_sell_monitor(mint, symbol, entry_sol):
    """Auto-sell with sell bag / moon bag strategy.
    
    Sell Bag: sell 50% at 2x (recovers principal)
    Moon Bag: hold for 5x, sell 25%, then 10x sell 25%, trailing stop on remainder
    """
    time_stop_seconds = 24 * 3600  # hold up to 24h
    last_alert = 0
    peak_price = 0
    trailing_triggered = False
    last_alert_time = 0

    print(f"  [autosell] Monitoring {symbol} (mint: {mint[:12]}...)")
    print(f"  [autosell] Strategy: sell_bag@2x, moon_bag@5x/10x/trail")

    while True:
        time.sleep(15)
        now = time.time()

        trades = load_active_trades()
        if mint not in trades:
            print(f"  [autosell] Position closed, stopping monitor")
            return
        # Price check via Jupiter/1inch for the token
        current_price = None
        try:
            from exchange import swap_any
            # Quick check: try to get a 1sol quote to see if token is liquid
            r = requests.get(
                "https://quote-api.jup.ag/v6/price?id=" + mint,
                timeout=10,
            )
            pdata = r.json()
            current_price = float(pdata.get("data", {}).get("price", 0) or 0)
        except:
            try:
                # Try 1inch price for EVM chains
                r = requests.get(
                    f"https://api.1inch.dev/swap/v6.0/{get_1inch_chain_id(chain)}/price",
                    params={"tokenIn": NATIVE_TOKENS.get(chain, ""), "tokenOut": mint, "amount": "1000000000000000000"},
                    headers={"Authorization": f"Bearer {ONEINCH_KEY}"},
                    timeout=10,
                )
                pdata = r.json()
                current_price = float(pdata.get("tokenOutPrice", 0) or 0)
            except:
                pass

        entry_price = trades[mint]["entry_price_sol"]
        if entry_price <= 0:
            continue

        multiplier = current_price / entry_price
        
        # Track peak for trailing stop on moon bag
        if multiplier > peak_price:
            peak_price = multiplier

        # ── Sell Bag: 2x → sell 50% (recovers principal) ──
        if multiplier >= 2.0 and trades[mint].get("sell_bag_sold") != True:
            print(f"  [autosell] 2x REACHED: selling sell bag (recovers principal)")
            try:
                token_amount = trades[mint].get("token_amount", 0)
                if token_amount > 0:
                    sell_amount = token_amount * 0.5
                    result = auto_sell(chain, mint, sell_amount, "token")
                    print(f"  [autosell] SELL BAG SOLD: {result}")
                    trades[mint]["sell_bag_sold"] = True
                    trades[mint]["sell_bag_tx"] = result.get("tx_hash", "")
                    save_active_trades(trades)
                    
                    # Alert user
                    if TELEGRAM_CHAT_ID:
                        from telegram_bot import send_alert
                        alert = (
                            f"*🟢 SELL BAG SOLD: {symbol}*\n"
                            f"2x reached — principal recovered\n"
                            f"TX: {result.get('tx_hash', '')[:30]}...\n"
                            f"Moon bag still running"
                        )
                        try:
                            send_alert(alert)
                        except:
                            pass
            except Exception as e:
                print(f"  [autosell] Sell bag failed: {e}")

        # ── Moon Bag: 5x → sell 25% ──
        if multiplier >= 5.0 and trades[mint].get("moon_5x_sold") != True:
            print(f"  [autosell] 5x REACHED: selling 25% moon bag")
            try:
                token_amount = trades[mint].get("token_amount", 0)
                if token_amount > 0:
                    sell_amount = token_amount * 0.25 * 0.5
                    result = auto_sell(chain, mint, sell_amount, "token")
                    print(f"  [autosell] MOON 5x SOLD: {result}")
                    trades[mint]["moon_5x_sold"] = True
                    trades[mint]["moon_5x_tx"] = result.get("tx_hash", "")
                    save_active_trades(trades)
            except Exception as e:
                print(f"  [autosell] Moon 5x sell failed: {e}")

        # ── Moon Bag: 10x → sell 25% more ──
        if multiplier >= 10.0 and trades[mint].get("moon_10x_sold") != True:
            print(f"  [autosell] 10x REACHED: selling 25% more moon bag")
            try:
                token_amount = trades[mint].get("token_amount", 0)
                if token_amount > 0:
                    sell_amount = token_amount * 0.25 * 0.5
                    result = auto_sell(chain, mint, sell_amount, "token")
                    print(f"  [autosell] MOON 10x SOLD: {result}")
                    trades[mint]["moon_10x_sold"] = True
                    trades[mint]["moon_10x_tx"] = result.get("tx_hash", "")
                    save_active_trades(trades)
            except Exception as e:
                print(f"  [autosell] Moon 10x sell failed: {e}")

        # ── Trailing Stop: sell 10% of remaining moon bag per 15% drop from peak ──
        if peak_price > 5.0 and multiplier < peak_price * 0.85:
            if not trades[mint].get("trailing_triggered"):
                print(f"  [autosell] TRAILING STOP: {multiplier:.1f}x vs peak {peak_price:.1f}x")
                try:
                    token_amount = trades[mint].get("token_amount", 0)
                    if token_amount > 0:
                        sell_amount = token_amount * 0.10
                        result = auto_sell(chain, mint, sell_amount, "token")
                        print(f"  [autosell] TRAIL SELL: {result}")
                        trades[mint]["trailing_triggered"] = True
                        save_active_trades(trades)
                except Exception as e:
                    print(f"  [autosell] Trail sell failed: {e}")

        # ── Time Stop: 24h max hold ──
        entry_time = datetime.fromisoformat(trades[mint]["entry_time"])
        elapsed = now - entry_time.timestamp()
        if elapsed > time_stop_seconds:
            print(f"  [autosell] TIME STOP: {symbol} for {elapsed/3600:.1f}h")
            try:
                token_amount = trades[mint].get("token_amount", 0)
                if token_amount > 0:
                    result = auto_sell(chain, mint, token_amount, "token")
                    print(f"  [autosell] TIME STOP SOLD: {result}")
            except:
                pass
            trades.pop(mint, None)
            save_active_trades(trades)
            return

        # Price alert every 3 minutes
        if now - last_alert > 180:
            last_alert = now
            print(f"  [autosell] {symbol}: {multiplier:.1f}x (peak: {peak_price:.1f}x)")


def auto_sell_position(mint, symbol, trade_info):
    """Manual auto-sell (called by time-stop or emergency)."""
    print(f"  [autosell] Selling {symbol}...")
    try:
        token_amount = trade_info.get("token_amount", 0)
        if token_amount > 0:
            result = sell_spl_token(mint, token_amount)
            print(f"  [autosell] Result: {result}")
        trades = load_active_trades()
        trades.pop(mint, None)
        save_active_trades(trades)
    except Exception as e:
        print(f"  [autosell] Sell failed: {e}")


def _load_honeypot_list():
    """Load known honeypot addresses."""
    if os.path.exists(HONEYPOT_LIST_PATH):
        with open(HONEYPOT_LIST_PATH) as f:
            return json.load(f)
    return []


def _save_honeypot_list(addresses):
    with open(HONEYPOT_LIST_PATH, "w") as f:
        json.dump(addresses, f)


def _check_honeypot(mint):
    """Check if a token is a known honeypot.
    
    Uses multiple heuristics:
    1. Known honeypot blacklist
    2. Check via Helius DAS API for suspicious token properties
    3. Simulate a tiny sell on Jupiter to verify the token is sellable
    """
    # 1. Check blacklist
    blacklist = _load_honeypot_list()
    if mint in blacklist:
        return True
    
    # 2. Check via Helius DAS API for transfer fees / restrictions
    try:
        from config import HELIUS_API_KEY, HELIUS_RPC_URL
        helius_url = HELIUS_RPC_URL
        
        r = requests.post(helius_url, json={
            "jsonrpc": "2.0", "id": "hp",
            "method": "getAsset",
            "params": {"id": mint},
        }, timeout=10)
        data = r.json().get("result", {})
        if data:
            # Check for transfer fee (honeypot indicator)
            transfer_fee = data.get("transferFee", {})
            max_fee = transfer_fee.get("maximumFee", 0) or transfer_fee.get("max", 0) or 0
            if max_fee > 0:
                # Token has transfer fee — could be honeypot
                # But some legit tokens have transfer fees (e.g. BONK had one)
                # Only flag if fee > 10%
                transfer_tax = transfer_fee.get("transferFeeBasisPoints", 0) or 0
                if transfer_tax > 1000:  # > 10%
                    return True
    
    except:
        pass
    
    # 3. Check via Jupiter simulation: try to get a quote to sell 1 token
    # If the quote fails or returns 0 output, it's likely a honeypot
    try:
        from trading import SOL_MINT
        r = requests.get(
            f"https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": mint,
                "outputMint": SOL_MINT,
                "amount": "1",
                "slippageBps": "1000000",  # 10000% slippage — max tolerance
                "onlyDirectRoutes": "false",
                "maxAccounts": 20,
            },
            timeout=10,
        )
        data = r.json()
        if "error" in data:
            # Jupiter can't route this token — honeypot or dead
            err = str(data["error"]).lower()
            if "inputMint" in err or "not found" in err or "zero" in err:
                # Save to blacklist so we don't check again
                blacklist.append(mint)
                _save_honeypot_list(blacklist)
                return True
    
    except:
        pass
    
    return False
