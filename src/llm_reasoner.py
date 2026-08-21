"""
LLM reasoning layer — uses Hermes default provider (no extra API key needed).
Sends trade analysis to the default model and returns BUY/SKIP recommendation.

This replaces the OpenRouter requirement — uses whatever model is already
configured for Hermes.

Fallback: if LLM is unavailable, proceed with buy (don't miss opportunities).

Configuration:
- Uses Hermes default provider via openrouter.ai (qwen3.6-35b or whatever is default)
- No extra API key needed — uses the same model as the main agent
"""
import os
import json
import requests
from datetime import datetime, timezone

# Use Hermes default model — configured in config
DEFAULT_MODEL = os.getenv("HERMES_LLM_MODEL", "qwen3.6-35b")
# Default provider URL — uses Hermes freeinference or whatever is configured
LLM_BASE_URL = os.getenv("HERMES_LLM_URL", "https://freeinference.org")

SYSTEM_PROMPT = """You are a shitcoin trade analyst on Solana and EVM chains.
You review potential buys and vote BUY or SKIP. Consider:
- Mint authority MUST be revoked (ACTIVE = rug risk, SKIP)
- Freeze authority MUST be disabled (ENABLED = rug risk, SKIP)
- Liquidity: $12.5k-$200k is ideal. <12.5k = too risky, >200k = no upside
- Buy ratio >60% with price flat or slightly up = accumulation, BUY
- Buy ratio <50% with price down = dumping, SKIP
- Volume surge >1.5x with price <25% change = smart money loading, BUY
- Token last bought 30+ mins ago (no recent activity) = dead, SKIP
- Supply reasonable (<1 trillion)
- If you are unsure, SKIP. Capital preservation first.

Rules:
- Keep reasoning under 2 sentences
- MUST start with BUY: or SKIP:
- If mint auth is ACTIVE → SKIP: (rug risk)
- If freeze auth is ENABLED → SKIP: (rug risk)
- If buy ratio >60% AND price change <15% → BUY:
- If volume surge >2x AND price <25% → BUY:
- If buy ratio <50% AND price down → SKIP:
- Default to SKIP if uncertain"""


def should_buy(signal_data, safety_data):
    """Ask LLM if this trade is worth executing.
    
    Returns dict with:
      - action: "buy" or "skip"
      - reason: why
      - llm_error: if LLM failed
    """
    mint = signal_data.get("mint", signal_data.get("address", "unknown"))
    symbol = signal_data.get("symbol", "unknown")
    chain = signal_data.get("chain", "unknown")
    liq = signal_data.get("liq", signal_data.get("liquidity_usd", 0))
    price = signal_data.get("price", 0)
    signal = signal_data.get("signal", "unknown")
    
    buy_ratio = signal_data.get("buy_ratio", 0)
    ch_1h = signal_data.get("price_change_1h", 0)
    vol_24h = signal_data.get("vol_24h", 0)
    vol_1h = signal_data.get("vol_1h", 0)
    
    mint_auth = safety_data.get("mint_authority", "unknown")
    freeze_auth = safety_data.get("freeze_authority", "unknown")
    supply = safety_data.get("supply", 0)
    
    trade_summary = (
        f"TOKEN: {symbol} on {chain}\n"
        f"Signal: {signal}\n"
        f"Price: ${float(price):.10f}\n"
        f"Liquidity: ${float(liq):,.0f}\n"
        f"24h Volume: ${float(vol_24h):,.0f}\n"
        f"1h Volume: ${float(vol_1h):,.0f}\n"
        f"Buy Ratio: {buy_ratio:.0%}\n"
        f"1h Change: {ch_1h:+.1f}%\n"
        f"Mint Authority: {mint_auth}\n"
        f"Freeze Authority: {freeze_auth}\n"
        f"Supply: {int(supply):,}"
    )
    
    try:
        r = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": DEFAULT_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": trade_summary},
                ],
                "max_tokens": 100,
                "temperature": 0.1,
            },
            timeout=15,
        )
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().upper()
        
        if content.startswith("SKIP:") or "SKIP" in content[:10]:
            return {"action": "skip", "reason": data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()[:200]}
        elif content.startswith("BUY:") or "BUY" in content[:10]:
            return {"action": "buy", "reason": data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()[:200]}
        else:
            return {"action": "buy", "reason": f"LLM unclear: {content[:100]}"}
    
    except Exception as e:
        return {"action": "buy", "reason": f"LLM failed ({str(e)[:80]}), proceeding on safety checks"}


if __name__ == "__main__":
    test_signal = {
        "mint": "test", "symbol": "TEST", "chain": "solana",
        "liq": 50000, "price": 0.001, "signal": "BUY_PRESSURE_EARLY",
        "buy_ratio": 0.85, "price_change_1h": 2.0,
        "vol_24h": 500000, "vol_1h": 200000,
    }
    test_safety = {
        "mint_authority": "REVOLED",
        "freeze_authority": "DISABLED",
        "supply": 1000000000,
    }
    result = should_buy(test_signal, test_safety)
    print(json.dumps(result, indent=2))
