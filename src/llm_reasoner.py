"""
LLM reasoning layer — structured outputs with retry + provider attribution.

Sends trade analysis to the judge model as a JSON-schema-constrained prompt
(with few-shot examples drawn from eval_scenarios.py), parses the JSON reply,
and retries exactly once on parse failure. The answering provider/model is
recorded via loomweaver.core.route when available (same pattern as
judge_eval.py), with graceful fallback to a direct HTTP call.

Fallback policy: on unrecoverable LLM/parse failure return SKIP with low
confidence (capital preservation first) instead of blindly buying.
"""
import os
import json
import requests

# Use Hermes default model — configured in config
DEFAULT_MODEL = os.getenv("HERMES_LLM_MODEL", "qwen3.6-35b")
# Default provider URL — uses Hermes freeinference or whatever is configured
LLM_BASE_URL = os.getenv("HERMES_LLM_URL", "https://freeinference.org")

RESPONSE_SCHEMA = (
    '{"vote": "BUY" or "SKIP", "confidence": 0.0-1.0, "rationale": "<one sentence>"}'
)

from eval_scenarios import pick_few_shot, EXAMPLE_RATIONALES, signal_strength  # noqa: E402


def _example_block(sc):
    """Format one labeled scenario as an example exchange for the prompt."""
    cand = json.dumps(sc["candidate"])
    sigs = json.dumps(sc["signals"])
    answer = json.dumps({
        "vote": sc["label"],
        "confidence": round(min(0.9, 0.5 + signal_strength(sc) / 4), 2),
        "rationale": EXAMPLE_RATIONALES[sc["id"]],
    })
    return f"Example candidate: {cand}\nExample signals: {sigs}\nCorrect response: {answer}"


def build_system_prompt():
    """System prompt: rules + explicit JSON schema + deterministic few-shots."""
    examples = "\n\n".join(_example_block(sc) for sc in pick_few_shot())
    return f"""You are a shitcoin trade analyst on Solana and EVM chains.
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

OUTPUT FORMAT — respond with ONLY valid JSON matching this schema:
{RESPONSE_SCHEMA}

No markdown fences, no extra text before or after the JSON object.

Few-shot examples:

{examples}"""


def build_messages(signal_data, safety_data, strict=False):
    """Build the chat messages list for the judge call.

    strict=True appends the retry instruction ("Return ONLY valid JSON").
    """
    trade_summary = format_trade_summary(signal_data, safety_data)
    content = trade_summary
    if strict:
        content += "\n\nReturn ONLY valid JSON matching the schema. No other text."
    return [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": content},
    ]


def format_trade_summary(signal_data, safety_data):
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

    return (
        f"TOKEN: {symbol} ({mint}) on {chain}\n"
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


def parse_judge_json(text):
    """Extract and parse the first JSON object from model text. None on failure."""
    import re
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


try:
    from loomweaver.core import route as _lw_route  # type: ignore
except ImportError:
    _lw_route = None  # graceful fallback to direct HTTP


def _call_llm(messages, max_tokens=150):
    """Call the judge model. Returns dict(ok, text, provider, model).

    Prefers loomweaver routing (provider attribution); falls back to direct HTTP.
    """
    if _lw_route is not None:
        r = _lw_route(messages, max_tokens=max_tokens)
        return {"ok": bool(r.get("ok")), "text": r.get("text") or "",
                "provider": r.get("provider") or "loomweaver",
                "model": r.get("model") or DEFAULT_MODEL}
    # Graceful fallback to direct HTTP (no provider attribution available)
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": DEFAULT_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.1,
            },
            timeout=15,
        )
        data = resp.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "text": text,
                "provider": "direct", "model": DEFAULT_MODEL}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:120],
                "provider": "direct", "model": DEFAULT_MODEL}


def should_buy(signal_data, safety_data):
    """Ask the judge if this trade is worth executing.

    JSON-schema-constrained prompt; exactly one retry on parse failure;
    falls back to SKIP with low confidence when the LLM is unusable.

    Returns dict with: action ("buy"/"skip"), reason, confidence,
    provider, model, llm_error (optional).
    """
    result = {"action": "skip", "reason": "", "confidence": 0.3}
    parsed = None
    last_err = None

    for attempt in range(2):  # initial + exactly one retry
        r = _call_llm(build_messages(signal_data, safety_data, strict=(attempt > 0)))
        result["provider"] = r.get("provider")
        result["model"] = r.get("model")
        if not r.get("ok"):
            last_err = r.get("error", "llm call failed")
            break
        parsed = parse_judge_json(r.get("text"))
        if parsed is not None:
            break
        last_err = "invalid JSON"

    if parsed is not None:
        vote = str(parsed.get("vote", "")).upper()
        conf = parsed.get("confidence")
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.5
        result["action"] = "buy" if vote == "BUY" else "skip"
        result["reason"] = str(parsed.get("rationale", ""))[:200]
        result["confidence"] = conf
    else:
        # Unrecoverable: SKIP with low confidence — capital preservation first.
        result["action"] = "skip"
        result["confidence"] = 0.2
        result["llm_error"] = str(last_err)[:120]
        result["reason"] = f"Judge unavailable ({str(last_err)[:80]}), defaulting to SKIP"
    return result


if __name__ == "__main__":
    test_signal = {
        "mint": "test", "symbol": "TEST", "chain": "solana",
        "liq": 50000, "price": 0.001, "signal": "BUY_PRESSURE_EARLY",
        "buy_ratio": 0.85, "price_change_1h": 2.0,
        "vol_24h": 500000, "vol_1h": 200000,
    }
    test_safety = {
        "mint_authority": "REVOKED",
        "freeze_authority": "DISABLED",
        "supply": 1000000000,
    }
    print(json.dumps(should_buy(test_signal, test_safety), indent=2))
