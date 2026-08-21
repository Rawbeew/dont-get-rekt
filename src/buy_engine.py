"""
Hermes Buy Engine — the bot detects and vets, you execute.

Flow:
1. Bot detects DEX signal → runs safety checks
2. Sends you a Telegram alert with analysis
3. You reply /buy <address> or /buy <symbol>
4. Bot generates Jupiter swap URL with your preferred parameters
5. You click it → swaps via Jupiter/Bonkbot on your own device

No private keys on the VPS. Full control stays with you.

Requires:
- SOLANA_RPC_URL (Helius)
- Jupiter API (public, no key needed)
"""
import time
import json
import requests
from config import SOLANA_RPC_URL, HELIUS_API_KEY

JUPITER_API = "https://quote-api.jup.ag/v6"
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


def check_safety(token_mint):
    """Check a token's on-chain safety before suggesting a buy.
    Uses Helius getAccountInfo to read mint account data.
    
    Returns dict with safety flags.
    """
    result = {
        "token": token_mint,
        "mint_authority": "REVOLED",  # default safe
        "freeze_authority": "DISABLED",  # default safe
        "is_mutable": True,  # default risky
        "supply": 0,
        "decimals": 0,
        "issues": [],
    }

    try:
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getAccountInfo",
            "params": [
                token_mint,
                {"encoding": "jsonParsed"}
            ],
        }, timeout=10)
        data = r.json().get("result", {}).get("value")
        if not data:
            result["issues"].append("Token account not found")
            return result

        parsed = data.get("data", {}).get("parsed", {}).get("info", {})
        
        # Mint authority
        mint_authority = parsed.get("mintAuthority", "")
        if mint_authority:
            result["mint_authority"] = f"ACTIVE ({mint_authority[:12]}...)"
            result["issues"].append("Mint authority NOT revoked")
        else:
            result["mint_authority"] = "REVOLED"

        # Freeze authority
        freeze_authority = parsed.get("freezeAuthority", "")
        if freeze_authority:
            result["freeze_authority"] = f"ENABLED ({freeze_authority[:12]}...)"
            result["issues"].append("Freeze authority NOT disabled")
        else:
            result["freeze_authority"] = "DISABLED"

        # Supply
        result["supply"] = int(parsed.get("supply", "0") or 0)
        result["decimals"] = parsed.get("decimals", 0)

    except Exception as e:
        result["issues"].append(f"Error fetching account: {str(e)[:100]}")

    return result


def verify_nft_metadata(mint_address):
    """Query Helius DAS API for NFT metadata."""
    try:
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": "hermes-nft-verify",
            "method": "getAsset",
            "params": {"id": mint_address},
        }, timeout=15)
        data = r.json().get("result", {})
        if not data:
            return None
        grouping = data.get("grouping", [])
        creators = data.get("creators", [])
        mutable = data.get("mutable", True)
        collection_id = grouping[0].get("group_value") if grouping else None
        is_verified = any(c.get("verified", False) for c in creators)
        return {
            "is_nft": True,
            "name": data.get("content", {}).get("metadata", {}).get("name", "?"),
            "collection": collection_id,
            "mutable": mutable,
            "verified_creator": is_verified,
            "image": data.get("content", {}).get("links", {}).get("image", ""),
        }
    except:
        return None


def get_swap_url(token_mint, amount_sol=0.1, slippage_bps=500):
    """Generate a Jupiter swap URL for manual execution.
    token_mint: the SPL token address to buy
    amount_sol: SOL to spend
    slippage_bps: slippage tolerance (500 = 5%)
    """
    url = f"{JUPITER_API}/quote?inputMint=So11111111111111111111111111111111111111112"
    url += f"&outputMint={token_mint}"
    url += f"&amount={int(amount_sol * 1e9)}"
    url += f"&slippageBps={slippage_bps}"
    
    return url


def send_buy_alert(token_info, safety_result):
    """Send a complete buy alert with safety check results.
    Returns a Telegram-formatted message with clickable Jupiter URL."""
    mint = token_info.get("mint", "")
    symbol = token_info.get("symbol", "?")
    chain = token_info.get("chain", "solana")
    price = float(token_info.get("price_usd", 0) or 0)
    vol_24h = float(token_info.get("vol_24h", 0) or 0)
    liq = float(token_info.get("liquidity_usd", 0) or 0)
    ch_1h = float(token_info.get("price_change_1h", 0) or 0)
    ch_6h = float(token_info.get("price_change_6h", 0) or 0)
    buy_ratio = float(token_info.get("buy_ratio", 0) or 0)
    url = token_info.get("url", "")

    lines = [
        f"*🎯 BUY SIGNAL*",
        f"`{symbol}` ({chain})",
        f"",
        f"*Price:* ${float(price):.10f}".rstrip("0").rstrip(".") if price else "N/A",
        f"*24h:* {ch_1h:+.1f}% | *6h:* {ch_6h:+.1f}%",
        f"*Vol:* ${vol_24h:,.0f} | *Liq:* ${liq:,.0f}",
        f"*Buy ratio:* {buy_ratio:.0%}",
        f"",
    ]

    # Safety check
    lines.append("*Safety Check:*")
    lines.append(f"  Mint auth: `{safety_result['mint_authority']}`")
    lines.append(f"  Freeze: `{safety_result['freeze_authority']}`")
    lines.append(f"  Supply: {safety_result['supply']:,}")
    if safety_result["issues"]:
        lines.append(f"  ⚠️ Issues:")
        for issue in safety_result["issues"][:3]:
            lines.append(f"    - {issue}")
    else:
        lines.append("  ✅ No issues")

    # Jupiter swap URL (clickable)
    lines.append("")
    lines.append(f"*Swap via Jupiter:*")
    lines.append(f"[Buy {symbol}]({get_swap_url(mint, amount_sol=0.1, slippage_bps=1000)})")
    lines.append(f"  [Buy 0.5 SOL]({get_swap_url(mint, amount_sol=0.5, slippage_bps=1000)})")
    lines.append(f"  [Buy 1.0 SOL]({get_swap_url(mint, amount_sol=1.0, slippage_bps=1000)})")
    lines.append("")
    lines.append(f"[Dexscreener]({url})")
    lines.append(f"*PAPER MODE: click link to swap via Jupiter/Bonkbot*")

    return "\n".join(lines)
