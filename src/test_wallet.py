#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from feeds.dex_feeds import fetch_all_dex
from wallet_profiler import find_early_entrants, format_wallet_report

pairs = fetch_all_dex()[:5]
solana = [p for p in pairs if p.get("chain", "") == "solana"
          and p.get("address", "") != "So11111111111111111111111111111111111111112"]
print(f"Solana pairs: {len(solana)}")
for p in solana:
    print(f"  {p['symbol']}: {p['address'][:20]}...")

wallets = find_early_entrants(solana, limit=20)
print()
print(format_wallet_report(wallets))
