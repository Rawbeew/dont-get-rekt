"""
Hermes Telegram Bot — interactive command interface.
 Run: python bot.py
 Listens for Telegram commands and responds.
 Paper mode only. No real trading.
"""
import os
import sys
import time
import asyncio
import threading
import requests

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AUTO_TRADE_ENABLED, PAPER_MODE, SCAN_INTERVAL_SEC,
)
from paper_engine import get_summary, load_positions, load_trade_log
from feeds.dex_feeds import search_dex, get_trending_boosted, fetch_token_pairs
from signals import scan_cex_symbol
from feeds.cex_feeds import fetch_ohlcv
from telegram_bot import send_alert, format_summary

TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def register_commands():
    """Register bot commands with Telegram so / shows the list."""
    cmds = [
        {"command": "status", "description": "Paper portfolio + P&L"},
        {"command": "positions", "description": "Open positions detail"},
        {"command": "trades", "description": "Closed trade history"},
        {"command": "scan", "description": "Search Dexscreener"},
        {"command": "trending", "description": "Top boosted tokens"},
        {"command": "degen", "description": "Pump.fun, Raydium, Orca"},
        {"command": "multichain", "description": "All chains, liq $12.5k-$200k"},
        {"command": "dex", "description": "DEX signals, safety checks"},
        {"command": "nft", "description": "Hot NFT contracts"},
        {"command": "backtest", "description": "Run backtest 90d"},
        {"command": "price", "description": "Live price + signals"},
        {"command": "wallets", "description": "Smart money tracking"},
        {"command": "buy", "description": "Buy token (amount SOL)"},
        {"command": "sell", "description": "Sell: sell bag/moon bag"},
        {"command": "ledger", "description": "P&L entry/exit ledger"},
        {"command": "pump", "description": "Start pump.fun live scan"},
        {"command": "pumpscan", "description": "Scan pump.fun graduations"},
        {"command": "stop", "description": "Stop pump.fun detector"},
        {"command": "help", "description": "This message"},
        {"command": "about", "description": "About Hermes"},
    ]
    try:
        r = requests.post(f"{TG_API}/setMyCommands", json={"commands": cmds}, timeout=10)
        print(f"Commands registered: {r.json()}")
    except Exception as e:
        print(f"Command registration failed: {e}")

register_commands()

HELP_TEXT = """*Hermes Command Reference*

📊 *Portfolio*
/status — Paper portfolio + P&L
/positions — Open positions detail
/trades — Closed trade history

🔍 *Scanning*
/scan <q> — Dexscreener search
/trending — Top boosted tokens
/degen — Broad scan: pump.fun, Raydium, Orca
/dex — DEX signals + safety checks
/price <sym> — Live price + signals
/wallets — Smart money tracking
/multichain — All chains, liq $12.5k-$200k

🖼️ *NFT*
/nft — Hot NFT contracts scoring

🧪 *Backtest*
/backtest — All CEX symbols, 90d
/backtest BTC — Single symbol

🎰 *Pump.fun*
/pump — Start live scanner
/pumpscan — Scan graduation tracker
/stop — Stop detector

⚡ *Trading*
/buy <addr> <amt> — Buy token (SOL)
/sell <addr> — Sell: sell bag/moon bag
/ledger — P&L ledger

/help — This message
/about — About Hermes"""

ABOUT_TEXT = """*Hermes Signal Engine*

Paper mode: ACTIVE. No real money.

📡 *Feeds*
- CEX: OKX (BTC, ETH, SOL, DOGE, AVAX, LINK, XRP)
- DEX: Dexscreener (Solana, Base, 90+ chains)
- Pump.fun: Helius WebSocket
- Smart wallet profiler
- Robinhood Chain: Arbitrum L2

📈 *Signals*
- CEX: VWAP bands, SFP, engulfing, CVD divergence
- DEX: Volume surge, buy/sell pressure, pump/dump
- Pump.fun: real-time token detection
- Smart money: 3+ token, 30%+ hit rate

🧠 *Paper Engine*
- $10,000 starting balance
- 1% risk, 2% stop, 4% target
- Max 5 concurrent positions

🔒 *Safety*
- Honeypot detection
- Mint/auth authority checks
- Freshness filter (30min)
- $12.5k min / $200k max liquidity"""


def handle_command(text, chat_id):
    """Parse a command and return a response string."""
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/") if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    # ── Portfolio ─────────────────────────────────────────
    if cmd in ("status", "s"):
        return format_summary(get_summary())

    elif cmd in ("positions", "p"):
        positions = load_positions()
        if not positions:
            return "No open positions."
        lines = ["*Open Positions:*"]
        for i, p in enumerate(positions):
            lines.append(
                f"\n{i+1}. `{p['symbol']}` {p['direction']}\n"
                f"   Entry: ${p['entry_price']:.6f}\n"
                f"   Signal: {p['signal_type']}\n"
                f"   SL: ${p['stop_loss']:.6f} | TP: ${p['take_profit']:.6f}\n"
                f"   Source: {p.get('source', '?')} | Chain: {p.get('chain', '?')}"
            )
        return "\n".join(lines)

    elif cmd in ("trades", "t"):
        trades = load_trade_log()
        if not trades:
            return "No closed trades yet."
        lines = [f"*Closed Trades ({len(trades)}):*"]
        for t in trades[-10:]:
            emoji = "+" if t.get("pnl_usd", 0) > 0 else ""
            lines.append(
                f"\n`{t['symbol']}` {t['direction']}\n"
                f"   {t['entry_price']:.6f} -> {t['exit_price']:.6f}\n"
                f"   P&L: {emoji}${t.get('pnl_usd', 0):+.2f} ({t.get('exit_reason', '?')})"
            )
        return "\n".join(lines)

    # ── Scanning ───────────────────────────────────────────
    elif cmd == "scan" and arg:
        pairs = search_dex(arg)
        if not pairs:
            return f"No Dexscreener results for '{arg}'."
        lines = [f"*Dexscreener: {arg}* ({len(pairs)} pairs)"]
        for p in pairs[:8]:
            base = p.get("baseToken", {})
            vol = p.get("volume", {}).get("h24", 0)
            price = p.get("priceUsd", 0) or 0
            change = p.get("priceChange", {}).get("h24", 0) or 0
            liq = p.get("liquidity", {}).get("usd", 0) or 0
            chain = p.get("chainId", "?")
            lines.append(
                f"\n`{base.get('symbol', '?')}` ({chain})\n"
                f"   Price: ${float(price):.10f}".rstrip("0").rstrip(".") + "\n"
                f"   24h: {change:+.1f}% | Vol: ${vol:,.0f} | Liq: ${liq:,.0f}\n"
                f"   [Chart]({p.get('url', '')})"
            )
        return "\n".join(lines)

    elif cmd == "scan" and not arg:
        return "Usage: /scan <query>\nExample: /scan SOL meme"

    elif cmd == "trending":
        boosted = get_trending_boosted()
        if not boosted:
            return "No trending data right now."
        lines = [f"*Top Trending ({len(boosted)} boosted)*"]
        for i, t in enumerate(boosted[:10]):
            chain = t.get("chainId", "?")
            url = t.get("url", "")
            desc = t.get("description", "").split("\n")[0][:30]
            addr = t.get("tokenAddress", "")[:12]
            lines.append(f"\n{i+1}. ({chain}) {desc}\n   `{addr}...`\n   [Chart]({url})")
        return "\n".join(lines)

    elif cmd == "degen":
        # Broad DEX scan: search across multiple degen platforms
        from feeds.dex_feeds import search_dex, DEX_SEARCH_QUERIES
        all_pairs = []
        seen = set()
        queries = [
            "pump.fun", "raydium", "meteora", "orca",
            "base degen", "solana meme", "bonding curve",
            "just launched", "new pair", "moonshot",
        ]
        for q in queries:
            pairs = search_dex(q)
            for p in pairs[:3]:
                sig = p.get("pairAddress", "")
                if sig in seen:
                    continue
                seen.add(sig)
                all_pairs.append(p)
        
        if not all_pairs:
            return "No degen pairs found."
        
        # Sort by volume
        all_pairs.sort(key=lambda p: p.get("volume", {}).get("h24", 0) or 0, reverse=True)
        
        lines = [f"*Top Degen Pairs ({len(all_pairs)} found)*"]
        for p in all_pairs[:12]:
            base = p.get("baseToken", {})
            vol = p.get("volume", {}).get("h24", 0) or 0
            price = p.get("priceUsd", 0) or 0
            change = p.get("priceChange", {}).get("h24", 0) or 0
            liq = p.get("liquidity", {}).get("usd", 0) or 0
            chain = p.get("chainId", "?")
            dex = p.get("dexId", "?")
            lines.append(
                f"\n`{base.get('symbol', '?')}` ({chain}/{dex})\n"
                f"   Price: ${float(price):.10f}".rstrip("0").rstrip(".") + "\n"
                f"   24h: {change:+.1f}% | Vol: ${vol:,.0f} | Liq: ${liq:,.0f}\n"
                f"   [Chart]({p.get('url', '')})"
            )
        return "\n".join(lines)

    elif cmd in ("dex", "dexback"):
        # DEX signals scan (Solana + Base + BSC only)
        from dex_signals import scan_all_dex_signals, format_signals_report
        signals = scan_all_dex_signals()
        report = format_signals_report(signals)
        return report

    elif cmd == "multichain":
        # Full multi-chain scan: Solana, Base, BSC, Robinhood, Arbitrum, Ethereum
        from multi_chain_scan import scan_all_chains, format_report
        signals = scan_all_chains()
        report = format_report(signals)
        return report

    # Auto-buy on H-priority DEX signals (when enabled)
    if cmd in ("dex", "dexback") and AUTO_TRADE_ENABLED:
        for sig in signals:
            if sig["priority"] == "H" and sig.get("address"):
                from auto_trade import auto_buy
                auto_buy(
                    {
                        "mint": sig.get("address", ""),
                        "symbol": sig.get("symbol", "unknown"),
                        "chain": sig.get("chain", "solana"),
                        "liq": sig.get("liq", 0),
                        "signal": sig["signal"],
                        "price": sig.get("price", 0),
                        "vol_24h": sig.get("vol_24h", 0),
                        "vol_1h": sig.get("vol_24h", 0) * 0.05,  # estimate from 24h
                        "buy_ratio": sig.get("buy_ratio", 0),
                        "price_change_1h": sig.get("price_change_1h", 0),
                    },
                    telegram_chat_id=TELEGRAM_CHAT_ID,
                    bot_token=TELEGRAM_BOT_TOKEN,
                )

    elif cmd == "buy":
        # Generate Jupiter swap link for a token with custom amount
        if not arg:
            return "Usage: /buy <token_address> <amount_in_SOL>\nExample: /buy BEdwv9hxufvF9pWGmtqHecUaH7uMY8H5byoYBhM8pump 0.5"
        
        parts = arg.strip().split()
        if len(parts) < 2:
            return "Usage: /buy <token_address> <amount_in_SOL>\nExample: /buy BEdwv9hxufvF9pWGmtqHecUaH7uMY8H5byoYBhM8pump 0.5"
        
        token_mint = parts[0]
        try:
            amount_sol = float(parts[1])
            if amount_sol <= 0 or amount_sol > 10:
                return "Amount must be between 0.01 and 10 SOL"
        except ValueError:
            return f"Invalid amount: {parts[1]}. Use a number like 0.5"
        
        slippage_bps = 1000  # 10% default for shitcoins
        
        from feeds.dex_feeds import fetch_token_pairs
        pairs = fetch_token_pairs(token_mint, chain_id=None)
        if not pairs:
            return f"Token not found on Dexscreener: {token_mint[:20]}..."
        
        pair = pairs[0]
        token_info = {
            "mint": token_mint,
            "symbol": pair.get("baseToken", {}).get("symbol", "?"),
            "chain": pair.get("chainId", "?"),
            "price_usd": pair.get("priceUsd", 0),
            "vol_24h": pair.get("volume", {}).get("h24", 0),
            "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
            "price_change_1h": pair.get("priceChange", {}).get("h1", 0) or pair.get("priceChange", {}).get("h24", 0),
            "price_change_6h": pair.get("priceChange", {}).get("h6", 0) or 0,
            "buy_24h": pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0,
            "sell_24h": pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0,
            "url": pair.get("url", ""),
        }
        total = token_info["buy_24h"] + token_info["sell_24h"]
        token_info["buy_ratio"] = token_info["buy_24h"] / max(total, 1)
        
        from buy_engine import check_safety, send_buy_alert
        safety = check_safety(token_mint)
        
        # Custom amount link
        from buy_engine import get_swap_url
        buy_link = get_swap_url(token_mint, amount_sol=amount_sol, slippage_bps=slippage_bps)
        
        lines = [
            f"*🎯 BUY — {amount_sol} SOL*",
            f"`{token_info['symbol']}` ({token_info['chain']})",
            f"",
            f"*Price:* ${float(token_info['price_usd']):.10f}".rstrip("0").rstrip("."),
            f"*24h:* {token_info['price_change_1h']:+.1f}% | *6h:* {token_info['price_change_6h']:+.1f}%",
            f"*Vol:* ${token_info['vol_24h']:,.0f} | *Liq:* ${token_info['liquidity_usd']:,.0f}",
            f"*Buy ratio:* {token_info['buy_ratio']:.0%}",
            f"",
            f"*Safety Check:*",
            f"  Mint auth: `{safety['mint_authority']}`",
            f"  Freeze: `{safety['freeze_authority']}`",
            f"  Supply: {safety['supply']:,}",
            f"",
            f"[BUY {amount_sol} SOL → {token_info['symbol']}]({buy_link})",
            f"",
            f"[Dexscreener]({token_info['url']}) | *Swap via Jupiter (your wallet)*",
        ]
        
        return "\n".join(lines)

    elif cmd == "ledger":
        from profit_strategy import ProfitLedger
        ledger = ProfitLedger()
        return ledger.export_summary()

    elif cmd == "nft":
        from nft_detector import scan_nft_collections, format_nft_report
        analyses = scan_nft_collections(limit=5)
        return format_nft_report(analyses)

    elif cmd == "buy":
        # Generate Jupiter swap link for a token with custom amount
        if not arg:
            return "Usage: /buy <token_address> <amount_in_SOL>\nExample: /buy BEdwv9hxufvF9pWGmtqHecUaH7uMY8H5byoYBhM8pump 0.5"
        
        parts = arg.strip().split()
        if len(parts) < 2:
            return "Usage: /buy <token_address> <amount_in_SOL>\nExample: /buy BEdwv9hxufvF9pWGmtqHecUaH7uMY8H5byoYBhM8pump 0.5"
        
        token_mint = parts[0]
        amount = float(parts[1])
        
        return get_swap_url(token_mint, amount)

    elif cmd == "sell":
        # Sell token at current price — calculates optimal exit
        if not arg:
            return "Usage: /sell <token_address> [amount] or /sell all\nExamples:\n  /sell BEdwv9hxufvF9pWGmtqHecUaH7uMY8H5byoYBhM8pump\n  /sell all"
        
        if arg.strip().lower() == "all":
            return format_sell_all_report()
        
        # Single token sell
        token_addr = arg.strip().split()[0] if " " in arg else arg.strip()
        amount_str = arg.strip().split()[1] if len(arg.strip().split()) > 1 else ""
        amount = float(amount_str) if amount_str else None
        
        from profit_strategy import ProfitLedger, calculate_sell_schedule, format_sell_schedule
        from trading import check_token_price
        
        ledger = ProfitLedger()
        mint = token_addr
        current_price = check_token_price(mint)
        
        if not current_price:
            return f"Could not find price for {mint[:20]}..."
        
        found = None
        for i, trade in enumerate(ledger.trades):
            if trade.get("mint") == mint and not trade.get("closed"):
                found = i
                break
        
        if found is None:
            return f"No open position for {mint[:20]}...\nUse /ledger to see open positions"
        
        trade = ledger.trades[found]
        
        if amount:
            # Specific amount
            entry_price = trade.get("entry_price_sol", 0)
            if entry_price > 0:
                multiplier = current_price / entry_price
                return f"{trade['symbol']} — {multiplier:.1f}x entry\nSell {amount} tokens"
            return f"Entry price unknown for {mint[:20]}..."
        
        # Show full sell schedule
        return format_sell_schedule(found, ledger)

    elif cmd in ("price", "pr") and arg:
        symbol = arg.upper().strip()
        if "/" not in symbol:
            symbol = symbol + "/USDT"
        df = fetch_ohlcv(symbol)
        if df is None:
            return f"Could not fetch {symbol}. Try BTC, ETH, SOL, DOGE, AVAX, LINK, XRP."
        sigs = scan_cex_symbol(symbol, df)
        last = df.iloc[-2]
        price = last["Close"]
        lines = [f"*{symbol}*\nPrice: ${price:.4f}\n"]
        if sigs:
            lines.append("*Signals:*")
            for s in sigs:
                lines.append(f"  {s['signal']}")
        else:
            lines.append("No signals detected this bar.")
        return "\n".join(lines)

    elif cmd in ("price", "pr") and not arg:
        return "Usage: /price <symbol>\nExample: /price BTC or /price SOL/USDT"

    # ── Wallet tracking ────────────────────────────────────
    elif cmd == "wallets":
        # Scan for smart money: find wallets trading across multiple degen tokens.
        # Uses Helius getTransactionsForAddress(mint) — returns ALL transactions
        # involving a token mint, including buyer/seller wallet addresses.
        # Cross-references: wallets appearing on multiple tokens = real money movers.
        
        import signal
    
        def timeout_handler(signum, frame):
            raise TimeoutError("Wallet scan timed out")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(90)
        
        try:
            from feeds.dex_feeds import fetch_all_dex
            from wallet_profiler import find_early_entrants, format_wallet_report
            
            pairs = fetch_all_dex()  # All chains
            # Prefer Solana pairs, expand to all if few
            solana = [p for p in pairs if p.get("chain", "") == "solana"
                      and p.get("address", "") != "So11111111111111111111111111111111111111112"]
            if len(solana) < 5:
                others = [p for p in pairs if p.get("chain", "") != "solana"
                          and p.get("address", "").startswith("0x")]
                solana.extend(others[:5])
            
            print(f"  [wallet] Scanning {len(solana)} tokens across {len(pairs)} pairs...")
            wallets = find_early_entrants(solana, limit=20)
            signal.alarm(0)
            return format_wallet_report(wallets)
        except TimeoutError:
            signal.alarm(0)
            return "Wallet scan timed out. Try again."

    # ── NFT detector ───────────────────────────────────────────
    elif cmd == "nft":
        from nft_detector import scan_nft_collections, format_nft_report
        results = scan_nft_collections(timeframe="one_hour", limit=20)
        return format_nft_report(results)

    # ── Pump.fun detector ──────────────────────────────────
    elif cmd == "pump":
        return "*Pump.fun detector is running.*\nWatching for new token launches via WebSocket.\nUse /pumpscan to see analyzed results.\nUse /stop to stop detection."

    elif cmd == "pumpscan":
        from pump_tracker import scan_pump_graduations, format_pump_report
        results = scan_pump_graduations(limit=20)
        return format_pump_report(results)

    elif cmd == "stop":
        from config import STATE_DIR
        pid_path = os.path.join(STATE_DIR, "pumpfun.pid")
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                pid = int(f.read())
            try:
                import signal as sig_module
                os.kill(pid, sig_module.SIGTERM)
                os.remove(pid_path)
                return "Pump.fun detector STOPPED."
            except Exception as e:
                os.remove(pid_path)
                return f"Could not stop process (may already be dead): {e}"
        return "Pump.fun detector is not running."

    # ── System ──────────────────────────────────────────────
    elif cmd == "backtest":
        import subprocess
        if arg:
            # Single symbol
            result = subprocess.run(
                [sys.executable, "backtest.py", "--symbol", arg, "--days", "90"],
                capture_output=True, text=True, timeout=120,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            return result.stdout[-4000:] if result.stdout else "Backtest failed."
        else:
            # All symbols
            return ("Running full backtest (90 days, all symbols).\n"
                    "This takes ~2 min. Will send results here.\n"
                    "Or use /backtest BTC for single symbol.")

    elif cmd == "help":
        return HELP_TEXT

    elif cmd == "about":
        return ABOUT_TEXT

    elif cmd == "start":
        return (f"Welcome to Hermes Signal Engine.\n\n"
                f"Paper mode: ACTIVE. No real money.\n\n"
                f"Send /help for commands.")

    else:
        return f"Unknown command: /{cmd}\nSend /help for available commands."


def run_bot():
    """Long-poll Telegram for commands."""
    if not TELEGRAM_BOT_TOKEN:
        print("No TELEGRAM_BOT_TOKEN set.")
        return

    print("Hermes Telegram bot running. Send /help on Telegram.")
    print("Press Ctrl+C to stop.\n")

    offset = 0
    while True:
        try:
            r = requests.get(
                f"{TG_API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            if r.status_code != 200:
                print(f"  getUpdates failed: {r.status_code}")
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID)

                if not text:
                    continue

                # Handle commands
                if text.startswith("/"):
                    print(f"  <- {text}")
                    reply = handle_command(text, chat_id)
                    # Send reply (try Markdown, fall back to plain)
                    try:
                        requests.post(
                            f"{TG_API}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": reply,
                                "parse_mode": "Markdown",
                            },
                            timeout=15,
                        )
                    except:
                        requests.post(
                            f"{TG_API}/sendMessage",
                            json={"chat_id": chat_id, "text": reply},
                            timeout=15,
                        )
                    print(f"  -> replied ({len(reply)} chars)")

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_bot()
