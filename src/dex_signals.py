"""
DEX signal engine — optimized for shitcoins.
 Focuses on early detection: buy pressure accumulation, volume before
 price, fresh tokens with smart wallets.

 Signals for shitcoins:
 1. BUY_PRESSURE_EARLY — high buy ratio (>70%) but small price change (<10%)
    → accumulation phase, pump likely coming
 2. VOLUME_PRE_PRICE — volume surge (>2x previous) BEFORE price jumps
    → smart money loading before retail
 3. FRESH_PUMP — new token (created in last 24h) with >$25k liq + buy pressure
    → pump.fun migration or new Raydium launch
 4. MOMENTUM_TURNOFF — high buy ratio (>70%) + positive 1h price change (>5%)
    → confirmed pump, might be late but trend following
 5. SELL_PRESSURE — buy ratio <30% + negative price change
    → dumping, avoid

 Filters:
 - Min liquidity: $25k (reject rugs)
 - Min volume: $5k/hour (needs activity)
 - Exclude stablecoins
"""
import time
from config import STATE_DIR


def analyze_dex_token(token_data):
    """Analyze a single DEX token and return triggered signals.
    
    Returns list of {signal, priority, score, details}.
    Priority: H (highest) → M → L (lowest).
    """
    signals = []
    
    symbol = token_data.get("symbol", "?")
    chain = token_data.get("chain", "?")
    address = token_data.get("address", "")
    price = float(token_data.get("price_usd", 0) or 0)
    vol_24h = float(token_data.get("vol_24h", 0) or 0)
    vol_6h = float(token_data.get("vol_6h", 0) or 0)
    vol_1h = float(token_data.get("vol_1h", 0) or 0)
    ch_5m = float(token_data.get("price_change_5m", 0) or 0)
    ch_1h = float(token_data.get("price_change_1h", 0) or 0)
    ch_6h = float(token_data.get("price_change_6h", 0) or 0)
    ch_24h = float(token_data.get("price_change_24h", 0) or 0)
    liq = float(token_data.get("liquidity_usd", 0) or 0)
    buys = float(token_data.get("buys_24h", 0) or 0)
    sells = float(token_data.get("sells_24h", 0) or 0)
    txns_24h = float(token_data.get("txns_24h", 0) or 0)
    
    # ── Hard filters: skip these ──────────────────────────
    # Liquidity too low — likely rug/honeypot
    if liq < 10000:
        return signals  # return empty = no signal
    
    # Volume too low — dead token
    if vol_1h < 5000:
        return signals
    
    # Stablecoins — no movement
    if symbol.upper() in ("USDT", "USDC", "BUSD", "DAI", "USD1"):
        return signals
    
    total_trades = buys + sells
    buy_ratio = buys / max(total_trades, 1)
    
    # ── Signal 1: BUY_PRESSURE_EARLY (EARLIEST signal) ────
    # High buy ratio but price hasn't moved yet
    # This means accumulation: smart money buying, price hasn't caught up
    if buy_ratio > 0.70 and abs(ch_1h) < 15 and ch_1h >= -5:
        score = buy_ratio * 10  # 7-10
        signals.append({
            "signal": "BUY_PRESSURE_EARLY",
            "priority": "H",
            "score": score,
            "reason": f"Buy ratio {buy_ratio:.0%} but price only {ch_1h:+.1f}%",
            "details": {
                "buy_ratio": buy_ratio,
                "price_change_1h": ch_1h,
                "buys": int(buys),
                "sells": int(sells),
                "buy_sell_ratio": round(buys / max(sells, 1), 1),
            }
        })
    
    # ── Signal 2: VOLUME_PRE_PRICE ───────────────────────
    # Volume accelerating BEFORE price moves
    # Projected 24h from 6h should be higher than actual 24h
    # But current price hasn't fully responded
    if vol_6h > 0 and vol_24h > 0:
        surge_ratio = (vol_6h * 4) / vol_24h  # annualized
        # Surge > 1.5x but price change < 25%
        if surge_ratio > 1.5 and ch_1h < 30 and ch_1h > -30:
            score = surge_ratio * 3  # 4.5-12
            signals.append({
                "signal": "VOLUME_PRE_PRICE",
                "priority": "H" if surge_ratio > 2.5 else "M",
                "score": score,
                "reason": f"Volume surging {surge_ratio:.1f}x, price lagging {ch_1h:+.1f}%",
                "details": {
                    "surge_ratio": surge_ratio,
                    "vol_24h": vol_24h,
                    "vol_6h": vol_6h,
                    "price_change_1h": ch_1h,
                    "price_change_6h": ch_6h,
                }
            })
    
    # ── Signal 3: FRESH_PUMP ─────────────────────────────
    # New token with strong metrics
    # We detect this by: very high txns relative to liquidity,
    # combined with buy pressure
    if total_trades > 5000 and liq < 100000:
        txn_ratio = total_trades / max(liq / 1000, 1)  # txns per $1k liq
        if txn_ratio > 50 and buy_ratio > 0.60:
            score = txn_ratio
            signals.append({
                "signal": "FRESH_PUMP",
                "priority": "M",
                "score": score,
                "reason": f"High activity: {int(total_trades)} trades, {txn_ratio:.0f} txns/$1k liq",
                "details": {
                    "total_trades": int(total_trades),
                    "liq": liq,
                    "txn_ratio": round(txn_ratio, 1),
                    "buy_ratio": buy_ratio,
                }
            })
    
    # ── Signal 4: MOMENTUM_TURNOFF (trend following) ─────
    # Buy pressure + positive price + volume confirms trend
    # Not early, but confirms the pump is real
    if buy_ratio > 0.65 and ch_1h > 5 and ch_1h < 200:
        score = buy_ratio * ch_1h / 10  # high when both strong
        signals.append({
            "signal": "MOMENTUM_TURNOFF",
            "priority": "M",
            "score": score,
            "reason": f"Confirmed pump: {buy_ratio:.0%} buys, {ch_1h:+.1f}% 1h",
            "details": {
                "buy_ratio": buy_ratio,
                "price_change_1h": ch_1h,
                "price_change_6h": ch_6h,
                "price_change_24h": ch_24h,
            }
        })
    
    # ── Signal 5: PUMP_CONFIRM (aggressive) ──────────────
    # Already pumped hard — only if volume supports it
    if ch_1h > 20 and vol_1h > 100000:
        score = ch_1h / 10  # 2-20
        signals.append({
            "signal": "PUMP_CONFIRM",
            "priority": "L",
            "score": score,
            "reason": f"Already pumping: {ch_1h:+.1f}% 1h, ${vol_1h:,.0f}/h vol",
            "details": {
                "price_change_1h": ch_1h,
                "price_change_6h": ch_6h,
                "vol_1h": vol_1h,
            }
        })
    
    # ── Signal 6: SELL_PRESSURE (warning) ────────────────
    # Buy ratio < 30%, price dropping
    # Not a buy signal, but useful for tracking
    if buy_ratio < 0.30 and total_trades > 100:
        score = (1 - buy_ratio) * 10  # 7-10
        signals.append({
            "signal": "SELL_PRESSURE",
            "priority": "L",
            "score": score,
            "reason": f"Selling pressure: {buy_ratio:.0%} buys, {ch_1h:+.1f}% 1h",
            "details": {
                "buy_ratio": buy_ratio,
                "price_change_1h": ch_1h,
                "sells": int(sells),
            }
        })
    
    return signals


def scan_all_dex_signals(take_snapshot=True):
    """Scan all DEX tokens and return triggered signals sorted by priority."""
    from feeds.dex_feeds import fetch_all_dex
    
    tokens = fetch_all_dex()
    all_signals = []
    
    for token in tokens:
        sigs = analyze_dex_token(token)
        for sig in sigs:
            sig["symbol"] = token.get("symbol", "?")
            sig["chain"] = token.get("chain", "?")
            sig["address"] = token.get("address", "")
            sig["price"] = float(token.get("price_usd", 0) or 0)
            sig["url"] = token.get("url", "")
            sig["vol_24h"] = float(token.get("vol_24h", 0) or 0)
            sig["liq"] = float(token.get("liquidity_usd", 0) or 0)
            sig["ch_1h"] = float(token.get("price_change_1h", 0) or 0)
            sig["ch_6h"] = float(token.get("price_change_6h", 0) or 0)
            all_signals.append(sig)
    
    # Sort by priority then score
    priority_order = {"H": 0, "M": 1, "L": 2}
    all_signals.sort(key=lambda s: (priority_order.get(s["priority"], 3), -s["score"]))
    
    # Remove duplicates by symbol+signal
    seen = set()
    unique = []
    for s in all_signals:
        key = (s["symbol"], s["signal"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    
    return unique


def format_signals_report(signals):
    """Format DEX signals for Telegram."""
    if not signals:
        return "*DEX Signals*\\n\\nNo signals triggered this scan.\\nFilter: liq >$10k, vol >$5k/h"
    
    lines = ["*DEX Signals*", f"{len(signals)} signals triggered\\n"]
    
    # Group by priority
    for priority in ["H", "M", "L"]:
        ps = [s for s in signals if s["priority"] == priority]
        if not ps:
            continue
        
        emoji = "🔴" if priority == "H" else ("🟡" if priority == "M" else "⚪")
        lines.append(f"{emoji} *Priority {priority}* ({len(ps)} signals)\\n")
        
        for s in ps:
            details = s["details"]
            buy_ratio = details.get("buy_ratio", 0)
            ch_1h = details.get("price_change_1h", 0)
            ch_6h = details.get("price_change_6h", 0)
            vol_24h = s.get("vol_24h", 0)
            liq = s.get("liq", 0)
            
            # Price display
            price = s.get("price", 0)
            if price > 1:
                price_str = f"${price:,.2f}"
            elif price > 0.001:
                price_str = f"${price:.4f}"
            else:
                price_str = f"${price:.10f}".rstrip("0").rstrip(".")
            
            lines.append(f"{s['symbol']} ({s['chain']})")
            lines.append(f"  Signal: {s['signal']}")
            lines.append(f"  Price: {price_str}  1h: {ch_1h:+.1f}%  6h: {ch_6h:+.1f}%")
            lines.append(f"  Vol: ${vol_24h:,.0f} | Liq: ${liq:,.0f}")
            lines.append(f"  {s['reason']}")
            
            # Buy ratio detail
            if "buy_ratio" in details:
                ratio_pct = int(buy_ratio * 100)
                lines.append(f"  Buy ratio: {ratio_pct}% ({details.get('buys', '?')}/{details.get('buys', '?') + details.get('sells', '?')})")
            
            # Surge ratio
            if "surge_ratio" in details:
                lines.append(f"  Volume surge: {details['surge_ratio']:.1f}x")
            
            lines.append(f"  [Chart]({s['url']})\\n")
    
    return "\n".join(lines)
