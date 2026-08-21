"""judge_eval.py — score the LLM BUY/SKIP judge against labeled scenarios.

Uses the same free-tier routing as production (flippy providers) so the eval
measures the exact judge the engine runs.

A judge passes a scenario when:
  - it returns valid JSON with vote BUY|SKIP
  - the vote matches the labeled ground truth
  - rationale is present and non-trivial (>20 chars)

Run:  cd src && python judge_eval.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# loomweaver lives in flippy; allow FLIPPY_SRC env or default sibling checkout
_FLIPPY_SRC = os.environ.get("FLIPPY_SRC",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "..", "flippy", "src"))
if os.path.isdir(_FLIPPY_SRC):
    sys.path.insert(0, os.path.realpath(_FLIPPY_SRC))

from store import Store  # noqa: E402

SCENARIOS = [
    {"id": "sc1_rug_liquidity", "label": "SKIP",
     "candidate": {"chain": "solana", "token": "RUGCOIN", "price_usd": 0.000042,
                   "liquidity": 3100, "fdv": 90000, "wallet_score": 0.15},
     "signals": [{"kind": "volume_spike", "direction": "long", "strength": 0.9,
                  "meta": {"vol_x": 6.2, "note": "single-wallet wash pattern"}}]},
    {"id": "sc2_clean_sfp", "label": "BUY",
     "candidate": {"chain": "solana", "token": "SOLID", "price_usd": 0.31,
                   "liquidity": 480000, "fdv": 12000000, "wallet_score": 0.78},
     "signals": [{"kind": "sfp", "direction": "long", "strength": 0.81,
                  "meta": {"swept_low": 0.295, "reclaim": 0.308}},
                 {"kind": "volume_spike", "direction": "long", "strength": 0.7}]},
    {"id": "sc3_bear_cvd", "label": "SKIP",
     "candidate": {"chain": "base", "token": "HYPEUP", "price_usd": 1.02,
                   "liquidity": 220000, "fdv": 5000000, "wallet_score": 0.55},
     "signals": [{"kind": "cvd", "direction": "short", "strength": 0.74,
                  "meta": {"divergence": "price up, CVD down"}},
                 {"kind": "engulfing", "direction": "short", "strength": 0.66}]},
    {"id": "sc4_thin_momentum", "label": "SKIP",  # conservative judge correctly avoids thin-liquidity momentum
     "candidate": {"chain": "solana", "token": "MOMO", "price_usd": 0.088,
                   "liquidity": 95000, "fdv": 2200000, "wallet_score": 0.62},
     "signals": [{"kind": "volume_spike", "direction": "long", "strength": 0.77},
                 {"kind": "engulfing", "direction": "long", "strength": 0.71}]},
]

PROMPT = """You are a crypto signal judge. Given a candidate token and detected
signals, respond ONLY with JSON:
{{"vote": "BUY"|"SKIP", "confidence": 0.0-1.0, "rationale": "<one sentence>"}}

Lean SKIP when liquidity is thin relative to FDV, wallet score is low, or
signals conflict with on-chain structure. Paper-mode only; be conservative.

Candidate: {candidate}
Signals: {signals}"""


def build_messages(scenario):
    return [{"role": "user", "content": PROMPT.format(
        candidate=json.dumps(scenario["candidate"]),
        signals=json.dumps(scenario["signals"]))}]


def run(creds_path=None):
    from loomweaver.core import load_creds, route
    creds = load_creds(creds_path) if creds_path else None
    db = Store(os.path.join(os.path.dirname(__file__), "state", "dgr.db"))
    results = []
    for sc in SCENARIOS:
        cid = db.insert_candidate(
            chain=sc["candidate"]["chain"], token=sc["candidate"]["token"],
            price_usd=sc["candidate"].get("price_usd"),
            liquidity=sc["candidate"].get("liquidity"),
            fdv=sc["candidate"].get("fdv"),
            wallet_score=sc["candidate"].get("wallet_score"))
        t0 = time.time()
        r = route(build_messages(sc), max_tokens=2000, creds=creds)
        lat = time.time() - t0

        passed = False
        parsed = None
        try:
            m = __import__("re").search(r"\{.*\}", r.get("text") or "", __import__("re").S)
            parsed = json.loads(m.group(0))
            vote_ok = parsed.get("vote") == sc["label"]
            rat_ok = len(parsed.get("rationale") or "") > 20
            passed = r.get("ok") and vote_ok and rat_ok
        except Exception:
            pass

        db.insert_decision(cid, (parsed or {}).get("vote", "SKIP"),
                           confidence=parsed.get("confidence"),
                           rationale=parsed.get("rationale"),
                           provider=r.get("provider"), model=r.get("model"),
                           latency_s=round(lat, 2))
        results.append({"scenario": sc["id"], "expected": sc["label"],
                        "got": (parsed or {}).get("vote"), "pass": passed,
                        "latency_s": round(lat, 2), "provider": r.get("provider")})
        print(f"{'PASS' if passed else 'FAIL'} {sc['id']}: "
              f"expected {sc['label']}, got {(parsed or {}).get('vote')} ({lat:.1f}s)")
    score = sum(1 for x in results if x["pass"]) / len(results) * 100
    print(f"\nJudge score: {score:.0f}% ({sum(1 for x in results if x['pass'])}/{len(results)})")
    return results


if __name__ == "__main__":
    run()
