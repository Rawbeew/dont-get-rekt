"""
Multi-chain signal scanner.
Scans all chains on Dexscreener: Solana, Base, BSC, Robinhood, Arbitrum, Ethereum.
Returns signals ranked by strength, not just 2x targets.

Signal types per chain:
1. BUY_PRESSURE_EARLY — accumulation (80%+ buys, price hasn't moved)
2. VOLUME_PRE_PRICE — volume surge before price
3. FRESH_PUMP — new token, high txns/$1k liq
4. MOMENTUM — confirmed pump (for entry on pullbacks)
5. PUMP_CONFIRM — already moved (chase only)
"""
import os
import time
import requests
from config import STATE_DIR

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"
ALL_CHAINS = ["solana", "base", "bsc", "robinhood", "arbitrum", "ethereum", "polygon", "avalanche", "fantom", "optimism"]
DEGEN_CHAINS = ["solana", "base", "bsc", "robinhood"]

SEARCH_QUERIES = [
    "solana meme", "base meme", "bsc meme", "pump",
    "degen", "moonshot", "raydium", "pancakeswap",
    "new pair", "just launched", "bonding curve",
    "PancakeSwap", "BSC pump", "BSC meme", "PancakeSwap meme",
]

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
os.makedirs(STATE_DIR, exist_ok=True)


def search_dex(query):
    try:
        url = f"{DEXSCREENER_BASE}/search?q={query}"
        r = requests.get(url, timeout=10)
        return r.json().get("pairs", [])
    except:
        return []


def fetch_trending(chain=None, limit=20):
    pairs = []
    url = f"{DEXSCREENER_BASE}/trending"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        for p in data.get("pairs", [])[:limit]:
            if chain is None or p.get("chainId") == chain:
                pairs.append(p)
    except:
        pass
    
    # Fallback: stealth browser if API fails
    if not pairs:
        try:
            from feeds.dex_feeds import fetch_all_dex_stealth
            stealth_pairs = fetch_all_dex_stealth([chain] if chain else None, limit_per_chain=limit)
            for sp in stealth_pairs:
                if chain is None or sp["chain"] == chain:
                    pairs.append(sp)
        except:
            pass
    
    return pairs


def analyze_token(pair):
    signals = []
    symbol = pair.get("baseToken", {}).get("symbol", "?")
    chain = pair.get("chainId", "?")
    address = pair.get("baseToken", {}).get("address", "")
    price = float(pair.get("priceUsd", 0) or 0)
    
    vol = pair.get("volume", {})
    vol_24h = float(vol.get("h24", 0) or 0)
    vol_6h = float(vol.get("h6", 0) or 0)
    vol_1h = float(vol.get("h1", 0) or 0)
    
    ch = pair.get("priceChange", {})
    ch_5m = float(ch.get("m5", 0) or 0)
    ch_1h = float(ch.get("h1", 0) or 0)
    ch_6h = float(ch.get("h6", 0) or 0)
    ch_24h = float(ch.get("h24", 0) or 0)
    
    liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    
    txns = pair.get("txns", {})
    txns_24h = txns.get("h24", {})
    buys = float(txns_24h.get("buys", 0) or 0)
    sells = float(txns_24h.get("sells", 0) or 0)
    total = buys + sells
    buy_ratio = buys / max(total, 1)
    
    # Hard filters — REJECT tokens already at 300k+ liq (no upside)
    # Target $12.5k-$200k — early enough for 10-100x
    if liq < 12500:
        return signals  # too risky
    if liq > 200000:
        return signals  # already pumped, no upside
    
    if vol_1h < 2000:
        return signals
    if symbol.upper() in ("USDT", "USDC", "BUSD", "DAI", "USD1", "SOL", "WETH", "ETH", "BNB"):
        return signals
    
    # BUY_PRESSURE_EARLY
    if buy_ratio > 0.70 and abs(ch_1h) < 10 and ch_1h >= -5:
        signals.append({
            "signal": "BUY_PRESSURE_EARLY", "priority": "H",
            "score": buy_ratio * 10,
            "reason": f"Buy ratio {buy_ratio:.0%} but price only {ch_1h:+.1f}%",
            "buy_ratio": buy_ratio, "price_change_1h": ch_1h,
            "buys": int(buys), "sells": int(sells),
        })
    
    # VOLUME_PRE_PRICE
    if vol_6h > 0 and vol_24h > 0:
        surge_ratio = (vol_6h * 4) / vol_24h
        if surge_ratio > 1.5 and abs(ch_1h) < 25:
            signals.append({
                "signal": "VOLUME_PRE_PRICE",
                "priority": "H" if surge_ratio > 2.5 else "M",
                "score": surge_ratio * 3,
                "reason": f"Volume {surge_ratio:.1f}x projected, price {ch_1h:+.1f}%",
                "surge_ratio": surge_ratio, "price_change_1h": ch_1h,
                "vol_24h": vol_24h, "vol_6h": vol_6h,
            })
    
    # FRESH_PUMP
    if total > 3000 and liq < 200000:
        txn_ratio = total / max(liq / 1000, 1)
        if txn_ratio > 30 and buy_ratio > 0.55:
            signals.append({
                "signal": "FRESH_PUMP", "priority": "M",
                "score": txn_ratio,
                "reason": f"{int(total)} trades, {txn_ratio:.0f} txns/$1k liq",
                "total_trades": int(total), "liq": liq,
                "txn_ratio": round(txn_ratio, 1), "buy_ratio": buy_ratio,
            })
    
    # MOMENTUM
    if buy_ratio > 0.60 and ch_1h > 5 and ch_1h < 200:
        signals.append({
            "signal": "MOMENTUM", "priority": "M",
            "score": buy_ratio * ch_1h / 10,
            "reason": f"{buy_ratio:.0%} buys, {ch_1h:+.1f}% 1h",
            "buy_ratio": buy_ratio, "price_change_1h": ch_1h,
            "price_change_6h": ch_6h,
        })
    
    # PUMP_CONFIRM
    if ch_1h > 25 and vol_1h > 100000:
        signals.append({
            "signal": "PUMP_CONFIRM", "priority": "L",
            "score": ch_1h / 10,
            "reason": f"{ch_1h:+.1f}% 1h, ${vol_1h:,.0f}/h",
            "price_change_1h": ch_1h,
        })
    
    return signals


def scan_all_chains():
    all_tokens = []
    seen = set()
    
    # 1. Trending
    for p in fetch_trending()[:50]:
        addr = p.get("baseToken", {}).get("address", "")
        if addr and addr not in seen:
            seen.add(addr)
            all_tokens.append(p)
    
    # 2. Search queries
    for q in SEARCH_QUERIES:
        for p in search_dex(q)[:5]:
            addr = p.get("baseToken", {}).get("address", "")
            if addr and addr not in seen:
                seen.add(addr)
                all_tokens.append(p)
    
    # 3. Per-chain trending (top 5 chains)
    for chain in ALL_CHAINS[:5]:
        for p in fetch_trending(chain=chain, limit=10):
            addr = p.get("baseToken", {}).get("address", "")
            if addr and addr not in seen:
                seen.add(addr)
                all_tokens.append(p)
    
    print(f"Scanned {len(all_tokens)} tokens")
    
    # Analyze
    all_signals = []
    for i, token in enumerate(all_tokens):
        if i % 20 == 0 and i > 0:
            print(f"  Analyzed {i}/{len(all_tokens)}...")
        for sig in analyze_token(token):
            sig["symbol"] = token.get("baseToken", {}).get("symbol", "?")
            sig["chain"] = token.get("chainId", "?")
            sig["address"] = token.get("baseToken", {}).get("address", "")
            sig["pair_address"] = token.get("pairAddress", "")
            sig["price"] = float(token.get("priceUsd", 0) or 0)
            sig["url"] = token.get("url", "")
            sig["vol_24h"] = float(token.get("volume", {}).get("h24", 0) or 0)
            sig["liq"] = float(token.get("liquidity", {}).get("usd", 0) or 0)
            all_signals.append(sig)
    
    # Deduplicate
    unique = {}
    for s in all_signals:
        key = (s["address"], s["signal"])
        if key not in unique:
            unique[key] = s
    
    # Sort
    priority_order = {"H": 0, "M": 1, "L": 2}
    return sorted(unique.values(), key=lambda s: (priority_order.get(s["priority"], 3), -s["score"]))


def format_report(signals):
    if not signals:
        return "*Multi-Chain Scan*\n\nNo signals this scan.\nFilters: liq $25k-$200k, vol >$2k/h\n(Rejecting >$200k liq — no upside. Rejecting <$25k — too risky.)"
    
    lines = ["*Multi-Chain Scan*", f"{len(signals)} signals\n"]
    
    for priority in ["H", "M", "L"]:
        ps = [s for s in signals if s["priority"] == priority]
        if not ps:
            continue
        emoji = "🔴" if priority == "H" else ("🟡" if priority == "M" else "⚪")
        chains = set(p["chain"] for p in ps)
        lines.append(f"{emoji} *{priority}* ({len(ps)}) {', '.join(chains)}\n")
        
        for s in ps:
            price = s.get("price", 0)
            if price > 1:
                pstr = f"${price:,.2f}"
            elif price > 0.001:
                pstr = f"${price:.4f}"
            else:
                pstr = f"${price:.10f}".rstrip("0").rstrip(".")
            
            ch_1h = s.get("price_change_1h", 0)
            vol = s.get("vol_24h", 0)
            liq = s.get("liq", 0)
            
            lines.append(f"`{s['symbol']}` ({s['chain']})")
            lines.append(f"  {s['signal']}")
            lines.append(f"  {pstr} | 1h:{ch_1h:+.1f}% | Vol:${vol:,.0f} | Liq:${liq:,.0f}")
            lines.append(f"  {s['reason']}")
            if "buy_ratio" in s:
                lines.append(f"  Buy ratio: {int(s['buy_ratio']*100)}%")
            lines.append(f"  [Chart]({s['url']})\n")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print("Starting multi-chain scan...")
    signals = scan_all_chains()
    report = format_report(signals)
    print(report)
    print(f"\nTotal: {len(signals)} signals")
