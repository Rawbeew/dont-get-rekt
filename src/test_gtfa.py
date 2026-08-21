#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

import requests, os, json
from collections import defaultdict

HELIUS_RPC = os.getenv("HELIUS_RPC_URL",
    "https://mainnet.helius-rpc.com/?api-key=" +
    os.getenv("HELIUS_API_KEY", "")
)

# Get BOME mint
r = requests.get("https://api.dexscreener.com/latest/dex/search?q=BOME", timeout=10)
pairs = r.json().get("pairs", [])
bome_pair = None
for p in pairs:
    if p.get("chainId") == "solana" and p.get("baseToken", {}).get("symbol", "").upper() == "BOME":
        bome_pair = p
        break

mint = bome_pair["baseToken"]["address"]
print(f"BOME mint: {mint}")

# getTransactionsForAddress returns signature-level results (data array)
r2 = requests.post(HELIUS_RPC, json={
    "jsonrpc": "2.0", "id": 1,
    "method": "getTransactionsForAddress",
    "params": [mint, {"limit": 5}],
}, timeout=10)
data = r2.json().get("result", {}).get("data", [])
print(f"Transactions: {len(data)}")

# Now fetch FULL transaction details for each
for i, sig_data in enumerate(data[:3]):
    sig = sig_data["signature"]
    block_time = sig_data.get("blockTime", 0)
    print(f"\n--- Tx {i+1}: {sig[:30]}... blockTime={block_time}")
    
    r3 = requests.post(HELIUS_RPC, json={
        "jsonrpc": "2.0", "id": 2,
        "method": "getTransaction",
        "params": [sig, {"maxSupportedTransactionVersion": 0, "encoding": "json"}],
    }, timeout=10)
    tx = r3.json().get("result", {})
    if not tx:
        print("  No tx data")
        continue
    
    meta = tx.get("meta", {})
    signers = meta.get("signers", [])
    print(f"  Signers: {len(signers)}")
    
    for s in signers:
        if isinstance(s, dict):
            wallet = s.get("publicKey", "")
        else:
            wallet = str(s)
        print(f"  Wallet: {wallet[:44]}")
    
    # Check token transfers
    for tt in meta.get("tokenTransfers", []):
        from_user = tt.get("fromUserAccount", "")
        to_user = tt.get("toUserAccount", "")
        print(f"  Transfer: {from_user[:20]}... -> {to_user[:20]}...")
        print(f"    {tt.get('tokenAmount', '0')} {tt.get('mint', '')[:12]}...")
    
    # Check top-level instructions
    msg = tx.get("transaction", {}).get("message", {})
    for instr in msg.get("instructions", []):
        pid_idx = instr.get("programIdIndex", -1)
        accounts = instr.get("accounts", [])
        if pid_idx < len(msg.get("accountKeys", [])):
            pid = msg["accountKeys"][pid_idx]
            pid = pid if isinstance(pid, str) else pid.get("pubkey", "")
            print(f"  Instr: program={pid[:20]}... accounts={len(accounts)}")
            for acct_idx in accounts:
                if acct_idx < len(msg.get("accountKeys", [])):
                    acct = msg["accountKeys"][acct_idx]
                    acct = acct if isinstance(acct, str) else acct.get("pubkey", "")
                    print(f"    acct: {acct[:20]}...")
