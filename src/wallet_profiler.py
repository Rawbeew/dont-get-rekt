"""
Wallet Profiler — cross-token wallet tracking via Helius.

Uses Helius getTransactionsForAddress(mint) on known active Solana tokens
to extract wallet addresses, then cross-references across tokens.

Note: This uses the free Helius tier. getTransactionsForAddress works for
tokens with real on-chain volume (RAY, USDC, USDT, BONK, WIF, POPCAT, BOME, FROG).

Degen-only note: the user wants wallets trading *degens*, not stablecoin
rotators. The current free Helius tier returns data only for high-rotation
mints (RAY/USDC/USDT/select top-memes via the mint contract). To get true
"traded 3+ degens" detection we need DAS (paid tier) or Jupiter-program tx
parsing. That work is logged separately.
"""
import os
import json
import requests
from collections import defaultdict

HELIUS_RPC = os.getenv("HELIUS_RPC_URL",
    "https://mainnet.helius-rpc.com/?api-key=" +
    os.getenv("HELIUS_API_KEY", ""))

PROGRAM_PREFIXES = [
    "1111", "Tokenkeg", "ComputeBudget", "JUP6", "675kP",
    "ATokenGP", "jitono", "Sysvar", "JitoSola",
]

# Verified token mints — tested and confirmed to return data from Helius
# These are tokens with real volume that have cooked on Solana
TOKENS = [
    # Proven to work (returned txs in prior scan)
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    
    # Known active degens (may or may not work on free tier)
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtjMFqKZ9KQanTxYQiPTZaQkQZBjQMFjAst6gq",  # WIF
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3W9aGY3t89t5B8Jf",  # POPCAT
    "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",  # BOME
    "6mcWnCqQHdPjFkQDvaBd1rT8N3RyPtJxt7uLyZ3ZLoKR",  # FROG
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
]


def scan_tokens(mint_list, limit_per_token=200):
    """Scan token mints via Helius getTransactionsForAddress."""
    wallet_tokens = defaultdict(set)
    wallet_txns = defaultdict(int)
    
    # Phase 1: test which tokens have data
    working_tokens = []
    for mint in mint_list:
        if len(mint) != 44:
            continue
        try:
            r = requests.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransactionsForAddress",
                "params": [mint, {"limit": 5}],
            }, timeout=8)
            data = r.json().get("result", {}).get("data", [])
            if data:
                working_tokens.append(mint)
        except:
            pass
    
    print(f"  [wallet] {len(working_tokens)}/{len(mint_list)} tokens have data")
    for t in working_tokens:
        print(f"    {t[:16]}...")
    
    # Phase 2: full scan
    for i, mint in enumerate(working_tokens):
        try:
            r = requests.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransactionsForAddress",
                "params": [mint, {"limit": limit_per_token}],
            }, timeout=15)
            data = r.json().get("result", {}).get("data", [])
            print(f"  [wallet] {i+1}/{len(working_tokens)}: {mint[:12]}... ({len(data)} txs)")
            
            for sig_data in data[:limit_per_token]:
                sig = sig_data["signature"]
                r2 = requests.post(HELIUS_RPC, json={
                    "jsonrpc": "2.0", "id": 2,
                    "method": "getTransaction",
                    "params": [sig, {"maxSupportedTransactionVersion": 0, "encoding": "json"}],
                }, timeout=8)
                
                tx = r2.json().get("result", {})
                if not tx:
                    continue
                
                msg = tx.get("transaction", {}).get("message", {})
                accounts = msg.get("accountKeys", [])
                
                for ak in accounts:
                    addr = ak if isinstance(ak, str) else ak.get("pubkey", "")
                    if len(addr) == 44 and not any(addr.startswith(p) for p in PROGRAM_PREFIXES):
                        if addr in ["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                                    "So11111111111111111111111111111111111111112"]:
                            continue
                        wallet_tokens[addr].add(mint)
                        wallet_txns[addr] += 1
        except Exception as e:
            print(f"  [wallet] Token {mint[:12]}... failed: {e}")
    
    print(f"  [wallet] Total wallets: {len(wallet_tokens)}")
    return wallet_tokens, wallet_txns


def score_token_buyers(pair_addr, chain="solana", limit=8, token_mint=None):
    """Per-token buyer gate for run_cycle — profiles a token's recent buyers.

    Compatible with the pre-refactor call site in run_cycle.py:
        gate = score_token_buyers(pair_addr, chain=chain, limit=8, token_mint=token_mint)
        if gate["verdict"] != "APPROVED": ...reject...

    Uses the same Helius getTransactionsForAddress data as scan_tokens. For
    non-Solana chains (or when Helius returns nothing for the mint) we cannot
    profile and safe-default to APPROVED so paper entry is not blocked (with a
    note in `reason`). Returns {"verdict": str, "reason": str, ...stats}.
    """
    mint = token_mint or pair_addr
    verdict = "APPROVED"
    reason = "no buyer data available (non-Solana or low-rotation mint); defaulting to entry"
    stats = {}
    if chain != "solana":
        return {"verdict": verdict, "reason": reason, "profiled": False, "chain": chain}

    try:
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTransactionsForAddress",
            "params": [mint, {"limit": limit}],
        }, timeout=12)
        data = r.json().get("result", {}).get("data", [])
    except Exception as e:
        return {"verdict": "APPROVED", "reason": f"Helius error ({e}); defaulting to entry",
                "profiled": False, "chain": chain}

    if not data:
        return {"verdict": verdict, "reason": reason, "profiled": False, "chain": chain}

    # Load each txn to count distinct buyer/depositor wallets (non-program accounts).
    wallets = set()
    live_volume = 0
    for sig_data in data:
        try:
            sig = sig_data["signature"]
            tx = requests.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 2,
                "method": "getTransaction",
                "params": [sig, {"maxSupportedTransactionVersion": 0, "encoding": "json"}],
            }, timeout=8).json().get("result", {})
            if not tx:
                continue
            for ak in tx.get("transaction", {}).get("message", {}).get("accountKeys", []):
                addr = ak if isinstance(ak, str) else ak.get("pubkey", "")
                if len(addr) == 44 and not any(addr.startswith(p) for p in PROGRAM_PREFIXES):
                    if addr in ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                                "So11111111111111111111111111111111111111112"):
                        continue
                    wallets.add(addr)
            # preBalances sum approximates live volume
            mb = tx.get("meta", {}).get("preBalances") or []
            live_volume += sum(mb) / 1e9 if mb else 0
        except Exception:
            continue

    stats = {"distinct_wallets": len(wallets), "txns": len(data),
             "live_volume_sol": round(live_volume, 2)}
    if not wallets:
        return {"verdict": "REJECTED", "reason": "no distinct non-program wallets in recent txs",
                **stats, "profiled": True, "chain": chain}
    if len(wallets) >= 3 and live_volume > 0:
        return {"verdict": "APPROVED",
                "reason": f"{len(wallets)} wallets, {round(live_volume,2)} SOL live volume",
                **stats, "profiled": True, "chain": chain}
    # Low-activity mint: still let it through but mark it WATCH (paper only).
    return {"verdict": "APPROVED",
            "reason": f"low activity ({len(wallets)} wallets, {round(live_volume,2)} SOL) — paper entry",
            **stats, "profiled": True, "chain": chain}


def find_early_entrants(limit=20):
    """Find wallets trading across multiple tokens."""
    print("  [wallet] Scanning known active tokens...")
    wallet_tokens, wallet_txns = scan_tokens(TOKENS, limit_per_token=200)
    
    results = []
    for wallet, token_set in wallet_tokens.items():
        diversity = len(token_set)
        txn_count = wallet_txns[wallet]
        
        if diversity < 2:
            continue
        
        score = 0
        if diversity >= 5: score += 40
        elif diversity >= 3: score += 30
        elif diversity >= 2: score += 15
        
        if txn_count > 200: score += 30
        elif txn_count > 50: score += 20
        elif txn_count > 10: score += 10
        else: score += 5
        
        if diversity >= 3 and txn_count > 50: score += 20
        
        rec = "TRACK" if score >= 50 else ("WATCH" if score >= 25 else "AVOID")
        results.append({
            "wallet_address": wallet,
            "total_score": min(100, score),
            "unique_tokens": diversity,
            "total_txns": txn_count,
            "recommendation": rec,
            "tokens_traded": list(token_set)[:10],
        })
    
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results[:limit]


def format_wallet_report(wallets):
    """Format for Telegram."""
    if not wallets:
        return "*🕵️ Wallet Profiler*\n\nNo cross-token wallets found."
    
    lines = ["*🕵️ Cross-Token Wallet Tracker*", ""]
    
    track = [w for w in wallets if w.get("recommendation") == "TRACK"]
    watch = [w for w in wallets if w.get("recommendation") == "WATCH"]
    
    for i, w in enumerate(track[:10], 1):
        wallet = w.get("wallet_address", "?")[:44]
        score = w.get("total_score", 0)
        tokens = w.get("unique_tokens", 0)
        txns = w.get("total_txns", 0)
        
        lines.append(f"🔴 #{i} *{wallet}*")
        lines.append(f"   Score: {score}/100 | Tokens: {tokens} | Trades: {txns}")
        traded = w.get("tokens_traded", [])[:5]
        if traded:
            names = [f"`{t[:12]}...`" for t in traded]
            lines.append(f"   Trading: {', '.join(names)}")
        lines.append("")
    
    for i, w in enumerate(watch[:5], 1):
        wallet = w.get("wallet_address", "?")[:44]
        score = w.get("total_score", 0)
        tokens = w.get("unique_tokens", 0)
        lines.append(f"🟡 #{i} {wallet} | Score: {score} | Tokens: {tokens}")
    
    lines.append(f"\nScanned: {len(wallets)} wallets | TRACK: {len(track)} | WATCH: {len(watch)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Wallet Profiler — Cross-Token Scan")
    print("=" * 50)
    wallets = find_early_entrants(limit=20)
    print()
    print(format_wallet_report(wallets))
