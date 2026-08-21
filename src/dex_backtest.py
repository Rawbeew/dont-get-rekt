"""
DEX backtest — test DEX signals on historical Dexscreener data.
 Dexscreener gives us 24h/6h/1h/5m price changes, volume, and txns
 for any token. We can't get true OHLCV from Dexscreener, but we can
 test whether the signals (volume surge, buy pressure, pump/dump)
 would have predicted further gains.

 Strategy:
 1. Pull trending/searched tokens from Dexscreener
 2. For each token, record price + volume + 24h change NOW
 3. Wait N hours (or use the price change data as proxy)
 4. Check if tokens that triggered signals actually pumped

 Since we can't time-travel, this backtest uses the available
 price change windows (5m, 1h, 6h, 24h) as a proxy:
 - If a token pumped in 1h, would our volume surge signal have
   caught it at the 6h mark before the 1h pump started?
 - Buy/sell pressure correlated with future price action?

 For a true historical backtest, we'd need to log tokens daily
 and check outcomes. This module starts that logging.

 Usage:
   python dex_backtest.py              # run current snapshot
   python dex_backtest.py --history    # check past snapshots
   python dex_backtest.py --sample 50  # sample 50 tokens
"""
import os
import json
import time
from datetime import datetime, timezone
from collections import defaultdict

from config import STATE_DIR, DEX_VOL_SURGE_MULT
from feeds.dex_feeds import (
    fetch_all_dex, get_trending_boosted, search_dex,
)

SNAPSHOT_DIR = os.path.join(STATE_DIR, "dex_snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def take_snapshot():
    """Take a snapshot of all DEX tokens now.
    Saves price, volume, txns, price changes. Later we compare
    against future snapshots to see which signals predicted pumps."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot = {
        "timestamp": ts,
        "tokens": [],
    }

    # Get all DEX data
    print("Fetching DEX data...")
    dex_data = fetch_all_dex()
    print(f"Got {len(dex_data)} tokens")

    for token in dex_data:
        snapshot["tokens"].append({
            "symbol": token.get("symbol", "?"),
            "chain": token.get("chain", "?"),
            "address": token.get("address", ""),
            "price_usd": token.get("price_usd", 0),
            "vol_24h": token.get("vol_24h", 0),
            "vol_6h": token.get("vol_6h", 0),
            "vol_1h": token.get("vol_1h", 0),
            "price_change_24h": token.get("price_change_24h", 0),
            "price_change_6h": token.get("price_change_6h", 0),
            "price_change_1h": token.get("price_change_1h", 0),
            "price_change_5m": token.get("price_change_5m", 0),
            "buys_24h": token.get("buys_24h", 0),
            "sells_24h": token.get("sells_24h", 0),
            "buys_1h": token.get("buys_1h", 0),
            "sells_1h": token.get("sells_1h", 0),
            "liquidity_usd": token.get("liquidity_usd", 0),
            "url": token.get("url", ""),
        })

    # Save snapshot
    path = os.path.join(SNAPSHOT_DIR, f"snapshot_{ts}.json")
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)
    print(f"Snapshot saved: {path} ({len(snapshot['tokens'])} tokens)")

    # Also run signals on the snapshot
    analysis = analyze_snapshot(snapshot)
    return snapshot, analysis


def analyze_snapshot(snapshot):
    """Run DEX signals on a snapshot and return which tokens triggered."""
    triggered = []

    for token in snapshot["tokens"]:
        signals = []

        # Volume surge: 6h volume annualized > 24h vol * threshold
        vol_24h = token.get("vol_24h", 0)
        vol_6h = token.get("vol_6h", 0)
        if vol_6h > 0 and vol_24h > 0:
            projected_24h = vol_6h * 4
            if projected_24h > vol_24h * DEX_VOL_SURGE_MULT:
                signals.append("DEX_VOLUME_SURGE")

        # Buy pressure: 70%+ buy ratio
        buys = token.get("buys_24h", 0)
        sells = token.get("sells_24h", 0)
        total = buys + sells
        if total > 0:
            buy_ratio = buys / total
            if buy_ratio > 0.70:
                signals.append("DEX_BUY_PRESSURE")
            elif buy_ratio < 0.30:
                signals.append("DEX_SELL_PRESSURE")

        # Price action
        change_1h = token.get("price_change_1h", 0)
        if change_1h > 25:
            signals.append("DEX_PUMP_1H")
        elif change_1h < -25:
            signals.append("DEX_DUMP_1H")

        change_6h = token.get("price_change_6h", 0)
        if change_6h > 50:
            signals.append("DEX_PUMP_6H")

        # Liquidity filter: skip tokens with < $10k liquidity
        liq = token.get("liquidity_usd", 0)
        if liq < 10000:
            signals = []  # too illiquid to trade

        if signals:
            triggered.append({
                "symbol": token["symbol"],
                "chain": token["chain"],
                "address": token["address"],
                "price": token["price_usd"],
                "signals": signals,
                "vol_24h": vol_24h,
                "liq": liq,
                "change_1h": change_1h,
                "change_6h": change_6h,
                "change_24h": token.get("price_change_24h", 0),
                "buy_ratio": round(buys / total, 2) if total > 0 else 0,
            })

    return triggered


def compare_snapshots(old_path, new_path):
    """Compare two snapshots to see which tokens pumped/dumped between them.
    This is the real backtest: did our signals predict the outcome?"""
    with open(old_path) as f:
        old = json.load(f)
    with open(new_path) as f:
        new = json.load(f)

    # Index old tokens by address
    old_tokens = {t["address"]: t for t in old["tokens"] if t.get("address")}
    new_tokens = {t["address"]: t for t in new["tokens"] if t.get("address")}

    # Run signals on old snapshot
    old_triggered = analyze_snapshot(old)
    old_signals = {t["address"]: t for t in old_triggered}

    # Check outcomes: did signaled tokens pump?
    results = []
    for addr, old_sig in old_signals.items():
        if addr in new_tokens:
            new_token = new_tokens[addr]
            old_price = old_sig["price"]
            new_price = new_token["price_usd"]

            if old_price > 0 and new_price > 0:
                price_change = (new_price - old_price) / old_price * 100
            else:
                price_change = 0

            # Was the signal profitable?
            entry = old_price
            # Paper: 1% risk, 2% stop, 4% target
            stop = entry * 0.98
            target = entry * 1.04
            hit_target = new_price >= target
            hit_stop = new_price <= stop
            pnl_pct = (new_price - entry) / entry * 100

            results.append({
                "symbol": old_sig["symbol"],
                "chain": old_sig["chain"],
                "signals": old_sig["signals"],
                "entry_price": entry,
                "current_price": new_price,
                "pnl_pct": round(pnl_pct, 2),
                "hit_target": hit_target,
                "hit_stop": hit_stop,
                "change_1h_at_signal": old_sig["change_1h"],
                "change_6h_at_signal": old_sig["change_6h"],
            })

    return results


def run_dex_backtest(sample_size=30):
    """Run a DEX backtest using current data + analysis.
    Since we can't time-travel, this samples current trending tokens
    and applies signals. Future snapshots will compare outcomes."""
    print(f"\n{'='*60}")
    print(f"DEX BACKTEST — sampling {sample_size} tokens")
    print(f"{'='*60}")

    # Take current snapshot
    snapshot, triggered = take_snapshot()

    print(f"\n  Snapshot: {len(snapshot['tokens'])} tokens")
    print(f"  Signals triggered: {len(triggered)}")

    # Analyze by signal type
    by_signal = defaultdict(list)
    for t in triggered:
        for sig in t["signals"]:
            by_signal[sig].append(t)

    lines = [
        f"*DEX Backtest Snapshot*",
        f"Time: {snapshot['timestamp']}",
        f"Tokens scanned: {len(snapshot['tokens'])}",
        f"Signals triggered: {len(triggered)}",
        f"",
    ]

    for sig, tokens in by_signal.items():
        lines.append(f"*{sig}* ({len(tokens)} tokens):")
        for t in tokens[:5]:
            change_1h = t.get("change_1h", 0)
            change_6h = t.get("change_6h", 0)
            lines.append(
                f"  `{t['symbol']}` ({t['chain']}) "
                f"${t['price']:.10f}".rstrip("0").rstrip(".") + "\n"
                f"   1h: {change_1h:+.1f}% 6h: {change_6h:+.1f}% "
                f"vol: ${t['vol_24h']:,.0f} liq: ${t['liq']:,.0f}"
            )
        lines.append("")

    # Performance by signal type
    lines.append("*Signal performance (current 24h change):*")
    for sig, tokens in by_signal.items():
        avg_change = sum(t.get("change_24h", 0) for t in tokens) / len(tokens) if tokens else 0
        positive = sum(1 for t in tokens if t.get("change_24h", 0) > 0)
        lines.append(f"  {sig}: avg 24h {avg_change:+.1f}%, {positive}/{len(tokens)} positive")

    # Check for historical snapshots
    snapshots = sorted([f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("snapshot_")])
    if len(snapshots) >= 2:
        lines.append(f"\n*Historical comparison:*")
        old_path = os.path.join(SNAPSHOT_DIR, snapshots[0])
        new_path = os.path.join(SNAPSHOT_DIR, snapshots[-1])
        results = compare_snapshots(old_path, new_path)
        if results:
            wins = [r for r in results if r["pnl_pct"] > 0]
            losses = [r for r in results if r["pnl_pct"] <= 0]
            lines.append(f"  Compared {snapshots[0][-20:-5]} -> {snapshots[-1][-20:-5]}")
            lines.append(f"  Tracked tokens: {len(results)}")
            lines.append(f"  Wins: {len(wins)} | Losses: {len(losses)}")
            if results:
                avg_pnl = sum(r["pnl_pct"] for r in results) / len(results)
                lines.append(f"  Avg P&L: {avg_pnl:+.1f}%")
            # Best/worst
            if results:
                best = max(results, key=lambda x: x["pnl_pct"])
                worst = min(results, key=lambda x: x["pnl_pct"])
                lines.append(f"  Best: {best['symbol']} {best['pnl_pct']:+.1f}%")
                lines.append(f"  Worst: {worst['symbol']} {worst['pnl_pct']:+.1f}%")
    else:
        lines.append(f"\n  (Need 2+ snapshots for historical comparison. Run again later.)")

    return "\n".join(lines)


if __name__ == "__main__":
    report = run_dex_backtest(sample_size=30)
    print(report)
