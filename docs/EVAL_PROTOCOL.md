# Eval Protocol — 30-Day Paper Run (started 2026-08-21)

This document is a **pre-registration**. Labels and success criteria are
committed BEFORE outcomes are known. Changing them mid-run invalidates the run.

## Rules (frozen)

1. **Cadence:** scans fire 4×/day (00:30, 06:30, 12:30, 18:30 UTC). No manual
   cherry-picking of cycles.
2. **Decisions:** every LLM BUY/SKIP vote is persisted to SQLite with provider,
   model, latency, confidence. No post-hoc deletion.
3. **Ground truth:** a BUY is "correct" if the token's price at +24h exceeds
   entry by >2% (covers fees + slippage on a $50 paper position). SKIP is
   correct if price at +24h is ≤ entry +2%. Price source: Dexscreener API,
   fetched once at T+24h by `labeler.py`. No re-fetching.
4. **Success criteria (all must hold):**
   - ≥200 decisions logged
   - Win rate ≥55% across all decisions
   - Win rate ≥55% on BUY votes specifically (precision matters more than recall)
   - Judge eval score ≥80% on the frozen scenario set (`judge_eval.py`)
5. **Live trading gate:** only unlocked if ALL four hold AND drawdown never
   exceeded 20% of starting balance.

## What is explicitly NOT allowed

- Tuning prompts mid-run based on interim results (note observations in
  `observations.md` instead — apply after the run)
- Disabling workflows to skip bad days
- Adding scenarios after seeing their outcome

## Daily labeler

`labeler.py` runs daily (cron), fetches T+24h prices for positions opened the
previous day, writes labels to the `labels` table. Missed labeling = those
decisions are excluded from scoring (not guessed).

## Reporting

At day 30: `report.py` emits win-rate tables, per-provider judge quality,
equity curve CSV, and an honest pass/fail against every criterion above.
