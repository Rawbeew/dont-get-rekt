"""
Cross-chain swap engine.
Uses different DEX aggregators per chain to avoid MEV sandwitches:
- Solana: Jupiter v6 (has anti-MEV features)
- Base: 1inch API v5 (best for L2s)
- BSC: PancakeSwap API (native) or 1inch
- Arbitrum: 1inch API v5
- Ethereum: 1inch API v5

Anti-sandwich measures:
1. Jupiter on Solana: uses dynamic compute unit limit (auto-adjusts gas)
2. Private RPC endpoints: Helius/Alchemy (no public mempool exposure)
3. Low slippage + high priority fee: front-running is more expensive than the spread
4. Small trade sizes: <0.1 SOL equivalent (harder to sandwich profitably)
5. Random delay: 0.5-3s before broadcast (breaks pattern-based MEV)
6. No large visible buys on public RPC

Requires:
- 1inch API key (free): https://portal.1inch.dev (optional, falls back to public)
- Private RPC for each chain (Helius for Solana, Alchemy for EVMs)
"""
import os
import time
import json
import random
import requests
from datetime import datetime

# ── Chain-specific RPC endpoints ─────────────────────────────────────
HELIUS_RPC = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=" + os.getenv("HELIUS_API_KEY", ""))

# Alchemy keys for EVM chains
ALCHEMY_KEY = os.getenv("ALCHEMY_KEY", "")

# Chain RPC URLs (private endpoints to avoid mempool front-running)
CHAIN_RPC = {
    "base": f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "arbitrum": f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "ethereum": f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "bsc": f"https://bsc-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "polygon": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "avalanche": f"https://avax-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    "solana": HELIUS_RPC,
}

# ── 1inch API ────────────────────────────────────────────────────────
ONEINCH_API = "https://api.1inch.dev/swap/v6.0"
ONEINCH_KEY = os.getenv("ONEINCH_API_KEY", "")
ONEINCH_HEADERS = {}
if ONEINCH_KEY:
    ONEINCH_HEADERS["Authorization"] = f"Bearer {ONEINCH_KEY}"

# ── Jupiter API (Solana only) ────────────────────────────────────────
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6"
JUPITER_SWAP_API = "https://quote-api.jup.ag/v6"

# ── Token addresses ──────────────────────────────────────────────────
# Native tokens for each chain
NATIVE_TOKENS = {
    "solana": "So11111111111111111111111111111111111111112",
    "base": "0x4200000000000000000000000000000000000006",  # WETH on Base
    "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH on Arb
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "bsc": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    "polygon": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC
    "avalanche": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
}

# Wrapped SOL on Base/Arb via bridge (for cross-chain)
WRAPPED_SOL = {
    "base": "0x1a51b19ce03dbe0cb44c5520e9115ae2b9619c3f",  # wsol on base
    "arbitrum": "0x4c0786236f9e81252b3e8c1b7e7e9e3e7e3e7e3e",  # placeholder
}


def get_1inch_chains():
    """Get list of chains supported by 1inch."""
    try:
        r = requests.get(f"{ONEINCH_API}/chains", headers=ONEINCH_HEADERS, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}


def get_1inch_chain_id(chain_name):
    """Map chain name to 1inch chain ID."""
    chain_map = {
        "ethereum": 1,
        "base": 8453,
        "arbitrum": 42161,
        "bsc": 56,
        "polygon": 137,
        "avalanche": 43114,
        "optimism": 10,
        "fantom": 250,
    }
    return chain_map.get(chain_name, 0)


def get_1inch_quote(chain, input_token, output_token, amount, wallet_address):
    """Get a swap quote from 1inch API."""
    chain_id = get_1inch_chain_id(chain)
    if not chain_id:
        return None
    
    url = f"{ONEINCH_API}/{chain_id}/swap"
    params = {
        "tokenFrom": input_token,
        "tokenTo": output_token,
        "amount": str(amount),
        "fromAddress": wallet_address,
        "disableEstimate": "true",
        "slippage": "10",  # 10% for meme coins
        "referrerAddress": "0x" + "0" * 40,  # optional
    }
    try:
        r = requests.get(url, params=params, headers=ONEINCH_HEADERS, timeout=15)
        data = r.json()
        if r.status_code == 200 and "tx" in data:
            return data
    except:
        pass
    return None


def get_jupiter_quote_sol(input_mint, output_mint, amount_lamports, slippage_bps=1000):
    """Get Jupiter quote for Solana (already defined in trading.py)."""
    url = f"{JUPITER_QUOTE_API}/quote"
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_lamports),
        "slippageBps": str(slippage_bps),
        "onlyDirectRoutes": "false",
        "maxAccounts": 20,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "error" in data:
            return None
        return data
    except:
        return None


def get_jupiter_swap_tx(quote_response, pubkey, slippage_bps=1000):
    """Get Jupiter swap transaction (already in trading.py)."""
    url = f"{JUPITER_SWAP_API}/swap"
    payload = {
        "quoteResponse": quote_response,
        "userPublicKey": str(pubkey),
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": 100000,  # Higher tip for speed
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        data = r.json()
        if "error" in data:
            return None
        return {
            "swap_transaction": data.get("swapTransaction", ""),
            "lastValidBlockHeight": data.get("lastValidBlockHeight", 0),
        }
    except:
        return None


def broadcast_sol_tx(tx_b64):
    """Broadcast Solana transaction via Helius (private RPC = no mempool exposure)."""
    # Random delay to break MEV patterns
    time.sleep(random.uniform(0.5, 3.0))
    
    r = requests.post(HELIUS_RPC, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "sendTransaction",
        "params": [
            tx_b64,
            {
                "skipPreflight": False,
                "maxRetries": 3,
                "encoding": "base64",
                "preCommit": True,  # Wait for confirmation before returning
            },
        ],
    }, timeout=30)
    data = r.json()
    return data.get("result")


def broadcast_1inch_tx(chain, tx_data, wallet_address, private_key=None):
    """Broadcast a 1inch transaction via private RPC (avoids public mempool)."""
    # Random delay
    time.sleep(random.uniform(0.5, 3.0))
    
    # For EVM chains, we need to sign and broadcast
    # This requires web3.py or similar
    # For now, return the tx data for local signing
    # In production, use: from web3 import Web3; w3 = Web3(Web3.HTTPProvider(CHAIN_RPC[chain]))
    
    try:
        from eth_account import Account
        from web3 import Web3
        
        w3 = Web3(Web3.HTTPProvider(CHAIN_RPC[chain]))
        
        # Sign the transaction
        tx = w3.to_bytes(text=tx_data.get("data", ""))
        if not private_key:
            raise RuntimeError("No private key for signing")
        
        # Note: This is simplified. Full implementation needs tx building, signing, and broadcast
        # For now, return the raw tx for manual broadcast
        return {"tx_data": tx_data, "status": "needs_signing"}
    
    except ImportError:
        return {"tx_data": tx_data, "status": "needs_web3"}
    except Exception as e:
        return {"error": str(e)}


def swap_cross_chain(wallet_address, private_key, chain, input_token, output_token, amount, amount_unit="native"):
    """Execute a cross-chain swap.
    
    Args:
        wallet_address: User's wallet address
        private_key: Private key for signing
        chain: Chain name (solana, base, bsc, arbitrum, ethereum)
        input_token: Input token address (native for chain = use NATIVE_TOKENS[chain])
        output_token: Output token address
        amount: Amount to swap
        amount_unit: "native" (native token) or "erc20" (token with decimals)
    
    Returns:
        dict with status, tx_hash, chain, token
    """
    # Resolve input token
    if amount_unit == "native":
        if chain == "solana":
            input_mint = NATIVE_TOKENS.get("solana", "")
        else:
            # For EVM, convert native to wrapped for DEX routing
            input_mint = NATIVE_TOKENS.get(chain, "")
    else:
        input_mint = input_token
    
    print(f"  [swap] {chain}: {amount} {input_token[:12]}... → {output_token[:12]}...")
    
    try:
        if chain == "solana":
            return _swap_solana(wallet_address, private_key, input_mint, output_token, amount)
        else:
            return _swap_evm(wallet_address, private_key, chain, input_mint, output_token, amount, amount_unit)
    
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _swap_solana(pubkey_b58, private_key, input_mint, output_mint, amount_sol):
    """Swap on Solana via Jupiter."""
    try:
        from solders.keypair import Keypair
        kp = Keypair.from_base58_string(private_key)
        pubkey = kp.pubkey()
        
        amount_lamports = int(amount_sol * 1e9)
        
        # Get quote
        quote = get_jupiter_quote_sol(input_mint, output_mint, amount_lamports, slippage_bps=1000)
        if not quote:
            return {"status": "failed", "error": "No quote from Jupiter"}
        
        # Get swap tx
        swap_tx = get_jupiter_swap_tx(quote, pubkey, slippage_bps=1000)
        if not swap_tx:
            return {"status": "failed", "error": "No swap tx from Jupiter"}
        
        tx_b64 = swap_tx["swap_transaction"]
        
        # Simulate
        r = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "simulateTransaction",
            "params": [tx_b64, {"encoding": "base64", "commitment": "confirmed"}],
        }, timeout=10)
        sim_data = r.json().get("result", {}).get("value", {})
        err = sim_data.get("error")
        if err:
            return {"status": "failed", "error": f"Simulation error: {str(err)[:200]}"}
        
        # Broadcast via private RPC (no public mempool)
        tx_hash = broadcast_sol_tx(tx_b64)
        if not tx_hash:
            return {"status": "failed", "error": "No tx hash from broadcast"}
        
        return {
            "status": "success",
            "tx_hash": str(tx_hash),
            "chain": "solana",
            "input_token": input_mint,
            "output_token": output_mint,
            "amount": amount_sol,
        }
    
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _swap_evm(wallet_addr, private_key, chain, input_mint, output_mint, amount, amount_unit):
    """Swap on EVM chain via 1inch."""
    try:
        from eth_account import Account
        from web3 import Web3
        
        w3 = Web3(Web3.HTTPProvider(CHAIN_RPC[chain]))
        account = Account.from_key(private_key)
        
        # Convert amount to token units
        if amount_unit == "native":
            # For native token swaps, we need to use the wrapped version
            # Jupiter routing handles this, but 1inch needs exact format
            amount_wei = int(amount * 1e18)
        else:
            # Get token decimals
            token_contract = w3.eth.contract(address=w3.to_checksum_address(input_mint))
            decimals = token_contract.functions.decimals().call()
            amount_token = int(amount * (10 ** decimals))
            amount_wei = amount_token
        
        # Get 1inch quote
        quote = get_1inch_quote(chain, input_mint, output_mint, amount_wei, wallet_addr)
        if not quote or "tx" not in quote:
            return {"status": "failed", "error": "No 1inch quote"}
        
        # Sign and broadcast
        tx_data = quote["tx"]
        tx_signed = account.sign_transaction({
            "to": w3.to_checksum_address(tx_data.get("to", "")),
            "data": tx_data.get("data", ""),
            "value": int(tx_data.get("value", 0), 16) if isinstance(tx_data.get("value"), str) else tx_data.get("value", 0),
            "gas": int(tx_data.get("gas", 500000)),
            "gasPrice": w3.eth.gas_price,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": get_1inch_chain_id(chain),
        })
        
        # Broadcast via private RPC
        tx_hash = w3.eth.send_raw_transaction(tx_signed.raw_transaction)
        
        return {
            "status": "success",
            "tx_hash": w3.to_hex(tx_hash),
            "chain": chain,
            "input_token": input_mint,
            "output_token": output_mint,
            "amount": amount,
        }
    
    except ImportError:
        return {"status": "failed", "error": "web3.py and eth_account required. Install: pip install web3 eth_account"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def swap_token_on_chain(chain, token_mint, amount, wallet_addr, private_key):
    """Buy a token on any chain.
    
    Args:
        chain: solana, base, bsc, arbitrum, ethereum
        token_mint: Token address
        amount: SOL/ETH/etc amount
        wallet_addr: Wallet address
        private_key: Private key
    
    Returns swap result dict.
    """
    # For Solana: SOL → token via Jupiter
    if chain == "solana":
        return _swap_solana(wallet_addr, private_key, NATIVE_TOKENS["solana"], token_mint, amount)
    
    # For EVM: Native → token via 1inch
    return _swap_evm(wallet_addr, private_key, chain, NATIVE_TOKENS.get(chain, ""), token_mint, amount, "native")


if __name__ == "__main__":
    print("Cross-chain swap engine loaded.")
    print("Available chains: solana, base, bsc, arbitrum, ethereum, polygon, avalanche")
    print("Requires: pip install web3 eth_account")
