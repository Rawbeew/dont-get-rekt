# dont-get-rekt

Paper-mode crypto signal engine. Multi-chain ingestion → free-tier LLM casts structured BUY/SKIP votes.

## What it does
- Ingests DEX (Dexscreener) + CEX (ccxt) data across 90+ chains
- Computes signals: VWAP, SFP, CVD, engulfing, volume spikes
- Free-tier LLM (freeinference/Groq/NVIDIA/Cloudflare) casts structured BUY/SKIP votes
- Paper-mode only: simulates trades, tracks P&L, no real money
- Unit-tested: store layer, eval scenarios, LLM reasoner parsing

## Architecture
```
DEX/CEX Feeds → Normalizer → Signal Engine → LLM Judge → Paper Engine
```

## Data pipeline

```
ingest (Dexscreener + ccxt) → normalize → SQLite store → SQL queries → LLM judge → paper trades
```

Every cycle persists to `src/state/dgr.db` (SQLite, stdlib-only):

| Table | What it holds |
|---|---|
| `signals` | every detector event (kind, direction, strength, raw indicator JSON) |
| `candidates` | tokens that passed wallet gating — the LLM judge's input |
| `decisions` | BUY/SKIP votes with rationale, provider, model, latency |
| `trades` | paper fills derived from BUY votes |
| `equity_curve` | mark-to-market portfolio snapshots |

Example query — judge performance by free-tier provider:

```sql
SELECT provider, model, COUNT(*) AS judgments,
       AVG(latency_s) AS avg_latency_s, SUM(vote = 'BUY') AS buy_votes
FROM decisions GROUP BY provider, model ORDER BY judgments DESC;
```

Same schema ports unchanged to Postgres.

## Quickstart
```bash
export FREEINFERENCE_KEY=...
export GROQ_KEY=...
export NVIDIA_KEY=...
python src/aihub.py --chat "Analyze SOL/USDC"
```

## Architecture

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                     INGESTION (cron)                     │
                        │                                                          │
   Dexscreener API ────▶│  fetch tokens across 90+ chains (top gainers / new pairs)│
   CEX feeds (ccxt) ───▶│  OHLCV + trades + liquidity snapshots                    │
                        └───────────────┬──────────────────────────────────────────┘
                                        ▼
                        ┌───────────────────────────┐
                        │        NORMALIZER         │  unify schemas, dedupe pairs,
                        │  (chain-agnostic frames)  │  compute derived fields
                        └───────────────┬───────────┘
                                        ▼
                        ┌───────────────────────────┐      every event persisted
                        │       SIGNAL ENGINE       │      to SQLite (signals table)
                        │  VWAP · SFP · CVD ·       │◀──── state/dgr.db
                        │  engulfing · vol spikes   │
                        └───────────────┬───────────┘
                                        ▼
                        ┌───────────────────────────┐
                        │      WALLET GATING        │  hard filters: liquidity floor,
                        │   (rule-based, no LLM)    │  age, volume — kills noise early
                        └───────────────┬───────────┘
                                        ▼
                        ┌───────────────────────────┐
                        │        LLM JUDGE          │  free-tier router
                        │  structured BUY:/ SKIP:   │  (freeinference/Groq/NVIDIA/CF)
                        │  vote + cited rationale   │  every gate it cites was
                        └───────────────┬───────────┘  pre-computed upstream
                                        ▼
                        ┌───────────────────────────┐
                        │       PAPER ENGINE        │  simulated fills, P&L,
                        │  equity curve, override   │  engine-side veto over the LLM
                        └───────────────────────────┘
```

The model is the judge, not the source of truth — every gate it cites is a value the pipeline already computed.

## Trading status

The auto-trade engine (`auto_trade.py`) is built and gated behind `LIVE_MODE=1`.
It has **never executed a real trade**. A 30-day paper evaluation is running
(docs/EVAL_PROTOCOL.md) — results will determine whether this gets enabled.
Do not enable it based on this README alone.

## Known limitations

- The LLM judge has been evaluated against 4 hand-labeled scenarios (not out-of-sample)
- Paper trades are simulated at signal price; real fills would include slippage
- Free-tier LLM judges have latency variance (2-20s per decision)
- The 30-day evaluation is running now; no win-rate data exists yet
- No backtest has been run against historical data (planned)

## Development approach