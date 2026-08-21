#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from feeds.dex_feeds import fetch_all_dex
from wallet_profiler import find_early_entrants, format_wallet_report

pairs = fetch_all_dex()[:5]
solana = [p for p in pairs if p.get("chain", "") == "solana"
          and p.get("address", "") != "So11111111111111111111111111111111111111112"]
print(f"Got {len(solana)} Solana pairs")

for p in solana[:15]:
    sym = p.get("symbol", "?")
    price = p.get("price_usd", 0)
    liq = p.get("liquidity_usd", 0)
    ch24 = p.get("price_change_24h", 0)
    vol = p.get("vol_24h", 0)
    print(f"  {sym:15s} ${price:>12}  liq: ${liq:>10,.0f}  24h: {ch24:+.1f}%  vol: ${vol:>12,.0f}")

print()
print("=== WALLET SCAN ===")
wallets = find_early_entrants(solana, limit=20)
print(format_wallet_report(wallets))
