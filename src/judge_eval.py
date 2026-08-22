"""judge_eval.py — score the LLM BUY/SKIP judge against labeled scenarios.

Scenarios are data-driven from eval_scenarios.py (same source that feeds the
judge's few-shot examples). Uses the same free-tier routing as production
(loomweaver providers) so the eval measures the exact judge the engine runs.

A judge passes a scenario when:
  - it returns valid JSON with vote BUY|SKIP
  - the vote matches the labeled ground truth
  - rationale is present and non-trivial (>20 chars)

Run:  cd src && python judge_eval.py
      (or via run_cycle.py --judge-eval before each cycle)
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# loomweaver is vendored under src/loomweaver — import directly, no path hacks

from store import Store  # noqa: E402
from eval_scenarios import SCENARIOS  # noqa: E402  # single source of truth

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


def _score_one(r, sc):
    """Score a route() result against a scenario. Returns (parsed, passed)."""
    parsed = None
    try:
        m = re.search(r"\{.*\}", r.get("text") or "", re.S)
        parsed = json.loads(m.group(0))
        vote_ok = parsed.get("vote") == sc["label"]
        rat_ok = len(parsed.get("rationale") or "") > 20
        return parsed, bool(r.get("ok") and vote_ok and rat_ok)
    except Exception:
        return None, False


def run(creds_path=None, use_db=True):
    from loomweaver.core import load_creds, route
    creds = load_creds(creds_path) if creds_path else None
    db = (Store(os.path.join(os.path.dirname(__file__), "state", "dgr.db"))
          if use_db else None)
    results = []
    for sc in SCENARIOS:
        cid = (db.insert_candidate(
            chain=sc["candidate"]["chain"], token=sc["candidate"]["token"],
            price_usd=sc["candidate"].get("price_usd"),
            liquidity=sc["candidate"].get("liquidity"),
            fdv=sc["candidate"].get("fdv"),
            wallet_score=sc["candidate"].get("wallet_score")) if db else None)
        t0 = time.time()
        r = route(build_messages(sc), max_tokens=2000, creds=creds)
        lat = time.time() - t0

        parsed, passed = _score_one(r, sc)

        if db:
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
