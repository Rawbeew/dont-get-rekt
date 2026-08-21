"""
NFT Hot Contract Detector — find collections before they trend.

Flows:
1. OpenSea API v2: trending collections by timeframe
2. Etherscan API: latest NFT mints from new contracts
3. Rarity.tools: rarity scores on newly listed contracts
4. Social signals: Twitter mentions, Discord activity, contract age

Scoring:
- Contract age < 48 hours = fresh (higher score if momentum)
- Volume surge in last 1h > 5x from 24h = momentum
- Holder growth rate > 100% in 24h = adoption signal
- Floor price stability (not dumping) = confidence
- Social mentions spike = narrative building

Usage:
  python -m nft_detector --scan
  python -m nft_detector --mint <contract_address>
"""
import os
import re
import time
import json
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────
OPENSEA_API_KEY = os.getenv("OPENSEA_API_KEY", "")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SOLSCAN_API_KEY = os.getenv("SOLSCAN_API_KEY", "")

# OpenSea API v2 endpoints
OPENSEA_BASE = "https://api.opensea.io/api/v2"

# Etherscan API endpoints
ETHERSCAN_BASE = "https://api.etherscan.io/api"


# ── OpenSea API v2 ────────────────────────────────────────────────────

def get_opensea_headers():
    """Get headers for OpenSea API v2."""
    headers = {
        "accept": "application/json",
        "x-api-key": OPENSEA_API_KEY,
    }
    if OPENSEA_API_KEY:
        headers["X-API-KEY"] = OPENSEA_API_KEY
    return headers


def fetch_trending_collections(timeframe="all_time", limit=20):
    """Fetch trending NFT collections from OpenSea API v2.
    
    Args:
        timeframe: "one_minute", "one_hour", "six_hours", "one_day", "three_days", "seven_days", "thirty_days", "all_time"
        limit: number of collections to return
    
    Returns:
        list of collection dicts with:
          - slug, name, description, image_url
          - floor_price, total_volume, total_sales
          - one_day_volume, six_hours_volume, etc.
          - market_cap, holders
    """
    url = f"{OPENSEA_BASE}/collections/trending"
    params = {
        "timeframe": timeframe,
        "limit": limit,
    }
    
    try:
        r = requests.get(url, headers=get_opensea_headers(), params=params, timeout=10)
        data = r.json()
        collections = data.get("collections", [])
        return collections
    except Exception as e:
        print(f"  [nft] OpenSea fetch failed: {e}")
        return []


def fetch_collection_details(slug):
    """Fetch detailed info about a specific collection.
    
    Args:
        slug: OpenSea collection slug
    
    Returns:
        dict with:
          - slug, name, description, image_url, banner_image_url
          - total_supply, num_owners, floor_price
          - one_day_volume, seven_day_volume, total_volume
          - twitter_username, discord_url, external_url
          - safelist_request_status (verified?)
    """
    url = f"{OPENSEA_BASE}/collections/{slug}"
    
    try:
        r = requests.get(url, headers=get_opensea_headers(), timeout=10)
        return r.json()
    except:
        return {}


def fetch_collection_activity(slug, limit=50):
    """Fetch recent events (sales, listings, transfers) for a collection.
    
    Args:
        slug: OpenSea collection slug
        limit: max events to return
    
    Returns:
        list of event dicts
    """
    url = f"{OPENSEA_BASE}/events/collection/{slug}/occurred"
    params = {"limit": limit}
    
    try:
        r = requests.get(url, headers=get_opensea_headers(), params=params, timeout=10)
        events = r.json().get("asset_events", [])
        return events
    except:
        return []


# ── Etherscan API ────────────────────────────────────────────────────

def fetch_new_nft_mints(chain="ethereum", limit=50):
    """Fetch recent NFT mint transactions via Etherscan.
    
    Looks for new ERC-721/ERC-1155 contracts with fresh mint activity.
    
    Args:
        chain: "ethereum", "polygon", "bsc"
    
    Returns:
        list of mint events with:
          - contract_address, token_id, from_address, to_address
          - timestamp, transaction_hash
          - contract_name, contract_type (ERC721/ERC1155)
    """
    mints = []
    
    if chain == "ethereum":
        api_key = ETHERSCAN_API_KEY
        base = ETHERSCAN_BASE
    elif chain == "polygon":
        api_key = POLYGONSCAN_API_KEY
        base = "https://api-polygon.tokenview.com/v1/api"
    else:
        return mints
    
    if not api_key:
        return mints
    
    # Method 1: Search for recent ERC-721 token transfers
    # (Mints are transfers from 0x000...000 to an address)
    try:
        # This requires a full node API that supports token transfer filtering
        # Etherscan standard API doesn't easily support this, so we use
        # a workaround: fetch new contract creations and filter for NFT standards
        r = requests.get(base, params={
            "module": "contract",
            "action": "getcontractcreation",
            "contractonly": 1,
            "startblock": 0,
            "endblock": 999999999,
            "page": 1,
            "offset": limit,
            "sort": "desc",
            "apikey": api_key,
        }, timeout=15)
        
        data = r.json()
        for item in data.get("result", []):
            # Check if this is an NFT contract
            # New contracts created recently with "NFT" or "Art" in name
            contract_addr = item.get("contractAddress", "")
            mints.append({
                "contract_address": contract_addr,
                "contract_name": "",  # Would need ABI to decode
                "type": "UNKNOWN",
                "timestamp": int(item.get("timeStamp", 0)),
                "transaction_hash": item.get("txHash", ""),
            })
    except Exception as e:
        print(f"  [nft] Etherscan fetch failed: {e}")
    
    return mints[:limit]


# ── Scoring Engine ────────────────────────────────────────────────────

def score_collection(collection, activity=None):
    """Score a collection based on multiple signals.
    
    Returns dict with:
      - total_score: 0-100
      - signal_breakdown: {volume: X, momentum: Y, holder_growth: Z, ...}
      - recommendation: BUY, WATCH, AVOID
    """
    score = 0
    signals = {}
    
    if not collection:
        return {"total_score": 0, "signals": signals, "recommendation": "AVOID"}
    
    # ── Signal 1: Volume Momentum ─────────────────────────────────────
    one_day_vol = float(collection.get("one_day_volume", 0) or 0)
    seven_day_vol = float(collection.get("seven_day_volume", 0) or 0)
    total_vol = float(collection.get("total_volume", 0) or 0)
    
    if seven_day_vol > 0:
        # Today's volume vs average daily volume
        avg_daily = seven_day_vol / 7
        if avg_daily > 0:
            momentum_ratio = one_day_vol / avg_daily
            if momentum_ratio > 3:
                signals["volume_momentum"] = "EXPLOSIVE"
                score += 30
            elif momentum_ratio > 2:
                signals["volume_momentum"] = "STRONG"
                score += 20
            elif momentum_ratio > 1.5:
                signals["volume_momentum"] = "STEADY"
                score += 10
            else:
                signals["volume_momentum"] = "FLAT"
    
    # ── Signal 2: Floor Price Strength ────────────────────────────────
    floor_price = float(collection.get("floor_price", {}).get("value", 0) or 0)
    floor_currency = collection.get("floor_price", {}).get("currency", "ETH")
    floor_usd = floor_price * 2000  # rough ETH price
    
    if floor_usd > 1000:
        signals["floor_usd"] = f"${floor_usd:,.0f} (blue chip)"
        score += 15  # Established, but might be late
    elif floor_usd > 100:
        signals["floor_usd"] = f"${floor_usd:,.0f} (mid-tier)"
        score += 25  # Sweet spot — has value but room to grow
    elif floor_usd > 10:
        signals["floor_usd"] = f"${floor_usd:,.0f} (emerging)"
        score += 20  # Early stage, higher risk
    else:
        signals["floor_usd"] = f"${floor_usd:,.0f} (speculative)"
        score += 10
    
    # ── Signal 3: Holder Count ────────────────────────────────────────
    holders = int(collection.get("total_supply", 0) or 0)
    # OpenSea v2 doesn't always return holder count directly
    # Estimate from supply
    if holders > 10000:
        signals["holders"] = f"{holders:,} (widely held)"
        score += 10
    elif holders > 1000:
        signals["holders"] = f"{holders:,} (moderate)"
        score += 20
    elif holders > 100:
        signals["holders"] = f"{holders:,} (growing)"
        score += 25  # Small and growing = early
    else:
        signals["holders"] = f"{holders} (tiny/new)"
        score += 15
    
    # ── Signal 4: Sales Activity ──────────────────────────────────────
    one_day_sales = int(collection.get("one_day_sales", 0) or 0)
    six_hours_sales = int(collection.get("six_hours_sales", 0) or 0)
    
    if one_day_sales > 50:
        signals["sales_24h"] = f"{one_day_sales} sales (very active)"
        score += 20
    elif one_day_sales > 20:
        signals["sales_24h"] = f"{one_day_sales} sales (active)"
        score += 15
    elif one_day_sales > 5:
        signals["sales_24h"] = f"{one_day_sales} sales (moderate)"
        score += 10
    else:
        signals["sales_24h"] = f"{one_day_sales} sales (quiet)"
        score += 5
    
    # ── Signal 5: Time-Based Decay ────────────────────────────────────
    # "All time" trending = established (less upside)
    # "One hour" trending = newly discovered (more upside)
    timeframe_score = {
        "one_minute": 30,
        "one_hour": 25,
        "six_hours": 20,
        "one_day": 15,
        "three_days": 10,
        "seven_days": 5,
        "thirty_days": 3,
        "all_time": 0,
    }
    
    signals["timeframe_decay"] = "early"
    
    # ── Signal 6: Verification Status ─────────────────────────────────
    is_verified = collection.get("safelist_request_status") == "verified"
    is_rarity_enabled = collection.get("rarity_enabled", False)
    
    if is_verified:
        signals["verified"] = True
        score += 5  # Slight confidence boost
    if is_rarity_enabled:
        signals["rarity_enabled"] = True
        score += 5
    
    # ── Signal 7: Social Signals ──────────────────────────────────────
    twitter = collection.get("twitter_username", "")
    discord = collection.get("discord_url", "")
    
    if twitter or discord:
        signals["socials"] = "Active"
        score += 10
    else:
        signals["socials"] = "Unknown"
    
    # ── Calculate Final Score ─────────────────────────────────────────
    total_score = min(100, score)
    
    if total_score >= 70:
        recommendation = "BUY"
    elif total_score >= 45:
        recommendation = "WATCH"
    else:
        recommendation = "AVOID"
    
    return {
        "total_score": total_score,
        "signals": signals,
        "recommendation": recommendation,
    }


# ── Main Scanning Logic ───────────────────────────────────────────────

def scan_nft_collections(timeframe="one_hour", limit=20):
    """Full NFT scan: fetch trending collections, score them, rank by signal.
    
    Args:
        timeframe: OpenSea API timeframe (shorter = fresher)
        limit: number of collections to analyze
    
    Returns:
        list of dicts with:
          - collection data + score + recommendation + signals
          - sorted by score descending
    """
    print(f"  [nft] Fetching trending collections ({timeframe})...")
    collections = fetch_trending_collections(timeframe=timeframe, limit=limit)
    print(f"  [nft] Found {len(collections)} collections")
    
    results = []
    for coll in collections:
        slug = coll.get("slug", "")
        if not slug:
            continue
        
        # Get detailed info
        details = fetch_collection_details(slug)
        
        # Score it
        scored = score_collection(details)
        
        result = {
            "slug": slug,
            "name": details.get("name", coll.get("name", "")),
            "description": details.get("description", "")[:100],
            "image_url": details.get("image_url", ""),
            "floor_price": details.get("floor_price", {}),
            "total_supply": details.get("total_supply", 0),
            "one_day_volume": details.get("one_day_volume", 0),
            "seven_day_volume": details.get("seven_day_volume", 0),
            "total_volume": details.get("total_volume", 0),
            "one_day_sales": details.get("one_day_sales", 0),
            "six_hours_sales": details.get("six_hours_sales", 0),
            "twitter": details.get("twitter_username", ""),
            "discord": details.get("discord_url", ""),
            "verified": details.get("safelist_request_status") == "verified",
            "score": scored["total_score"],
            "signals": scored["signals"],
            "recommendation": scored["recommendation"],
        }
        results.append(result)
    
    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def scan_new_contracts(limit=10):
    """Scan for newly created NFT contracts with recent mint activity.
    
    Uses Etherscan to find recently created contracts, then checks
    if they're NFT contracts (ERC-721/ERC-1155) with mint activity.
    
    Returns:
        list of newly created NFT contracts with basic info
    """
    contracts = fetch_new_nft_mints(limit=limit)
    return contracts


def format_nft_report(analyses):
    """Format NFT scan results for Telegram.
    
    Returns:
        Markdown-formatted string
    """
    if not analyses:
        return "*🖼️ NFT Hot Contract Detector*\n\nNo trending collections found."
    
    lines = ["*🖼️ NFT Hot Contract Detector*", ""]
    
    buy = [a for a in analyses if a.get("recommendation") == "BUY"]
    watch = [a for a in analyses if a.get("recommendation") == "WATCH"]
    
    for i, a in enumerate(buy[:5], 1):
        name = a.get("name", "?")
        slug = a.get("slug", "?")
        score = a.get("score", 0)
        floor = a.get("floor_price", {})
        floor_val = float(floor.get("value", 0) or 0)
        floor_cur = floor.get("currency", "ETH")
        vol_1d = float(a.get("one_day_volume", 0) or 0)
        sales = a.get("one_day_sales", 0)
        
        lines.append(f"🔴 #{i} *BUY* — {name}")
        lines.append(f"   Slug: @{slug}")
        lines.append(f"   Score: {score}/100 | Floor: {floor_val:.3f} {floor_cur}")
        lines.append(f"   24h Vol: {vol_1d:.1f} {floor_cur} | Sales: {sales}")
        
        # Show key signals
        signals = a.get("signals", {})
        if signals.get("volume_momentum"):
            lines.append(f"   Momentum: {signals['volume_momentum']}")
    
    for i, a in enumerate(watch[:5], 1):
        name = a.get("name", "?")
        slug = a.get("slug", "?")
        score = a.get("score", 0)
        floor = a.get("floor_price", {})
        floor_val = float(floor.get("value", 0) or 0)
        
        lines.append(f"🟡 #{i} *WATCH* — {name}")
        lines.append(f"   Slug: @{slug} | Score: {score}/100 | Floor: {floor_val:.3f} {floor.get('currency', 'ETH')}")
    
    lines.append(f"\nScanned: {len(analyses)} | BUY: {len(buy)} | WATCH: {len(watch)}")
    
    return "\n".join(lines)


# ── Mint Execution (Future) ───────────────────────────────────────────

def prepare_mint_tx(contract_address, token_id, amount=1):
    """Prepare a mint transaction for a contract.
    
    TODO: Implement actual tx construction using:
    1. Contract ABI to call the mint function
    2. Estimate gas for the mint call
    3. Sign with wallet private key
    
    Args:
        contract_address: NFT contract address
        token_id: Specific token ID to mint (None for random next)
        amount: Number of tokens to mint
    
    Returns:
        dict with:
          - to: contract address
          - data: encoded mint function call
          - value: ETH amount to send
          - gas_estimate: estimated gas limit
          - gas_price: current gas price
    """
    # TODO: Full implementation
    return {
        "contract": contract_address,
        "status": "ready_to_mint",
        "note": "Requires contract ABI + wallet key",
    }


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--mint":
        # Mint mode
        contract = sys.argv[2] if len(sys.argv) > 2 else None
        if contract:
            tx = prepare_mint_tx(contract)
            print(json.dumps(tx, indent=2))
    else:
        # Scan mode
        print("NFT Hot Contract Detector")
        print("=" * 50)
        
        results = scan_nft_collections(timeframe="one_hour", limit=20)
        report = format_nft_report(results)
        print(report)
        
        # Also scan new contracts
        print("\n--- New Contracts ---")
        new_contracts = scan_new_contracts(10)
        print(f"Found {len(new_contracts)} new contracts")
        for c in new_contracts[:5]:
            print(f"  {c.get('contract_address', '?')[:20]}... created at {c.get('timestamp', 0)}")
