"""store.py — SQLite persistence layer for signals, decisions, and paper P&L.

The explicit data pipeline: ingestion → normalize → STORE → retrieve → LLM judge.

Why SQLite: zero-dep (stdlib), single file, perfect for one-writer workloads,
and queryable with plain SQL — the same schema ports to Postgres unchanged.

Schema:
    signals      — every raw signal the detector fires (one row per event)
    candidates   — tokens that passed wallet gating (LLM judge input)
    decisions    — LLM BUY/SKIP votes with rationale + model attribution
    trades       — paper fills derived from BUY decisions
    equity_curve — mark-to-market portfolio value over time

Usage:
    from store import Store
    db = Store("state/signals.db")
    db.insert_signal(...)
    rows = db.query("SELECT * FROM decisions ORDER BY ts DESC LIMIT 10")
"""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    chain       TEXT NOT NULL,
    token       TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- vwap | sfp | cvd | engulfing | volume_spike
    direction   TEXT NOT NULL,          -- long | short
    strength    REAL NOT NULL,          -- 0..1 normalized
    meta        TEXT                    -- JSON blob of raw indicator values
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_token ON signals(token);

CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    chain       TEXT NOT NULL,
    token       TEXT NOT NULL,
    price_usd   REAL,
    liquidity   REAL,
    fdv         REAL,
    wallet_score REAL,                   -- gate score that admitted it
    UNIQUE(ts, token)
);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    vote        TEXT NOT NULL CHECK(vote IN ('BUY','SKIP')),
    confidence  REAL,                   -- 0..1
    rationale   TEXT,                   -- LLM's stated reasoning
    provider    TEXT,                   -- which free tier judged it
    model       TEXT,
    latency_s   REAL
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);

CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id  INTEGER NOT NULL REFERENCES decisions(id),
    opened_ts    TEXT NOT NULL,
    entry_price  REAL NOT NULL,
    size_usd     REAL NOT NULL,
    closed_ts    TEXT,                  -- NULL = open position
    exit_price   REAL,
    pnl_usd      REAL
);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(closed_ts);

CREATE TABLE IF NOT EXISTS equity_curve (
    ts          TEXT PRIMARY KEY,
    equity_usd  REAL NOT NULL,
    open_positions INTEGER,
    realized_pnl_usd REAL
);
"""


class Store:
    def __init__(self, path="state/dgr.db"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- writes -------------------------------------------------------
    def insert_signal(self, chain, token, kind, direction, strength, meta=None):
        cur = self.conn.execute(
            "INSERT INTO signals (ts, chain, token, kind, direction, strength, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), chain, token,
             kind, direction, strength, json.dumps(meta or {})))
        self.conn.commit()
        return cur.lastrowid

    def insert_candidate(self, chain, token, price_usd=None, liquidity=None,
                         fdv=None, wallet_score=None):
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO candidates (ts, chain, token, price_usd, "
            "liquidity, fdv, wallet_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), chain, token,
             price_usd, liquidity, fdv, wallet_score))
        self.conn.commit()
        return cur.lastrowid

    def insert_decision(self, candidate_id, vote, confidence=None, rationale=None,
                        provider=None, model=None, latency_s=None):
        cur = self.conn.execute(
            "INSERT INTO decisions (ts, candidate_id, vote, confidence, rationale, "
            "provider, model, latency_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), candidate_id,
             vote, confidence, rationale, provider, model, latency_s))
        self.conn.commit()
        return cur.lastrowid

    def record_equity(self, equity_usd, open_positions=0, realized_pnl_usd=0.0):
        self.conn.execute(
            "INSERT OR REPLACE INTO equity_curve (ts, equity_usd, open_positions, "
            "realized_pnl_usd) VALUES (?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), equity_usd,
             open_positions, realized_pnl_usd))
        self.conn.commit()

    # ---- reads (the SQL recruiters want to see) ------------------------
    def query(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params)]

    def win_rate(self):
        """BUY decisions that would have been profitable — placeholder until
        trade closes are wired; structure shown for the real query."""
        return self.query("""
            SELECT vote, COUNT(*) as n, AVG(confidence) as avg_conf
            FROM decisions GROUP BY vote ORDER BY n DESC
        """)

    def judge_performance_by_provider(self):
        """Which free-tier provider is doing the judging, and how fast."""
        return self.query("""
            SELECT provider, model,
                   COUNT(*)              AS judgments,
                   AVG(latency_s)        AS avg_latency_s,
                   SUM(vote = 'BUY')     AS buy_votes
            FROM decisions
            GROUP BY provider, model
            ORDER BY judgments DESC
        """)

    def top_signals_24h(self, limit=20):
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(time.time() - 86400))
        return self.query(
            "SELECT chain, token, kind, direction, strength, ts FROM signals "
            "WHERE ts >= ? ORDER BY strength DESC LIMIT ?", (cutoff, limit))

    def equity_history(self):
        return self.query(
            "SELECT ts, equity_usd, realized_pnl_usd FROM equity_curve ORDER BY ts")
