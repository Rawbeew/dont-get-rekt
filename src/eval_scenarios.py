"""eval_scenarios.py — labeled scenarios used to drive both the judge's prompt
(few-shot examples) and the judge evaluation (pass-rate scoring).

Each scenario is a labeled ground-truth case: what the ideal conservative
judge would vote given the candidate + detected signals.

Single source of truth: llm_reasoner.py draws its few-shot examples from here,
and judge_eval.py scores against these exact labels.
"""

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
    {"id": "sc4_thin_momentum", "label": "SKIP",  # conservative judge avoids thin-liquidity momentum
     "candidate": {"chain": "solana", "token": "MOMO", "price_usd": 0.088,
                   "liquidity": 95000, "fdv": 2200000, "wallet_score": 0.62},
     "signals": [{"kind": "volume_spike", "direction": "long", "strength": 0.77},
                 {"kind": "engulfing", "direction": "long", "strength": 0.71}]},
]

EXAMPLE_RATIONALES = {
    "sc1_rug_liquidity": "Liquidity is $3.1k against a $90k FDV with a single-wallet "
                         "volume pattern — classic rug setup.",
    "sc2_clean_sfp": "Deep liquidity ($480k), high wallet score, SFP reclaim plus volume "
                     "confirmation — structure supports an entry.",
    "sc3_bear_cvd": "Price rising while CVD falls shows distribution; conflicting with "
                    "bearish engulfing, sit out.",
    "sc4_thin_momentum": "Momentum is real but liquidity ($95k) is thin relative to FDV "
                         "and wallet score is middling — capital preservation first.",
}


def signal_strength(sc):
    """Total directional signal strength for a scenario."""
    return sum(s.get("strength", 0.0) for s in sc.get("signals", []))


def _ambiguity(sc):
    """How close long vs short pressure is — lower = more ambiguous."""
    longs = sum(s.get("strength", 0.0) for s in sc["signals"] if s.get("direction") == "long")
    shorts = sum(s.get("strength", 0.0) for s in sc["signals"] if s.get("direction") == "short")
    return abs(longs - shorts)


def pick_few_shot():
    """Deterministic 3-example pick: highest-signal SKIP, cleanest BUY,
    most ambiguous (not already picked). Returns list of scenario dicts."""
    skips = [s for s in SCENARIOS if s["label"] == "SKIP"]
    buys = [s for s in SCENARIOS if s["label"] == "BUY"]
    top_skip = max(skips, key=signal_strength) if skips else None
    top_buy = max(buys, key=signal_strength) if buys else None

    picked = {s["id"]: s for s in (top_skip, top_buy) if s}
    rest = [s for s in SCENARIOS if s["id"] not in picked]
    ambiguous = min(rest, key=_ambiguity) if rest else None
    if ambiguous:
        picked[ambiguous["id"]] = ambiguous

    # Stable order: scenario file order
    return [s for s in SCENARIOS if s["id"] in picked]
