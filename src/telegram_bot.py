"""
Telegram bot — alert sender + command handler.
 Sends signal alerts to a chat. Runs a long-poll listener for commands.
 In GitHub Actions mode (no persistent process), it only sends alerts.
 For interactive commands, run locally with --bot flag.
"""
import os
import json
import time
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_alert(message, parse_mode="Markdown"):
    """Send a message to Telegram. Returns True on success.
    Falls back to plain text if Markdown parsing fails."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [telegram] no token/chat_id set, skipping alert")
        return False
    try:
        chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
        for chunk in chunks:
            r = requests.post(
                f"{TG_API}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": parse_mode,
                },
                timeout=15,
            )
            if r.status_code != 200:
                # Retry without parse_mode if Markdown fails
                r = requests.post(
                    f"{TG_API}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                    timeout=15,
                )
                if r.status_code != 200:
                    print(f"  [telegram] send failed: {r.status_code} {r.text[:200]}")
                    return False
            time.sleep(0.5)
        return True
    except Exception as e:
        print(f"  [telegram] error: {e}")
        return False


def format_signal_alert(signal):
    """Format a signal dict into a Telegram message."""
    source = signal.get("source", "?")
    symbol = signal.get("symbol", "?")
    sig_type = signal.get("signal", "?")
    chain = signal.get("chain", "")

    # Emoji by signal type
    bull = any(x in sig_type for x in ["BULL", "BUY", "PUMP", "LOWER_TOUCH"])
    bear = any(x in sig_type for x in ["BEAR", "SELL", "DUMP", "UPPER_TOUCH"])
    arrow = "🟢" if bull else "🔴" if bear else "⚡"

    header = f"{arrow} *{sig_type}*"

    if source == "CEX":
        price = signal.get("close", 0)
        body = f"`{symbol}` @ ${price:.4f}"
        extras = []
        if "swing_high" in signal:
            extras.append(f"Swing H: ${signal['swing_high']:.4f}")
        if "swing_low" in signal:
            extras.append(f"Swing L: ${signal['swing_low']:.4f}")
        if "body_ratio" in signal:
            extras.append(f"Body: {signal['body_ratio']:.1%}")
        if "volume" in signal:
            extras.append(f"Vol: {signal['volume']:,.0f}")
        if "multiple" in signal:
            extras.append(f"({signal['multiple']:.1f}x avg)")
        if "upper_band" in signal:
            extras.append(f"Upper Band: ${signal['upper_band']:.4f}")
        if "lower_band" in signal:
            extras.append(f"Lower Band: ${signal['lower_band']:.4f}")
        if extras:
            body += "\n" + "\n".join(extras)
    else:  # DEX
        price = signal.get("price_usd", 0)
        body = f"`{symbol}` ({chain}) @ ${price:.10f}".rstrip("0").rstrip(".")
        extras = []
        if signal.get("vol_24h"):
            extras.append(f"Vol 24h: ${signal['vol_24h']:,.0f}")
        if signal.get("vol_6h"):
            extras.append(f"Vol 6h: ${signal['vol_6h']:,.0f}")
        if "buy_ratio" in signal:
            extras.append(f"Buy ratio: {signal['buy_ratio']:.0%}")
        if "buys_24h" in signal:
            extras.append(f"Buys: {signal['buys_24h']} Sells: {signal['sells_24h']}")
        if "change_1h" in signal:
            extras.append(f"1h change: {signal['change_1h']:+.1f}%")
        if signal.get("url"):
            extras.append(f"[Chart]({signal['url']})")
        if extras:
            body += "\n" + "\n".join(extras)

    return f"{header}\n{body}"


def format_paper_trade(trade):
    """Format a paper trade (open or close) for Telegram."""
    if "exit_price" in trade:
        # Closed trade
        pnl = trade.get("pnl_usd", 0)
        emoji = "🟢" if pnl > 0 else "🔴"
        return (
            f"{emoji} *PAPER CLOSE*\n"
            f"`{trade['symbol']}` {trade['direction']}\n"
            f"Entry: ${trade['entry_price']:.4f}\n"
            f"Exit: ${trade['exit_price']:.4f}\n"
            f"P&L: {pnl:+.2f} USD ({trade.get('pnl_pct', 0)*100:+.1f}%)\n"
            f"Reason: {trade.get('exit_reason', '?')}"
        )
    else:
        # Opened position
        return (
            f"📥 *PAPER OPEN*\n"
            f"`{trade['symbol']}` {trade['direction']}\n"
            f"Entry: ${trade['entry_price']:.4f}\n"
            f"Size: {trade.get('size', 0):.4f}\n"
            f"Signal: {trade.get('signal_type', '?')}\n"
            f"SL: ${trade.get('stop_loss', 0):.4f} | TP: ${trade.get('take_profit', 0):.4f}"
        )


def format_summary(summary):
    """Format paper portfolio summary for Telegram."""
    return (
        f"📊 *Paper Portfolio*\n\n"
        f"Balance: ${summary['balance']:,.2f}\n"
        f"Realized P&L: ${summary['realized_pnl']:+,.2f}\n"
        f"Win rate: {summary['win_rate']}% ({summary['wins']}W / {summary['losses']}L)\n"
        f"Open positions: {summary['open_positions']}\n\n"
        f"*Open positions:*\n"
        + "\n".join(
            f"  `{p['symbol']}` {p['direction']} @ ${p['entry']:.4f} ({p['signal']})"
            for p in summary["positions_detail"]
        )
        if summary["positions_detail"]
        else "No open positions"
    )


# ── Command handler (interactive mode only) ──────────────────────────

def handle_command(text):
    """Parse a command from Telegram and return a response string."""
    from paper_engine import get_summary
    from feeds.dex_feeds import search_dex, get_trending_boosted

    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/status":
        return format_summary(get_summary())

    elif cmd == "/scan" and arg:
        pairs = search_dex(arg)
        if not pairs:
            return f"No results for '{arg}'"
        lines = []
        for p in pairs[:8]:
            base = p.get("baseToken", {})
            vol = p.get("volume", {}).get("h24", 0)
            price = p.get("priceUsd", 0)
            change = p.get("priceChange", {}).get("h24", 0)
            lines.append(
                f"`{base.get('symbol', '?')}` ({p.get('chainId', '?')}) "
                f"${price:.8f} 24h: {change:+.1f}% vol: ${vol:,.0f}"
            )
        return f"*Search: {arg}*\n" + "\n".join(lines)

    elif cmd == "/trending":
        boosted = get_trending_boosted()
        if not boosted:
            return "No trending data"
        lines = []
        for t in boosted[:10]:
            lines.append(
                f"`{t.get('tokenAddress', '?')[:8]}...` "
                f"({t.get('chainId', '?')}) "
                f"[link]({t.get('url', '')})"
            )
        return "*Top Trending (Boosted)*\n" + "\n".join(lines)

    elif cmd == "/help":
        return (
            "*Archimeda Commands*\n\n"
            "/status - Paper portfolio + P&L\n"
            "/scan <query> - Search Dexscreener\n"
            "/trending - Top boosted DEX tokens\n"
            "/help - This message"
        )
    else:
        return "Unknown command. Send /help"


def run_bot_listener():
    """Long-poll Telegram for commands. Run locally with --bot flag."""
    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        print("No TELEGRAM_BOT_TOKEN set. Cannot run bot listener.")
        return

    print("🤖 Archimeda bot listener running (Ctrl+C to stop)...")
    offset = 0
    while True:
        try:
            r = requests.get(
                f"{TG_API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            if r.status_code != 200:
                print(f"  [bot] getUpdates failed: {r.status_code}")
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id", "")

                if text.startswith("/"):
                    reply = handle_command(text)
                    requests.post(
                        f"{TG_API}/sendMessage",
                        json={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"},
                        timeout=15,
                    )
                    print(f"  [bot] {text} -> replied")

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"  [bot] error: {e}")
            time.sleep(5)
