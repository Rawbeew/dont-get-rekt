"""
Unified swap execution — buy tokens on any chain, anti-MEV protected.

Architecture:
- Solana: Jupiter v6 (best aggregator, has built-in anti-MEV)
- EVM chains (Base, BSC, Arb, ETH): 1inch API v6 (best for L2s, MEV-resistant)

Anti-sandwich measures:
1. Private RPC endpoints (Helius for Solana, Alchemy for EVMs)
   → No public mempool exposure
2. Random delay 0.5-3s before broadcast
   → Breaks pattern-based MEV bots
3. Dynamic compute limits (Solana)
   → Auto-adjusts gas, no overpaying for priority
4. Small trade sizes (<0.1 SOL equivalent)
   → Harder to sandwich profitably
5. No visible pending tx on public RPC
   → Private RPC = your tx never hits the public mempool

Requires:
- pip install web3 eth_account
- Private RPC keys (Alchemy, Helius)
- Optional: 1inch API key (free at portal.1inch.dev)
"""
import os
import time
import json
import random
import requests
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────
HELIUS_RPC = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=" + os.getenv("HELIUS_API_KEY", ""))
ALCHEMY_KEY = os.getenv("ALCHEMY_KEY", "")
ONEINCH_KEY = os.getenv("ONEINCH_API_KEY", "")
WALLET_PRIVATE_KEY = os.getenv("HERMES_WALLET_PRIVATE_KEY", "")

# ── Private RPCs (no public mempool) ──────────────────────────────────
CHAIN_RPC = {
    "solana": HELIUS_RPC,
    "base": f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "arbitrum": f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "bsc": f"https://bsc-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "ethereum": f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "polygon": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "avalanche": f"https://avax-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
}

# ── Native tokens per chain ───────────────────────────────────────────
NATIVE_TOKENS = {
    "solana": "So11111111111111111111111111111111111111112",
    "base": "0x4200000000000000000000000000000000000006",  # WETH
    "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    "bsc": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "polygon": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC
    "avalanche": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
}

# ── 1inch API (EVM chains) ───────────────────────────────────────────
ONEINCH_URL = "https://api.1inch.dev/swap/v6.0"
ONEINCH_HEADERS = {"Authorization": f"Bearer {ONEINCH_KEY}"} if ONEINCH_KEY else {}

# ── Jupiter API (Solana) ─────────────────────────────────────────────
JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP = "https://quote-api.jup.ag/v6/swap"


# ── Core functions ────────────────────────────────────────────────────

def get_wallet_address():
    """Derive wallet address from private key."""
    try:
        from solders.keypair import Keypair
        kp = Keypair.from_base58_string(WALLET_PRIVATE_KEY)
        return str(kp.pubkey())
    except ImportError:
        return None


def get_wallet_address_evm(chain):
    """Derive EVM wallet address from private key."""
    try:
        from eth_account import Account
        return Account.from_key(WALLET_PRIVATE_KEY).address
    except ImportError:
        return None


def random_delay():
    """Random delay to break MEV pattern detection."""
    time.sleep(random.uniform(0.5, 3.0))


def swap_solana(mint, amount_sol, slippage_bps=1000):
    """Buy token on Solana via Jupiter. Anti-MEV via private RPC + random delay."""
    print(f"  [swap] SOL→{mint[:12]}: {amount_sol} SOL (Jupiter)")
    
    try:
        from solders.keypair import Keypair
        kp = Keypair.from_base58_string(WALLET_PRIVATE_KEY)
        pubkey = str(kp.pubkey())
        
        # 1. Get quote
        r = requests.get(JUPITER_QUOTE, params={
            "inputMint": NATIVE_TOKENS["solana"],
            "outputMint": mint,
            "amount": str(int(amount_sol * 1e9)),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": "false",
            "maxAccounts": 20,
        }, timeout=15)
        quote = r.json()
        if "error" in quote:
            return {"status": "failed", "error": quote["error"]}
        
        # 2. Get swap tx
        r = requests.post(JUPITER_SWAP, json={
            "quoteResponse": quote,
            "userPublicKey": pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 100000,  # Higher tip for speed
        }, timeout=15)
        swap = r.json()
        if "error" in swap:
            return {"status": "failed", "error": swap["error"]}
        
        tx_b64 = swap.get("swapTransaction", "")
        if not tx_b64:
            return {"status": "failed", "error": "No tx from Jupiter"}
        
        # 3. Simulate (safety check)
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "simulateTransaction",
            "params": [tx_b64, {"encoding": "base64"}],
        }, timeout=10)
        err = r.json().get("result", {}).get("value", {}).get("error")
        if err:
            return {"status": "failed", "error": str(err)[:200]}
        
        # 4. Broadcast via PRIVATE RPC (no mempool exposure)
        random_delay()
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [tx_b64, {"skipPreflight": False, "maxRetries": 3, "encoding": "base64"}],
        }, timeout=30)
        tx_hash = r.json().get("result")
        
        return {
            "status": "success", "tx_hash": str(tx_hash),
            "chain": "solana", "symbol": mint[:12],
            "amount": amount_sol, "type": "solana_jupiter",
        }
    
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def swap_evm(chain, mint, amount_native, slippage_bps=1000):
    """Buy token on EVM chain via 1inch. Anti-MEV via private RPC + random delay."""
    try:
        from web3 import Web3
        from eth_account import Account
        
        w3 = Web3(Web3.HTTPProvider(CHAIN_RPC[chain]))
        account = Account.from_key(WALLET_PRIVATE_KEY)
        wallet_addr = account.address
        input_token = NATIVE_TOKENS.get(chain, "")
        
        # 1inch chain ID
        chain_ids = {"base": 8453, "arbitrum": 42161, "bsc": 56, "ethereum": 1, "polygon": 137, "avalanche": 43114}
        chain_id = chain_ids.get(chain)
        
        print(f"  [swap] {chain.upper()}→{mint[:12]}: {amount_native} {chain} (1inch)")
        
        # Convert amount to wei
        amount_wei = int(amount_native * 1e18)
        
        # 1. Get 1inch quote
        r = requests.get(
            f"{ONEINCH_URL}/{chain_id}/swap",
            params={
                "tokenFrom": input_token,
                "tokenTo": mint,
                "amount": str(amount_wei),
                "fromAddress": wallet_addr,
                "slippage": str(slippage_bps // 100),
                "disableEstimate": "true",
            },
            headers=ONEINCH_HEADERS,
            timeout=15,
        )
        quote = r.json()
        if r.status_code != 200 or "tx" not in quote:
            return {"status": "failed", "error": f"1inch quote failed: {json.dumps(quote)[:200]}"}
        
        tx_data = quote["tx"]
        
        # 2. Build and sign transaction
        tx = {
            "to": w3.to_checksum_address(tx_data["to"]),
            "data": tx_data["data"],
            "value": int(tx_data.get("value", 0), 16) if isinstance(tx_data.get("value"), str) else tx_data.get("value", 0),
            "gas": int(tx_data.get("gas", 500000)),
            "gasPrice": w3.eth.gas_price,
            "nonce": w3.eth.get_transaction_count(wallet_addr),
            "chainId": chain_id,
        }
        
        signed = account.sign_transaction(tx)
        
        # 3. Broadcast via PRIVATE RPC
        random_delay()
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        return {
            "status": "success",
            "tx_hash": w3.to_hex(tx_hash),
            "chain": chain,
            "symbol": mint[:12],
            "amount": amount_native,
            "type": f"{chain}_1inch",
        }
    
    except ImportError:
        return {"status": "failed", "error": "Install: pip install web3 eth_account"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def swap_any(chain, mint, amount, amount_unit="native"):
    """Unified swap function — route to correct chain/executor.
    
    Args:
        chain: solana, base, bsc, arbitrum, ethereum, polygon, avalanche
        mint: Token address
        amount: Amount of native token to spend
        amount_unit: "native" (SOL, ETH, BNB, etc.) or "token"
    
    Returns:
        dict with status, tx_hash, chain, symbol, amount, type
    """
    if chain == "solana":
        return swap_solana(mint, amount)
    else:
        return swap_evm(chain, mint, amount)


def sell_token(chain, mint, token_amount):
    """Sell token back to native. Anti-MEV via 1inch/Jupiter + random delay."""
    if chain == "solana":
        return sell_solana(mint, token_amount)
    else:
        return sell_evm(chain, mint, token_amount, token_amount)  # sell all


def sell_solana(mint, token_amount):
    """Sell SPL token for SOL via Jupiter."""
    print(f"  [sell] SOLANA sell {mint[:12]}")
    try:
        from solders.keypair import Keypair
        kp = Keypair.from_base58_string(WALLET_PRIVATE_KEY)
        pubkey = str(kp.pubkey())
        
        # Jupiter sell quote (token → SOL)
        r = requests.get(JUPITER_QUOTE, params={
            "inputMint": mint,
            "outputMint": NATIVE_TOKENS["solana"],
            "amount": str(int(token_amount)),
            "slippageBps": "3000",  # 30% for meme coins
            "onlyDirectRoutes": "false",
            "maxAccounts": 20,
        }, timeout=15)
        quote = r.json()
        if "error" in quote:
            return {"status": "failed", "error": quote["error"]}
        
        # Get swap tx
        r = requests.post(JUPITER_SWAP, json={
            "quoteResponse": quote,
            "userPublicKey": pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 100000,
        }, timeout=15)
        swap = r.json()
        if "error" in swap:
            return {"status": "failed", "error": swap["error"]}
        
        tx_b64 = swap.get("swapTransaction", "")
        if not tx_b64:
            return {"status": "failed", "error": "No tx from Jupiter"}
        
        # Simulate
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "simulateTransaction",
            "params": [tx_b64, {"encoding": "base64"}],
        }, timeout=10)
        if r.json().get("result", {}).get("value", {}).get("error"):
            return {"status": "failed", "error": "Simulation failed"}
        
        # Broadcast via private RPC
        random_delay()
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [tx_b64, {"skipPreflight": False, "maxRetries": 3, "encoding": "base64"}],
        }, timeout=30)
        tx_hash = r.json().get("result")
        
        return {"status": "success", "tx_hash": str(tx_hash), "chain": "solana", "type": "solana_sell_jupiter"}
    
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def sell_evm(chain, mint, token_amount, amount_native=None):
    """Sell token on EVM for native via 1inch."""
    try:
        from web3 import Web3
        from eth_account import Account
        
        w3 = Web3(Web3.HTTPProvider(CHAIN_RPC[chain]))
        account = Account.from_key(WALLET_PRIVATE_KEY)
        wallet_addr = account.address
        
        chain_ids = {"base": 8453, "arbitrum": 42161, "bsc": 56, "ethereum": 1}
        chain_id = chain_ids.get(chain)
        
        # Get token decimals and convert amount
        token_contract = w3.eth.contract(address=w3.to_checksum_address(mint))
        decimals = token_contract.functions.decimals().call()
        amount_wei = int(token_amount * (10 ** decimals))
        
        # 1inch sell quote
        r = requests.get(
            f"{ONEINCH_URL}/{chain_id}/swap",
            params={
                "tokenFrom": mint,
                "tokenTo": NATIVE_TOKENS.get(chain, ""),
                "amount": str(amount_wei),
                "fromAddress": wallet_addr,
                "slippage": "30",  # 30% for meme coins
                "disableEstimate": "true",
            },
            headers=ONEINCH_HEADERS,
            timeout=15,
        )
        quote = r.json()
        if r.status_code != 200 or "tx" not in quote:
            return {"status": "failed", "error": f"1inch quote failed"}
        
        tx_data = quote["tx"]
        
        # Sign
        tx = {
            "to": w3.to_checksum_address(tx_data["to"]),
            "data": tx_data["data"],
            "value": int(tx_data.get("value", 0), 16) if isinstance(tx_data.get("value"), str) else tx_data.get("value", 0),
            "gas": int(tx_data.get("gas", 500000)),
            "gasPrice": w3.eth.gas_price,
            "nonce": w3.eth.get_transaction_count(wallet_addr),
            "chainId": chain_id,
        }
        
        signed = account.sign_transaction(tx)
        
        # Broadcast
        random_delay()
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        
        return {"status": "success", "tx_hash": w3.to_hex(tx_hash), "chain": chain, "type": f"{chain}_sell_1inch"}
    
    except ImportError:
        return {"status": "failed", "error": "pip install web3 eth_account"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ── Auto-sell hook for auto_trade.py ──────────────────────────────────
# This is what auto_trade.py calls for sells:

def auto_sell(chain, mint, amount, amount_unit="token"):
    """Call from auto_trade.py to sell a position."""
    if chain == "solana":
        return sell_solana(mint, amount)
    else:
        return sell_evm(chain, mint, amount)
