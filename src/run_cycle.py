"""
Archimeda run_cycle — main entry point.
 One scan cycle: pull feeds → detect signals → open/check paper trades → alert Telegram.
 Usage:
   python run_cycle.py          # one scan cycle
   python run_cycle.py --watch  # continuous every 15 min
   python run_cycle.py --bot    # telegram command listener (interactive)
"""
import os
import sys
import time
import json
from datetime import datetime, timezone

LIVE_MODE = os.getenv("LIVE_MODE", "0") == "1"

from config import (
    PAPER_MODE, SCAN_INTERVAL_SEC, PAPER_STARTING_BALANCE,
    CEX_WATCHLIST,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from feeds.cex_feeds import fetch_all_cex
from feeds.dex_feeds import fetch_all_dex
from signals import scan_cex_symbol, scan_dex_signals
from paper_engine import (
    open_paper_position, check_exits, get_summary, load_positions,
)
from wallet_profiler import score_token_buyers
from telegram_bot import (
    send_alert, format_signal_alert, format_paper_trade, format_summary,
)

import os as _os

def _alert(*args, **kwargs):
    """Silenced when DGR_SILENT=1 — data still collected, no notifications."""
    if _os.getenv("DGR_SILENT") == "1":
        return
    send_alert(*args, **kwargs)



def run_cycle():
    """Run one full scan cycle."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*60}")
    print(f"⚡ Archimeda scan cycle — {ts}")
    print(f"   PAPER MODE: {PAPER_MODE}")
    print(f"{'='*60}")

    all_signals = []
    price_map = {}  # for exit checking

    # ── 1. CEX feeds ─────────────────────────────────────────
    print("\n📊 [1/4] Fetching CEX feeds (Binance)...")
    cex_data = fetch_all_cex()
    print(f"   Got {len(cex_data)}/{len(CEX_WATCHLIST)} symbols")

    for symbol, df in cex_data.items():
        # Run signals
        sigs = scan_cex_symbol(symbol, df)
        all_signals.extend(sigs)
        # Track latest price for exit checks
        price_map[symbol] = float(df["Close"].iloc[-1])

    print(f"   CEX signals: {len(all_signals)}")

    # ── 2. DEX feeds ─────────────────────────────────────────
    print("\n🔗 [2/4] Fetching DEX feeds (Dexscreener)...")
    dex_data = fetch_all_dex()
    print(f"   Got {len(dex_data)} DEX tokens")

    dex_alerts = scan_dex_signals(dex_data)
    print(f"   DEX alerts: {len(dex_alerts)}")

    all_signals.extend(dex_alerts)

    # Track DEX prices for exit checks
    for token in dex_data:
        if token.get("symbol"):
            price_map[token["symbol"]] = token["price_usd"]

    # ── 3. Check paper exits on open positions ───────────────
    print("\n📋 [3/4] Checking paper exits...")
    positions = load_positions()
    closed = check_exits(positions, price_map)
    if closed:
        print(f"   Closed {len(closed)} positions:")
        for trade in closed:
            print(f"     {trade['symbol']} {trade['direction']} "
                  f"P&L: ${trade['pnl_usd']:+.2f} ({trade['exit_reason']})")
            # Alert closed trades
            _alert(format_paper_trade(trade))
    else:
        print("   No positions closed this cycle")

    # ── 4. Open paper positions on new signals ───────────────
    print(f"\n📝 [4/4] Processing {len(all_signals)} signals for paper entry...")
    new_positions = 0
    for signal in all_signals:
        # DEX signals go through the wallet profiler gate
        if signal.get("source") == "DEX":
            pair_addr = signal.get("pair_address", "")
            token_mint = signal.get("address", "")
            chain = signal.get("chain", "solana")
            if pair_addr or token_mint:
                print(f"   [gate] profiling buyers for {signal.get('symbol', '?')}...")
                gate = score_token_buyers(pair_addr, chain=chain, limit=8, token_mint=token_mint)
                signal["wallet_gate"] = gate

                if gate["verdict"] != "APPROVED":
                    print(f"   REJECTED: {gate['reason']}")
                    continue
                else:
                    print(f"   APPROVED: {gate['reason']}")
            else:
                # No pair address = can't profile, skip
                continue

        pos = open_paper_position(signal)
        if pos:
            new_positions += 1
            print(f"   OPENED: {pos['symbol']} {pos['direction']} "
                  f"@ ${pos['entry_price']:.6f} ({pos['signal_type']})")
            _alert(format_paper_trade(pos))

        # Auto-trade if enabled (DEX signals with sufficient liq)
        if LIVE_MODE:
            from auto_trade import auto_buy
            auto_buy(
                signal,
                telegram_chat_id=TELEGRAM_CHAT_ID,
                bot_token=TELEGRAM_BOT_TOKEN,
            )
        else:
            print(f"   [paper] would auto-buy {signal.get('symbol', '?')} (LIVE_MODE=0)")

    # ── Alert new signals ─────────────────────────────────────
    if all_signals:
        print(f"\n⚡ {len(all_signals)} signals detected:")
        for signal in all_signals:
            alert = format_signal_alert(signal)
            print(f"   {signal.get('signal', '?')}: {signal.get('symbol', '?')}")
            _alert(alert)

    # ── Summary ──────────────────────────────────────────────
    summary = get_summary()
    print(f"\n{'─'*40}")
    print(f"📊 Paper Portfolio Summary")
    print(f"   Balance: ${summary['balance']:,.2f}")
    print(f"   Realized P&L: ${summary['realized_pnl']:+,.2f}")
    print(f"   Win rate: {summary['win_rate']}% ({summary['wins']}W / {summary['losses']}L)")
    print(f"   Open: {summary['open_positions']}")
    print(f"   New this cycle: {new_positions}")
    print(f"{'─'*40}\n")

    # Send a summary alert if there were signals or trades
    if all_signals or closed or new_positions:
        _alert(format_summary(summary))

    # ── Persist cycle to SQLite (data pipeline: store → query → judge) ──
    try:
        from store import Store
        db = Store(os.path.join(os.path.dirname(__file__), "state", "dgr.db"))
        for signal in all_signals:
            db.insert_signal(
                chain=signal.get("chain", "unknown"),
                token=signal.get("symbol", "unknown"),
                kind=str(signal.get("signal", "unknown")).lower(),
                direction="long" if signal.get("side", "buy") == "buy" else "short",
                strength=float(signal.get("strength", 0.5) or 0.5),
                meta={k: v for k, v in signal.items()
                      if k not in ("chain", "symbol", "signal", "side")},
            )
        db.record_equity(
            equity_usd=PAPER_STARTING_BALANCE + summary["realized_pnl"],
            open_positions=summary["open_positions"],
            realized_pnl_usd=summary["realized_pnl"],
        )
        print(f"   Stored: {len(all_signals)} signals, equity snapshot")
    except Exception as e:
        print(f"   (store skipped: {e})")


    return all_signals


def run_watch():
    """Continuous mode: run cycle every SCAN_INTERVAL_SEC."""
    print(f"Watch mode: cycling every {SCAN_INTERVAL_SEC}s")
    while True:
        try:
            run_cycle()
            print(f"\n💤 Sleeping {SCAN_INTERVAL_SEC}s...")
            time.sleep(SCAN_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"❌ Cycle error: {e}")
            time.sleep(60)  # wait before retry


def run_bot():
    """Interactive Telegram bot listener."""
    from telegram_bot import run_bot_listener
    run_bot_listener()


def run_judge_eval():
    """Re-score judge scenarios and print pass-rate. Cheap: len(SCENARIOS) calls."""
    from judge_eval import SCENARIOS, run as _run
    print(f"\n⚖️ Judge eval — re-scoring {len(SCENARIOS)} scenarios...")
    try:
        results = _run(use_db=False)
    except Exception as e:
        # loomweaver unavailable etc. — never block the trading cycle on eval
        print(f"   (judge eval skipped: {e})")
        return None
    return results


if __name__ == "__main__":
    if "--judge-eval" in sys.argv:
        run_judge_eval()
    elif "--watch" in sys.argv:
        run_watch()
    elif "--bot" in sys.argv:
        run_bot()
    else:
        # Optional pre-cycle eval pass-rate gate (cheap, 4 scenarios)
        if os.getenv("DGR_JUDGE_EVAL_FIRST") == "1":
            run_judge_eval()
        run_cycle()
