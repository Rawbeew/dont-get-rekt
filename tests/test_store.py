"""Tests for store.py — SQLite data layer. Fully in-memory/temp, no network."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from store import Store


def make_store():
    return Store(os.path.join(tempfile.mkdtemp(), "test.db"))


class TestSchemaAndWrites:
    def test_creates_tables(self):
        db = make_store()
        tables = {r["name"] for r in
                  db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"signals", "candidates", "decisions", "trades", "equity_curve"} <= tables

    def test_insert_and_query_signal(self):
        db = make_store()
        sid = db.insert_signal("solana", "TOKEN123", "sfp", "long", 0.82,
                               {"vwap_dist": 0.03})
        rows = db.query("SELECT * FROM signals WHERE id=?", (sid,))
        assert rows[0]["kind"] == "sfp"
        assert rows[0]["strength"] == 0.82

    def test_candidate_unique_constraint(self):
        db = make_store()
        db.insert_candidate("solana", "T1")
        db.insert_candidate("solana", "T1")  # same ts+token → ignored
        assert len(db.query("SELECT * FROM candidates")) == 1

    def test_decision_foreign_key_chain(self):
        db = make_store()
        cid = db.insert_candidate("base", "T2", wallet_score=0.7)
        did = db.insert_decision(cid, "BUY", confidence=0.9,
                                 rationale="strong SFP + volume",
                                 provider="groq", model="openai/gpt-oss-20b",
                                 latency_s=0.4)
        rows = db.query("""
            SELECT d.vote, d.rationale, c.token
            FROM decisions d JOIN candidates c ON c.id = d.candidate_id
            WHERE d.id = ?
        """, (did,))
        assert rows[0]["vote"] == "BUY" and rows[0]["token"] == "T2"


class TestAnalyticQueries:
    def test_win_rate_groups(self):
        db = make_store()
        cid = db.insert_candidate("solana", "T1")
        for vote in ("BUY", "BUY", "SKIP"):
            db.insert_decision(cid, vote, confidence=0.8)
        rows = db.win_rate()
        by_vote = {r["vote"]: r["n"] for r in rows}
        assert by_vote == {"BUY": 2, "SKIP": 1}

    def test_judge_performance(self):
        db = make_store()
        cid = db.insert_candidate("solana", "T1")
        db.insert_decision(cid, "BUY", provider="groq",
                           model="openai/gpt-oss-20b", latency_s=0.4)
        rows = db.judge_performance_by_provider()
        assert rows[0]["provider"] == "groq"
        assert rows[0]["judgments"] == 1

    def test_top_signals_ordering(self):
        db = make_store()
        db.insert_signal("solana", "A", "sfp", "long", 0.5)
        db.insert_signal("solana", "B", "cvd", "short", 0.95)
        top = db.top_signals_24h(limit=1)
        assert top[0]["token"] == "B"

    def test_equity_history(self):
        db = make_store()
        import time as _t
        db.record_equity(10000.0)
        _t.sleep(1.1)  # ts is the PK (second resolution) — later mark wins
        db.record_equity(10150.25, realized_pnl_usd=150.25)
        hist = db.equity_history()
        assert len(hist) == 2
        assert hist[-1]["equity_usd"] == 10150.25
