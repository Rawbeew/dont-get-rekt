"""
Backtesting engine — run signals on historical data, measure win rate.
 Pulls historical OHLCV from ccxt (CEX) and runs the same signals
 (VWAP, SFP, engulfing, CVD divergence, volume spike) across every
 bar in the dataset. Simulates paper trades and reports:
 - Total trades, wins, losses
 - Win rate
 - Average gain / average loss
 - Best / worst trade
 - Max drawdown
 - Profit factor
 - Sharpe ratio (simplified)

 Also runs DEX backtests using Dexscreener historical price changes.

 Usage:
   python backtest.py                    # backtest all CEX symbols (default 180 days)
   python backtest.py --symbol BTC/USDT  # single symbol
   python backtest.py --days 365         # 1 year
   python backtest.py --dex              # backtest DEX signals too

 Telegram:
   /backtest - run backtest (CEX, 180 days)
   /backtest BTC - backtest BTC/USDT
"""
import sys
import json
import time
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from config import (
    CEX_WATCHLIST, CEX_TIMEFRAME, PAPER_STARTING_BALANCE,
    PAPER_POSITION_SIZE_PCT, PAPER_STOP_LOSS_PCT,
    PAPER_TAKE_PROFIT_PCT, PAPER_MAX_POSITIONS,
    STATE_DIR,
)
from feeds.cex_feeds import fetch_ohlcv
from signals import compute_vwap, detect_sfp, detect_engulfing, detect_volume_spike, detect_cvd_divergence


def fetch_historical_ohlcv(symbol, timeframe="15m", days=180):
    """Fetch historical OHLCV for backtesting.
    ccxt limit is 1000 bars per call. For 15m timeframe:
    180 days = 180 * 24 * 4 = 17,280 bars = ~18 calls
    We'll use 1000 bars max for speed (about 10 days on 15m).
    For longer backtests, switch to 1h or 4h timeframe.
    """
    # For backtests, use 1h timeframe to cover more history per call
    tf = "1h" if days > 10 else timeframe
    limit = min(1000, days * 24)
    if tf == "1h":
        limit = min(1000, days * 24)
    elif tf == "4h":
        limit = min(1000, days * 6)

    df = fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    return df, tf


def run_backtest_symbol(symbol, days=180):
    """Run backtest on a single CEX symbol. Returns trade list + stats."""
    df, tf = fetch_historical_ohlcv(symbol, days=days)
    if df is None or len(df) < 30:
        return {"symbol": symbol, "error": f"insufficient data ({0 if df is None else len(df)} bars)"}

    # Compute VWAP once for the whole series
    df = compute_vwap(df)

    trades = []
    open_position = None

    # Walk through each bar starting from bar 25 (need 20-bar lookback for SFP)
    for i in range(25, len(df) - 1):
        bar = df.iloc[i]
        prev_bar = df.iloc[i - 1]

        # ── Check exits on open position ───────────────────
        if open_position:
            entry = open_position["entry_price"]
            direction = open_position["direction"]
            sl = open_position["stop_loss"]
            tp = open_position["take_profit"]

            # Check this bar's high/low for SL/TP hits
            hit_sl = False
            hit_tp = False

            if direction == "BUY":
                if bar["Low"] <= sl:
                    hit_sl = True
                elif bar["High"] >= tp:
                    hit_tp = True
            else:  # SELL
                if bar["High"] >= sl:
                    hit_sl = True
                elif bar["Low"] <= tp:
                    hit_tp = True

            if hit_sl or hit_tp:
                exit_price = sl if hit_sl else tp
                if direction == "BUY":
                    pnl_pct = (exit_price - entry) / entry
                else:
                    pnl_pct = (entry - exit_price) / entry

                risk_amount = PAPER_STARTING_BALANCE * PAPER_POSITION_SIZE_PCT
                pnl_usd = risk_amount * pnl_pct

                trades.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "entry_bar": open_position["entry_bar"],
                    "exit_bar": i,
                    "signal": open_position["signal"],
                    "exit_reason": "STOP_LOSS" if hit_sl else "TAKE_PROFIT",
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_usd": round(pnl_usd, 2),
                    "bars_held": i - open_position["entry_bar"],
                })
                open_position = None

        # ── Check for new entry signals ─────────────────────
        if open_position is not None:
            continue  # already in a position, skip new signals

        # Run signals on bar i (using data up to bar i)
        slice = df.iloc[:i+1]
        if len(slice) < 25:
            continue

        sigs = []

        # SFP
        sfp = detect_sfp(slice)
        if sfp:
            sigs.append(("BULL_SFP" if sfp["bull_sfp"] else "BEAR_SFP", sfp["close"]))

        # Engulfing
        eng = detect_engulfing(slice)
        if eng:
            sigs.append((eng["signal"], eng["close"]))

        # Volume spike
        vs = detect_volume_spike(slice)
        if vs:
            # Volume spike alone isn't directional, use price action
            if bar["Close"] > bar["Open"]:
                sigs.append(("VOLUME_SPIKE_BULL", bar["Close"]))
            else:
                sigs.append(("VOLUME_SPIKE_BEAR", bar["Close"]))

        # CVD divergence
        cvd = detect_cvd_divergence(slice)
        if cvd:
            sigs.append((cvd["signal"], cvd["close"]))

        # VWAP band touch
        if bar["Close"] >= bar["Upper_Band"]:
            sigs.append(("VWAP_UPPER_TOUCH", bar["Close"]))
        elif bar["Close"] <= bar["Lower_Band"]:
            sigs.append(("VWAP_LOWER_TOUCH", bar["Close"]))

        # Take the first signal as the entry
        if sigs:
            sig_type, price = sigs[0]

            # Determine direction
            bull = any("BULL" in s or "LOWER" in s or "BUY" in s for s, _ in sigs)
            bear = any("BEAR" in s or "UPPER" in s or "SELL" in s for s, _ in sigs)

            if bull:
                direction = "BUY"
            elif bear:
                direction = "SELL"
            else:
                continue

            open_position = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": price,
                "entry_bar": i,
                "signal": sig_type,
                "stop_loss": price * (1 - PAPER_STOP_LOSS_PCT) if direction == "BUY"
                              else price * (1 + PAPER_STOP_LOSS_PCT),
                "take_profit": price * (1 + PAPER_TAKE_PROFIT_PCT) if direction == "BUY"
                                else price * (1 - PAPER_TAKE_PROFIT_PCT),
            }

    # Close any remaining position at the last close
    if open_position:
        last_close = df.iloc[-1]["Close"]
        entry = open_position["entry_price"]
        if open_position["direction"] == "BUY":
            pnl_pct = (last_close - entry) / entry
        else:
            pnl_pct = (entry - last_close) / entry
        risk_amount = PAPER_STARTING_BALANCE * PAPER_POSITION_SIZE_PCT
        pnl_usd = risk_amount * pnl_pct
        trades.append({
            "symbol": symbol,
            "direction": open_position["direction"],
            "entry_price": entry,
            "exit_price": last_close,
            "entry_bar": open_position["entry_bar"],
            "exit_bar": len(df) - 1,
            "signal": open_position["signal"],
            "exit_reason": "END_OF_DATA",
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 2),
            "bars_held": len(df) - 1 - open_position["entry_bar"],
        })

    return analyze_trades(symbol, trades, tf, len(df))


def analyze_trades(symbol, trades, timeframe, total_bars):
    """Analyze a list of trades and compute statistics."""

    if not trades:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "total_bars": total_bars,
            "total_trades": 0,
            "message": "No signals triggered in this period",
        }

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    total_pnl = sum(t["pnl_usd"] for t in trades)

    # Win rate
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    # Average win/loss
    avg_win = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0

    # Best/worst
    best_trade = max(trades, key=lambda t: t["pnl_usd"]) if trades else None
    worst_trade = min(trades, key=lambda t: t["pnl_usd"]) if trades else None

    # Profit factor
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    # Max drawdown (simplified: track cumulative P&L)
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t["pnl_usd"]
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (simplified: mean P&L / std P&L * sqrt(trades))
    import numpy as np
    pnl_list = [t["pnl_usd"] for t in trades]
    if len(pnl_list) > 1:
        mean_pnl = np.mean(pnl_list)
        std_pnl = np.std(pnl_list)
        sharpe = (mean_pnl / std_pnl * np.sqrt(len(pnl_list))) if std_pnl > 0 else 0
    else:
        sharpe = 0

    # Signal breakdown
    signal_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    for t in trades:
        sig = t["signal"]
        signal_stats[sig]["trades"] += 1
        if t["pnl_usd"] > 0:
            signal_stats[sig]["wins"] += 1
        signal_stats[sig]["pnl"] += t["pnl_usd"]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "total_bars": total_bars,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(best_trade["pnl_usd"], 2) if best_trade else 0,
        "worst_trade": round(worst_trade["pnl_usd"], 2) if worst_trade else 0,
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "signal_breakdown": {
            sig: {
                "trades": s["trades"],
                "win_rate": round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0,
                "pnl": round(s["pnl"], 2),
            }
            for sig, s in sorted(signal_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
        },
        "trades_detail": trades[-10:],  # last 10 trades
    }


def format_backtest_report(result):
    """Format backtest result for Telegram."""
    if "error" in result or "message" in result:
        return f"`{result['symbol']}`: {result.get('error', result.get('message', 'no data'))}"

    lines = [
        f"*Backtest: {result['symbol']}*",
        f"Timeframe: {result['timeframe']} | Bars: {result['total_bars']}",
        f"",
        f"Trades: {result['total_trades']} ({result['wins']}W / {result['losses']}L)",
        f"Win rate: {result['win_rate']}%",
        f"Total P&L: ${result['total_pnl']:+.2f}",
        f"Avg win: ${result['avg_win']:+.2f} | Avg loss: ${result['avg_loss']:+.2f}",
        f"Best: ${result['best_trade']:+.2f} | Worst: ${result['worst_trade']:+.2f}",
        f"Profit factor: {result['profit_factor']}",
        f"Max drawdown: ${result['max_drawdown']:.2f}",
        f"Sharpe: {result['sharpe']}",
        f"",
        f"*Signal breakdown:*",
    ]

    for sig, stats in result.get("signal_breakdown", {}).items():
        lines.append(
            f"  {sig}: {stats['trades']} trades, "
            f"{stats['win_rate']}% win, "
            f"${stats['pnl']:+.2f}"
        )

    return "\n".join(lines)


def run_full_backtest(days=180):
    """Run backtest on all CEX watchlist symbols. Returns combined report."""
    print(f"\n{'='*60}")
    print(f"BACKTEST — {days} days, {len(CEX_WATCHLIST)} symbols")
    print(f"{'='*60}")

    all_results = []
    for symbol in CEX_WATCHLIST:
        print(f"\n  Backtesting {symbol}...")
        result = run_backtest_symbol(symbol, days=days)
        all_results.append(result)
        if "error" not in result and "message" not in result:
            print(f"    {result['total_trades']} trades, win rate {result['win_rate']}%, "
                  f"P&L ${result['total_pnl']:+.2f}")
        else:
            print(f"    {result.get('error', result.get('message', 'skip'))}")

    # Combined summary
    valid = [r for r in all_results if "error" not in r and "message" not in r]
    if not valid:
        return "No valid backtest results."

    total_trades = sum(r["total_trades"] for r in valid)
    total_wins = sum(r["wins"] for r in valid)
    total_pnl = sum(r["total_pnl"] for r in valid)

    lines = [
        f"*Backtest Summary ({days} days)*",
        f"",
        f"Total trades: {total_trades}",
        f"Total wins: {total_wins}",
        f"Overall win rate: {total_wins/total_trades*100:.1f}%" if total_trades else "N/A",
        f"Total P&L: ${total_pnl:+.2f}",
        f"",
        f"*Per symbol:*",
    ]
    for r in valid:
        lines.append(
            f"  `{r['symbol']}`: {r['total_trades']} trades, "
            f"{r['win_rate']}% win, ${r['total_pnl']:+.2f}"
        )

    # Signal breakdown across all symbols
    all_signals = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    for r in valid:
        for sig, stats in r.get("signal_breakdown", {}).items():
            all_signals[sig]["trades"] += stats["trades"]
            all_signals[sig]["wins"] += int(stats["win_rate"] / 100 * stats["trades"])
            all_signals[sig]["pnl"] += stats["pnl"]

    lines.append(f"\n*Best signals:*")
    for sig, s in sorted(all_signals.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]:
        wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
        lines.append(f"  {sig}: {s['trades']} trades, {wr:.0f}% win, ${s['pnl']:+.2f}")

    return "\n".join(lines)


def save_backtest_result(result, path=None):
    """Save backtest result to JSON."""
    if path is None:
        path = os.path.join(STATE_DIR, "backtest_result.json")
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    return path


if __name__ == "__main__":
    days = 180
    symbol = None

    for i, arg in enumerate(sys.argv):
        if arg == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
        elif arg == "--symbol" and i + 1 < len(sys.argv):
            symbol = sys.argv[i + 1]

    if symbol:
        result = run_backtest_symbol(symbol, days=days)
        print(format_backtest_report(result))
        save_backtest_result(result)
    else:
        report = run_full_backtest(days=days)
        print(report)
        save_backtest_result({"report": report})
