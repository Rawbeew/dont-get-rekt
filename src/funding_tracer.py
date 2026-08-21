"""
Funding source tracer — the insider detector.
 Instead of profiling individual wallets, this traces the MONEY FLOW:
 1. Get buyers for multiple tokens
 2. For each buyer wallet, check who funded them (SOL transfers IN)
 3. Find wallets that funded 5+ buyer wallets across different tokens
 That funder is the insider. They spread SOL across satellite wallets
 to buy into tokens before they pump.

 Usage:
   from funding_tracer import find_insiders
   insiders = find_insiders(token_list)
"""
import requests
import time
from collections import defaultdict
from config import (
    SOLANA_RPC_URL, BASE_RPC_URL,
    SMART_WALLET_MIN_FUNDING_TRANSFERS,
    SCAN_LOOKBACK_TXS,
)

TIMEOUT = 20


def _rpc(url, method, params):
    try:
        r = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": method, "params": params,
        }, timeout=TIMEOUT)
        return r.json().get("result")
    except Exception as e:
        return None


def _get_dexscreener_pairs(token_mint):
    """Get all pair addresses for a token from Dexscreener."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}",
            timeout=10,
        )
        data = r.json()
        pairs = data.get("pairs") or []
        return [p["pairAddress"] for p in pairs], pairs
    except:
        return [], []


def get_buyers_for_token(token_mint, symbol, chain="solana", max_buyers=15):
    """Get buyer wallets for a token by querying the token mint + all pair
    addresses on chain. Returns set of wallet addresses (fee payers who
    received the token)."""
    rpc_url = SOLANA_RPC_URL if chain == "solana" else BASE_RPC_URL
    pair_addrs, _ = _get_dexscreener_pairs(token_mint)
    all_addrs = [token_mint] + pair_addrs
    buyers = set()
    sigs_seen = set()

    for addr in all_addrs:
        if len(buyers) >= max_buyers:
            break
        sigs = _rpc(rpc_url, "getSignaturesForAddress", [addr, {"limit": 30}])
        if not sigs:
            continue

        for s in sigs:
            if s.get("err") or s["signature"] in sigs_seen:
                continue
            sigs_seen.add(s["signature"])

            tx = _rpc(rpc_url, "getTransaction", [
                s["signature"],
                {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}
            ])
            if not tx:
                continue

            meta = tx.get("meta", {})
            msg = tx.get("transaction", {}).get("message", {})
            keys = msg.get("accountKeys", [])
            if not keys:
                continue

            fee_payer = keys[0]
            if isinstance(fee_payer, dict):
                fee_payer = fee_payer.get("pubkey", "")

            post_bal = meta.get("postTokenBalances", [])
            pre_bal = meta.get("preTokenBalances", [])

            for b in post_bal:
                if b.get("mint") != token_mint:
                    continue
                post_amt = b.get("uiTokenAmount", {}).get("uiAmount")
                acct_idx = b.get("accountIndex", -1)
                pre_amt = None
                for pb in pre_bal:
                    if pb.get("accountIndex") == acct_idx:
                        pre_amt = pb.get("uiTokenAmount", {}).get("uiAmount")
                        break

                if post_amt and (pre_amt is None or post_amt > (pre_amt or 0)):
                    if fee_payer and fee_payer != addr and fee_payer != token_mint:
                        buyers.add(fee_payer)

            time.sleep(0.1)

    return list(buyers)


def trace_funding(wallet, rpc_url=None, limit=30):
    """For a wallet, find who sent SOL to it (system program transfers IN).
    Returns set of funder addresses."""
    url = rpc_url or SOLANA_RPC_URL
    funders = set()

    sigs = _rpc(url, "getSignaturesForAddress", [wallet, {"limit": limit}])
    if not sigs:
        return funders

    for s in sigs:
        if s.get("err"):
            continue
        tx = _rpc(url, "getTransaction", [
            s["signature"],
            {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}
        ])
        if not tx:
            continue

        meta = tx.get("meta", {})
        msg = tx.get("transaction", {}).get("message", {})
        instructions = msg.get("instructions", [])

        # Check top-level instructions for SOL transfers
        for inst in instructions:
            parsed = inst.get("parsed", {})
            if not parsed:
                continue
            if parsed.get("type") == "transfer":
                info = parsed.get("info", {})
                source = info.get("source", "")
                dest = info.get("destination", "")
                if dest == wallet and source and source != wallet:
                    funders.add(source)

        # Check inner instructions too
        inner = meta.get("innerInstructions", [])
        for grp in inner:
            for inst in grp.get("instructions", []):
                parsed = inst.get("parsed", {})
                if not parsed:
                    continue
                if parsed.get("type") == "transfer":
                    info = parsed.get("info", {})
                    source = info.get("source", "")
                    dest = info.get("destination", "")
                    if dest == wallet and source and source != wallet:
                        funders.add(source)

        time.sleep(0.1)

    return funders


def find_insiders(token_list, chain="solana", max_buyers_per_token=15):
    """Full insider detection pipeline.
    token_list: [(token_mint, symbol), ...]
    Returns dict with:
    - insiders: {funder_wallet: {tokens, funded_wallets, score}}
    - per_token_buyers: {symbol: [wallets]}
    """
    print(f"\n{'='*60}")
    print(f"INSIDER DETECTION — {len(token_list)} tokens, chain={chain}")
    print(f"{'='*60}")

    rpc_url = SOLANA_RPC_URL if chain == "solana" else BASE_RPC_URL

    # 1. Get buyers for each token
    per_token_buyers = {}
    all_buyers = set()

    for token_mint, symbol in token_list:
        print(f"\n  [{symbol}] getting buyers...")
        buyers = get_buyers_for_token(token_mint, symbol, chain=chain,
                                       max_buyers=max_buyers_per_token)
        per_token_buyers[symbol] = buyers
        all_buyers.update(buyers)
        print(f"  [{symbol}] {len(buyers)} buyers found")

    if not all_buyers:
        print("\n  No buyers found across any token.")
        return {"insiders": {}, "per_token_buyers": per_token_buyers}

    print(f"\n  Total unique buyer wallets: {len(all_buyers)}")

    # 2. Trace funding sources for each buyer
    print(f"\n  Tracing funding sources for {len(all_buyers)} wallets...")
    funding_map = defaultdict(set)  # funder -> set of buyer wallets

    for i, wallet in enumerate(all_buyers):
        print(f"    [{i+1}/{len(all_buyers)}] {wallet[:16]}...", end="")
        funders = trace_funding(wallet, rpc_url=rpc_url, limit=20)
        print(f" -> {len(funders)} funders")
        for funder in funders:
            funding_map[funder].add(wallet)
        time.sleep(0.1)

    # 3. Find funders who funded 5+ wallets (the insiders)
    print(f"\n  {'='*50}")
    print(f"  FUNDING ANALYSIS")
    print(f"  {'='*50}")
    print(f"  Total funding sources: {len(funding_map)}")

    # Also check which tokens each funder's satellites bought
    # A funder is an INSIDER if their funded wallets bought different tokens
    insiders = {}
    for funder, funded_wallets in funding_map.items():
        if len(funded_wallets) < SMART_WALLET_MIN_FUNDING_TRANSFERS:
            continue

        # Which tokens did these funded wallets buy?
        tokens_involved = set()
        for symbol, buyers in per_token_buyers.items():
            for w in funded_wallets:
                if w in buyers:
                    tokens_involved.add(symbol)

        insiders[funder] = {
            "funded_wallets": list(funded_wallets),
            "num_funded": len(funded_wallets),
            "tokens": list(tokens_involved),
            "num_tokens": len(tokens_involved),
            "score": len(funded_wallets) * len(tokens_involved),
        }

    if insiders:
        print(f"\n  INSIDERS ({len(insiders)} found):")
        for funder, info in sorted(insiders.items(), key=lambda x: x[1]["score"], reverse=True):
            print(f"\n    FUNDER: {funder}")
            print(f"    Funded {info['num_funded']} wallets across {info['num_tokens']} tokens")
            print(f"    Tokens: {', '.join(info['tokens'])}")
            print(f"    Score: {info['score']}")
            print(f"    Satellite wallets (first 5):")
            for w in info["funded_wallets"][:5]:
                print(f"      {w}")
    else:
        print(f"\n  No insiders found (need {SMART_WALLET_MIN_FUNDING_TRANSFERS}+ funded wallets)")
        # Show top funders anyway
        print(f"\n  Top funders (any count):")
        for funder, targets in sorted(funding_map.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
            tokens = set()
            for symbol, buyers in per_token_buyers.items():
                for w in targets:
                    if w in buyers:
                        tokens.add(symbol)
            print(f"    {funder[:16]}... -> {len(targets)} wallets, tokens: {', '.join(tokens) or 'none'}")

    return {
        "insiders": insiders,
        "per_token_buyers": per_token_buyers,
        "funding_map": {k: list(v) for k, v in funding_map.items()},
    }
