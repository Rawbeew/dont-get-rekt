"""Tests for eval_scenarios.py — scenario data + deterministic few-shot pick."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from eval_scenarios import (SCENARIOS, pick_few_shot, signal_strength,
                            EXAMPLE_RATIONALES, _ambiguity)


def test_four_labeled_scenarios():
    assert len(SCENARIOS) == 4
    assert all(s["label"] in ("BUY", "SKIP") for s in SCENARIOS)
    ids = [s["id"] for s in SCENARIOS]
    assert len(set(ids)) == 4


def test_signal_strength_sums():
    sc2 = next(s for s in SCENARIOS if s["id"] == "sc2_clean_sfp")
    assert abs(signal_strength(sc2) - (0.81 + 0.7)) < 1e-9


def test_ambiguity_prefers_balanced_pressure():
    # sc4 has only longs (high ambiguity score), a balanced case scores lower
    sc4 = next(s for s in SCENARIOS if s["id"] == "sc4_thin_momentum")
    sc1 = next(s for s in SCENARIOS if s["id"] == "sc1_rug_liquidity")
    assert _ambiguity(sc4) > _ambiguity(sc1)


def test_pick_few_shot_deterministic():
    a = pick_few_shot()
    b = pick_few_shot()
    assert [s["id"] for s in a] == [s["id"] for s in b]  # deterministic
    assert len(a) == 3
    labels = {s["id"]: s["label"] for s in a}
    # highest-signal SKIP = sc4 (.77+.71 = 1.48 > sc3 1.4 > sc1 .9)
    assert labels.get("sc4_thin_momentum") == "SKIP"
    # cleanest BUY = sc2 (only BUY)
    assert labels.get("sc2_clean_sfp") == "BUY"
    # third is most ambiguous of the remainder -> sc1 (gap 0.9 < sc3's 1.4)
    assert labels.get("sc1_rug_liquidity") == "SKIP"


def test_every_scenario_has_example_rationale():
    for s in SCENARIOS:
        assert len(EXAMPLE_RATIONALES[s["id"]]) > 20


def test_judge_eval_shares_same_scenarios():
    import judge_eval
    assert judge_eval.SCENARIOS is SCENARIOS
