"""
Dex feeds via Dexscreener API.
Covers Solana, Base, and any chain. Tracks:
- Specific token addresses (watchlist)
- Free-text search for shitcoin discovery
- Trending / boosted tokens
- Volume surges on DEX pairs

Dexscreener API docs: https://api.dexscreener.com
- GET /latest/dex/tokens/{tokenAddresses} — pairs by token addr
- GET /latest/dex/search?q={query} — search by name/symbol
- GET /token-boosts/top/v1 — top boosted tokens
- GET /token-profiles/latest/v1 — latest profiled tokens

Rate limit: 300 req/min
"""
import requests
import time
from config import DEX_WATCHLIST, DEX_SEARCH_QUERIES, DEX_VOL_SURGE_MULT

DEXSCREENER_BASE = "https://api.dexscreener.com"
TIMEOUT = 15


def _rate_limited_get(url, params=None):
    """Thin wrapper with timeout + basic error handling."""
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"  [dex] {url} -> {r.status_code}")
            return None
    except Exception as e:
        print(f"  [dex] {url} failed: {e}")
        return None


def fetch_token_pairs(token_address, chain_id=None):
    """Fetch all pairs for a token address. Returns list of pair dicts."""
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{token_address}"
    data = _rate_limited_get(url)
    pairs_raw = data.get("pairs")
    pairs = pairs_raw if pairs_raw else []
    if chain_id:
        pairs = [p for p in pairs if p.get("chainId") == chain_id]
    return pairs


def search_dex(query):
    """Search Dexscreener by text. Returns list of pair dicts."""
    url = f"{DEXSCREENER_BASE}/latest/dex/search"
    data = _rate_limited_get(url, params={"q": query})
    if data and "pairs" in data:
        return data["pairs"]
    return []


def get_trending_boosted():
    """Get top boosted tokens on Dexscreener. These are paid-boosted, not
    necessarily good, but they show what's getting attention. Returns list."""
    url = f"{DEXSCREENER_BASE}/token-boosts/top/v1"
    data = _rate_limited_get(url)
    return data if data else []


def get_latest_profiles():
    """Latest token profiles (newly listed with Dexscreener profiles)."""
    url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
    data = _rate_limited_get(url)
    return data if data else []


def _pair_to_signal(pair):
    """Extract the signal-relevant fields from a Dexscreener pair."""
    if not pair:
        return None
    base = pair.get("baseToken", {})
    quote = pair.get("quoteToken", {})
    vol = pair.get("volume", {})
    txns = pair.get("txns", {})
    price_changes = pair.get("priceChange", {})
    liquidity = pair.get("liquidity", {})

    return {
        "chain": pair.get("chainId", "?"),
        "dex": pair.get("dexId", "?"),
        "symbol": base.get("symbol", "?"),
        "name": base.get("name", "?"),
        "address": base.get("address", ""),
        "pair_address": pair.get("pairAddress", ""),
        "price_usd": float(pair.get("priceUsd", 0) or 0),
        "vol_24h": vol.get("h24", 0),
        "vol_6h": vol.get("h6", 0),
        "vol_1h": vol.get("h1", 0),
        "vol_5m": vol.get("m5", 0),
        "buys_24h": txns.get("h24", {}).get("buys", 0),
        "sells_24h": txns.get("h24", {}).get("sells", 0),
        "buys_1h": txns.get("h1", {}).get("buys", 0),
        "sells_1h": txns.get("h1", {}).get("sells", 0),
        "price_change_24h": price_changes.get("h24", 0),
        "price_change_6h": price_changes.get("h6", 0),
        "price_change_1h": price_changes.get("h1", 0),
        "price_change_5m": price_changes.get("m5", 0),
        "liquidity_usd": liquidity.get("usd", 0),
        "url": pair.get("url", ""),
        "fdv": pair.get("fdv", 0),
    }


def fetch_all_dex():
    """Run full DEX scan: watchlist tokens + search queries + trending.
    Returns list of signal dicts, deduplicated by address."""
    all_signals = []
    seen_addresses = set()

    # 1. Watchlist tokens (by address)
    for name, info in DEX_WATCHLIST.items():
        addr = info.get("address")
        chain = info.get("chain")
        if addr:
            pairs = fetch_token_pairs(addr, chain_id=chain)
            # Take the highest-volume pair for this token
            if pairs:
                pairs.sort(key=lambda p: p.get("volume", {}).get("h24", 0), reverse=True)
                sig = _pair_to_signal(pairs[0])
                if sig and sig["address"] not in seen_addresses:
                    all_signals.append(sig)
                    seen_addresses.add(sig["address"])
                    print(f"  [dex] {name}: ${sig['price_usd']:.6f} vol24h=${sig['vol_24h']:,.0f}")
        else:
            # No address: search by symbol + chain
            results = search_dex(name)
            for p in results:
                if p.get("chainId") == chain:
                    sig = _pair_to_signal(p)
                    if sig and sig["address"] not in seen_addresses:
                        all_signals.append(sig)
                        seen_addresses.add(sig["address"])
                        print(f"  [dex] {name} ({chain}): ${sig['price_usd']:.8f} vol24h=${sig['vol_24h']:,.0f}")
                        break
        time.sleep(0.3)  # polite rate limit

    # 2. Search queries for shitcoin discovery (expanded)
    for q in DEX_SEARCH_QUERIES:
        pairs = search_dex(q)
        for p in pairs[:5]:  # top 5 per query
            sig = _pair_to_signal(p)
            if sig and sig["address"] not in seen_addresses:
                # Only add if it has meaningful volume (> $10k 24h)
                if sig["vol_24h"] and sig["vol_24h"] > 10000:
                    all_signals.append(sig)
                    seen_addresses.add(sig["address"])
                    print(f"  [dex] {q}: {sig['symbol']} ${sig['price_usd']:.8f} vol=${sig['vol_24h']:,.0f}")
        time.sleep(0.3)

    # 3. Aggressive BSC search — BSC has the biggest shitcoin pumps
    # MarsCoin, 牛来, etc. live here
    bsc_queries = ["BSC meme", "PancakeSwap", "BNB meme", "BSC pump", "PancakeSwap meme"]
    for q in bsc_queries:
        pairs = search_dex(q)
        for p in pairs[:3]:
            chain = p.get("chainId", "")
            if chain != "bsc":
                continue
            sig = _pair_to_signal(p)
            if sig and sig["address"] not in seen_addresses:
                if sig["vol_24h"] and sig["vol_24h"] > 5000:  # lower threshold for BSC
                    all_signals.append(sig)
                    seen_addresses.add(sig["address"])
                    print(f"  [dex] {q} (BSC): {sig['symbol']} ${sig['price_usd']:.10f} vol=${sig['vol_24h']:,.0f}")
        time.sleep(0.3)

    # 3. Trending boosted tokens
    boosted = get_trending_boosted()
    for entry in boosted[:10]:
        chain = entry.get("chainId", "")
        addr = entry.get("tokenAddress", "")
        if addr and addr not in seen_addresses:
            pairs = fetch_token_pairs(addr, chain_id=chain)
            if pairs:
                pairs.sort(key=lambda p: p.get("volume", {}).get("h24", 0), reverse=True)
                sig = _pair_to_signal(pairs[0])
                if sig and sig["address"] not in seen_addresses:
                    all_signals.append(sig)
                    seen_addresses.add(sig["address"])
                    print(f"  [dex] trending: {sig['symbol']} ${sig['price_usd']:.8f} vol=${sig['vol_24h']:,.0f}")
        time.sleep(0.3)

    return all_signals


def detect_volume_surge(dex_signal):
    """Check if a DEX token has a volume surge (24h vol >> 6h vol * 4)."""
    vol_24h = dex_signal.get("vol_24h", 0)
    vol_6h = dex_signal.get("vol_6h", 0)
    if vol_6h > 0 and vol_24h > 0:
        # If 6h volume annualized to 24h > surge threshold
        projected_24h = vol_6h * 4
        if projected_24h > vol_24h * DEX_VOL_SURGE_MULT:
            return True
    return False


# ── Stealth scraper fallback ─────────────────────────────────────────
# When the Dexscreener API is rate-limited or blocking, use stealth browser.
def fetch_all_dex_stealth(chains=None, limit_per_chain=10):
    """Fetch tokens via stealth browser when API fails.
    
    This is the undetectable path — uses Playwright with stealth patches.
    """
    from feeds.dex_scraper import fetch_dexscreener_tokens
    
    if chains is None:
        chains = ["solana", "base", "bsc", "ethereum", "arbitrum"]
    
    all_pairs = []
    for chain in chains:
        try:
            tokens = fetch_dexscreener_tokens(chain)
            for t in tokens[:limit_per_chain]:
                all_pairs.append({
                    "symbol": t["symbol"],
                    "chain": t["chain"],
                    "address": "",  # Dexscreener doesn't return address in scraped data
                    "price": t["price"],
                    "vol_24h": t["volume_24h"],
                    "liq": t["liquidity_usd"],
                    "market_cap": t["market_cap"],
                    "price_change_24h": t["price_change_24h"],
                    "price_change_1h": t["price_change_1h"],
                    "url": t.get("url", f"https://dexscreener.com/{chain}"),
                })
        except Exception as e:
            print(f"  [dex/stealth] {chain} failed: {e}")
    
    return all_pairs
