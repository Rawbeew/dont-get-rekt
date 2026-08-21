"""
Dexscreener scraper using stealth browser.
Extracts structured token data from Dexscreener's JS-rendered pages.

Bypasses anti-bot protections by using Playwright with stealth patches.

Usage:
    from dex_scraper import fetch_dexscreener_tokens
    tokens = fetch_dexscreener_tokens("solana")  # trending tokens
    
    # Specific query
    tokens = fetch_dexscreener_tokens("solana", query="degs")
    
    # All chains
    for chain in ["solana", "base", "bsc", "ethereum", "arbitrum"]:
        tokens = fetch_dexscreener_tokens(chain)

CLI:
    python -m feeds.dex_scraper solana --json  # trending
    python -m feeds.dex_scraper solana degs --json  # specific pair
"""
import re
import json
import sys
import asyncio
from datetime import datetime, timezone

from scraper import scrape_page_sync


def parse_dexscreener_data(text):
    """Parse raw scraped text from Dexscreener into structured token data.
    
    Dexscreener renders token rows like:
        Hide pair
        #1
        TOADLAYER
        /
        SOL
        TOADLAYER
        500
        $0.0001029
        19h
        95,147
        $3.7M
        9,883
        ...
        $25K
        $102K
    """
    tokens = []
    
    # Split by "Hide pair" markers (each token starts with "Hide pair")
    blocks = text.split("Hide pair")
    
    for block in blocks[1:]:  # skip first empty block
        try:
            lines = block.strip().split('\n')
            
            # Extract symbol and chain
            symbol = None
            chain_symbol = None
            name = None
            
            # Find token symbol (the all-caps/short name at top of block)
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                if symbol is None and i > 0:
                    # First meaningful text after "Hide pair" and rank number
                    if line.replace('/', '').strip().isalpha() or line.startswith('$') == False:
                        # Could be symbol
                        if len(line) <= 20 and not line.startswith('$') and not line.endswith('h') and not line.endswith('d'):
                            symbol = line
                            break
            
            if not symbol:
                # Try to find it after rank number and slash
                for line in lines:
                    l = line.strip()
                    if l and not l.startswith('$') and not l.endswith('h') and not l.endswith('d') and not l.isdigit() and len(l) <= 20 and len(l) >= 2:
                        # Skip known non-token text
                        skip_words = ['SOL', 'NEW', 'TOP', 'PUMP', 'FROG', 'Cat', 'Dog', 
                                     'Trending', 'Gainers', 'Top', 'Loading', 'Filters',
                                     'TOKEN', 'PRICE', 'AGE', 'TXNS', 'VOLUME', 'TRADERS',
                                     'LIQUIDITY', 'MCAP', 'Rank']
                        if l not in skip_words and not l.startswith('#'):
                            symbol = l
                            break
            
            # Find price ($X.XXXX format)
            price_match = re.search(r'\$([0-9,]+\.?[0-9]*)', block)
            price = float(price_match.group(1).replace(',', '')) if price_match else 0
            
            # Find age (Xh, Xd, Xw, Xmo)
            age_match = re.search(r'(\d+[hdmw])', block)
            age = age_match.group(1) if age_match else ""
            
            # Find transactions
            txn_match = re.search(r'(\d+(?:,\d+)*)\n', block)  # transactions count
            txns = int(txn_match.group(1).replace(',', '')) if txn_match else 0
            
            # Find volume ($XM)
            vol_match = re.search(r'\$([0-9.]+[BKMG])', block)
            vol_str = vol_match.group(1) if vol_match else "0"
            vol = _parse_money(vol_str)
            
            # Find liquidity ($XK)
            liq_match = re.findall(r'\$([0-9.]+[BKMG])', block)
            liq = _parse_money(liq_match[0]) if len(liq_match) > 1 else 0
            
            # Find market cap ($XM)
            mcap = _parse_money(liq_match[-1]) if len(liq_match) >= 2 else 0
            
            # Find price changes
            changes = re.findall(r'([+-]?[\d.]+)%', block)
            # Remove money changes (they use $ prefix in regex above)
            price_changes = [float(c) for c in changes if c]
            
            # 5M, 1H, 6H, 24H changes
            change_5m = price_changes[0] if len(price_changes) > 0 else 0
            change_1h = price_changes[1] if len(price_changes) > 1 else 0
            change_6h = price_changes[2] if len(price_changes) > 2 else 0
            change_24h = price_changes[3] if len(price_changes) > 3 else 0
            
            if symbol:
                tokens.append({
                    "symbol": symbol,
                    "name": name or symbol,
                    "price": price,
                    "age": age,
                    "txns": txns,
                    "volume_24h": vol,
                    "traders": int(txns / 10) if txns > 0 else 0,  # estimate
                    "price_change_5m": change_5m,
                    "price_change_1h": change_1h,
                    "price_change_6h": change_6h,
                    "price_change_24h": change_24h,
                    "liquidity_usd": liq,
                    "market_cap": mcap,
                    "chain": "solana",  # inferred from URL
                    "url": "https://dexscreener.com/solana/" + symbol.lower(),
                })
        
        except Exception as e:
            continue
    
    return tokens[:30]  # Top 30


def _parse_money(s):
    """Parse money string like $3.7M, $25K to float."""
    if not s:
        return 0
    s = s.upper()
    multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000, 'T': 1000000000000}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            num = float(s.replace(suffix, ''))
            return num * mult
    try:
        return float(s.replace('$', '').replace(',', ''))
    except:
        return 0


def fetch_dexscreener_tokens(chain="solana", query=None):
    """Fetch trending tokens from Dexscreener via stealth browser.
    
    Args:
        chain: solana, base, bsc, ethereum, arbitrum, polygon, etc.
        query: optional token query string
    
    Returns:
        list of token dicts with structured data
    """
    base = f"https://dexscreener.com/{chain}"
    if query:
        base += f"/{query}"
    
    text = scrape_page_sync(base, timeout=45000)
    content = text['content']
    
    if isinstance(content, list):
        # Extract text from list items
        content = " ".join(item.get('text', '') for item in content)
    
    tokens = parse_dexscreener_data(content)
    
    for t in tokens:
        t['chain'] = chain
        t['url'] = f"https://dexscreener.com/{chain}/{query}" if query else f"https://dexscreener.com/{chain}"
    
    return tokens


def fetch_all_dex_chains():
    """Fetch trending tokens from all major chains."""
    chains = ["solana", "base", "bsc", "ethereum", "arbitrum", "polygon", "avalanche"]
    all_tokens = []
    
    for chain in chains:
        try:
            tokens = fetch_dexscreener_tokens(chain)
            all_tokens.extend(tokens)
        except Exception as e:
            print(f"  [dex] {chain} failed: {e}")
    
    return all_tokens


def main():
    """CLI: python -m feeds.dex_scraper [chain] [query] [--json]"""
    args = sys.argv[1:]
    json_output = "--json" in args
    chain = "solana"
    query = None
    
    non_flag_args = [a for a in args if not a.startswith("--")]
    if non_flag_args:
        chain = non_flag_args[0]
        if len(non_flag_args) > 1:
            query = non_flag_args[1]
    
    tokens = fetch_dexscreener_tokens(chain, query)
    
    if json_output:
        print(json.dumps(tokens, indent=2, default=str))
    else:
        print(f"Dexscreener tokens ({chain}, {query or 'trending'}):\n")
        for i, t in enumerate(tokens[:20], 1):
            print(f"{i}. {t['symbol'][:20]:20s} ${t['price']:>15}  liq: ${t['liquidity_usd']:>10,.0f}  vol: ${t['volume_24h']:>12,.0f}  mcap: ${t['market_cap']:>10,.0f}")


if __name__ == "__main__":
    main()
