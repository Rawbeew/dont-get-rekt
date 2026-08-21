# dont-get-rekt

Paper-mode crypto signal engine. Multi-chain ingestion → free-tier LLM casts structured BUY/SKIP votes.

## What it does
- Ingests DEX (Dexscreener) + CEX (ccxt) data across 90+ chains
- Computes signals: VWAP, SFP, CVD, engulfing, volume spikes
- Free-tier LLM (freeinference/Groq/NVIDIA/Cloudflare) casts structured BUY/SKIP votes
- Paper-mode only: simulates trades, tracks P&L, no real money
- Adversarial-tested: 44 tests with real payloads

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

## License
MIT
