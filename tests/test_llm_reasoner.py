"""Tests for llm_reasoner.py — all network mocked, no live LLM calls."""
import json
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

import llm_reasoner
from llm_reasoner import (should_buy, parse_judge_json, build_messages,
                          build_system_prompt, RESPONSE_SCHEMA)

GOOD_JSON = json.dumps({"vote": "BUY", "confidence": 0.8,
                        "rationale": "Strong buy pressure with deep liquidity."})
BAD_JSON = "I think this is a great opportunity! BUY BUY BUY"


def _route_result(text, ok=True):
    return {"ok": ok, "text": text, "provider": "test-provider",
            "model": "test-model"}


SIGNAL = {"mint": "test", "symbol": "TEST", "chain": "solana",
          "liq": 50000, "price": 0.001, "signal": "BUY_PRESSURE_EARLY",
          "buy_ratio": 0.85, "price_change_1h": 2.0,
          "vol_24h": 500000, "vol_1h": 200000}
SAFETY = {"mint_authority": "REVOKED", "freeze_authority": "DISABLED",
          "supply": 1000000000}


def test_parse_valid_json():
    parsed = parse_judge_json(f'Sure! {GOOD_JSON} hope that helps')
    assert parsed is not None
    assert parsed["vote"] == "BUY"
    assert parsed["confidence"] == 0.8


def test_parse_invalid_returns_none():
    assert parse_judge_json(BAD_JSON) is None
    assert parse_judge_json("") is None
    assert parse_judge_json('{"vote": [1,2],') is None


def test_should_buy_valid_json_no_retry():
    with mock.patch.object(llm_reasoner, "_call_llm",
                           return_value=_route_result(GOOD_JSON)) as m:
        r = should_buy(SIGNAL, SAFETY)
    assert m.call_count == 1  # valid JSON -> no retry
    assert r["action"] == "buy"
    assert r["provider"] == "test-provider"
    assert r["model"] == "test-model"
    assert r["confidence"] == 0.8
    assert "llm_error" not in r


def test_invalid_json_exactly_one_retry_then_skip_low_confidence():
    with mock.patch.object(llm_reasoner, "_call_llm",
                           return_value=_route_result(BAD_JSON)) as m:
        r = should_buy(SIGNAL, SAFETY)
    assert m.call_count == 2  # exactly one retry
    assert r["action"] == "skip"
    assert r["confidence"] < 0.3
    assert r.get("llm_error") == "invalid JSON"
    # retry message carries the strict instruction
    second_messages = m.call_args_list[1][0][0]
    assert "Return ONLY valid JSON" in second_messages[-1]["content"]
    first_messages = m.call_args_list[0][0][0]
    assert "Return ONLY valid JSON" not in first_messages[-1]["content"]


def test_llm_failure_skips_without_retry():
    with mock.patch.object(llm_reasoner, "_call_llm",
                           return_value=_route_result("", ok=False)) as m:
        r = should_buy(SIGNAL, SAFETY)
    assert m.call_count == 1  # transport failure: no point retrying parse
    assert r["action"] == "skip"
    assert r["confidence"] < 0.3
    assert "llm_error" in r


def test_prompt_contains_schema_and_few_shots():
    sys_prompt = build_system_prompt()
    assert RESPONSE_SCHEMA in sys_prompt
    assert sys_prompt.count("Correct response:") == 3  # 3 few-shot examples
    # all three deterministic picks present by token name
    from eval_scenarios import pick_few_shot
    for sc in pick_few_shot():
        assert sc["candidate"]["token"] in sys_prompt
        assert f'"vote": "{sc["label"]}"' in sys_prompt


def test_strict_mode_appends_retry_instruction():
    msgs = build_messages(SIGNAL, SAFETY, strict=True)
    assert "Return ONLY valid JSON" in msgs[-1]["content"]
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


def test_fallback_provider_attribution_fields():
    """Direct-HTTP fallback path still records provider/model."""
    fake_resp = mock.Mock()
    fake_resp.json.return_value = {"choices": [{"message": {"content": GOOD_JSON}}]}
    with mock.patch.object(llm_reasoner, "_lw_route", None), \
         mock.patch.object(llm_reasoner, "requests") as req:
        req.post.return_value = fake_resp
        r = should_buy(SIGNAL, SAFETY)
    assert r["action"] == "buy"
    assert r["provider"] == "direct"
    assert r["model"] == llm_reasoner.DEFAULT_MODEL


def test_loomweaver_route_provider_attribution():
    """loomweaver routing path records the answering provider/model."""
    with mock.patch.object(
            llm_reasoner, "_lw_route",
            return_value={"ok": True, "text": GOOD_JSON,
                          "provider": "openrouter", "model": "qwen-test"}) as m:
        r = should_buy(SIGNAL, SAFETY)
    assert m.call_count == 1
    assert r["action"] == "buy"
    assert r["provider"] == "openrouter"
    assert r["model"] == "qwen-test"
