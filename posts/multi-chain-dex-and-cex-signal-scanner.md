---
title: "Multi-chain DEX and CEX signal scanner: normalising 90 chains for one LLM"
date: 2026-08-18
tags: [crypto, trading-bot, dex, cex, on-chain, multi-chain, solana, evm, data-pipeline]
canonical: https://github.com/Rawbeew/dont-get-rekt
---

# Multi-chain DEX and CEX signal scanner: normalising 90 chains for one LLM

The hardest part of building an LLM-as-judge crypto scanner isn't the
prompt. It's the **data normalisation**: turning the wildly different
shapes of 90+ chain DEX feeds into one signal stream that the LLM can
reason about without making any API calls itself.

This post is about that pipeline — the pattern that lets you add a new
chain by writing one adapter, not a new model.

## Why you do not want the LLM calling APIs

It is tempting. The LLM can write HTTP requests. It can call
`/coins/{id}` on a chain explorer. It can fetch a chart. Why build a
pipeline when the model can just go get what it needs?

Three reasons, in order of how much they cost:

1. **Latency.** A LLM that fetches 6 charts and 4 on-chain reads takes
   30+ seconds. A pipeline that pre-computes the same numbers takes
   200ms. Trading decisions live or die on latency.
2. **Reliability.** A LLM that calls APIs can fail because the API is
   down, because the response shape changed, because the model's tool
   call was malformed. A pipeline that calls APIs in code retries with
   backoff, validates the response, and falls back to cached data.
3. **Audit.** A LLM that calls APIs has an implicit reasoning chain
   ("I called X and got Y, so I conclude Z") that's hard to replay. A
   pipeline writes the normalised data to disk; the LLM's prompt is
   deterministic from the file.

The rule: **the LLM sees a fixed, complete, normalised signal summary.
It never calls anything itself.** The model is the *judge*, not the
*investigator*.

## The two sources you actually need

The pipeline mixes two signal sources. Each one answers a different
question.

### CEX OHLCV (ccxt)
**What it answers:** "How is the major-pair market behaving right now?"

For BTC, ETH, SOL and a few other majors, you want clean OHLCV data,
5-minute bars, last 200 periods. The standard library is `ccxt`, which
exchanges a uniform `fetch_ohlcv()` for Binance, Coinbase, Kraken, etc.

```python
import ccxt
exchange = ccxt.binance()
bars = exchange.fetch_ohlcv("BTC/USDT", timeframe="5m", limit=200)
# -> [[timestamp, open, high, low, close, volume], ...]
```

The signal computation (VWAP, SFP, engulfing, CVD divergence, volume
spike) runs on these bars. The result is a per-major "market regime"
signal that goes into the normalised summary.

### DEX feeds (Dexscreener API)
**What it answers:** "What's new on the DEX side that isn't on the CEX
side?"

Dexscreener exposes a clean REST API covering 90+ chains — Solana,
Base, Ethereum, BSC, Arbitrum, and dozens of long-tail chains. It gives
you pair-level data (volume, liquidity, age, holders, top-10
concentration) without needing per-chain RPC.

```python
import urllib.request, json
def dexscreener_search(q):
    req = urllib.request.Request(
        f"https://api.dexscreener.com/latest/dex/search?q={q}",
        headers={"User-Agent": "Mozilla/5.0 ... Chrome/126.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())
```

The token-level signals (mint authority, freeze authority, holder
concentration, buy ratio) come from here.

## The normalised signal summary

Every candidate token becomes a single JSON object with this shape:

```json
{
  "chain": "solana",
  "token_age_hours": 11,
  "holders": 1847,
  "top_10_pct": 31.2,
  "mint_authority": null,
  "freeze_authority": null,
  "liquidity_usd": 48200,
  "vol_24h_usd": 184300,
  "vol_vs_7d_avg": 4.3,
  "buys_24h": 1213,
  "sells_24h": 814,
  "buy_ratio": 0.598,
  "price_change_24h": 0.07,
  "vwap_position": 0.0042,
  "sfp_24h": false,
  "engulfing_24h": false,
  "cvd_divergence": false,
  "volume_spike_2x": true,
  "last_buy_minutes_ago": 3,
  "trades_last_30min": 127
}
```

Every field is a number or a boolean. There are no nested objects, no
free-text strings, no missing keys. The LLM prompt is a JSON dump of
this object plus a one-line header.

The shape matters because:

- **The LLM has a fixed input.** It cannot request more data. It has to
  work with what it sees.
- **The pipeline is auditable.** Any field is traceable to its source
  adapter and its timestamp.
- **The state is replayable.** Given the JSON, the LLM prompt is
  deterministic. Re-running the LLM with the same input gives the same
  output (modulo non-deterministic model sampling).

## The five signals

The current pipeline computes these on top of the normalised data:

### 1. VWAP position
Volume-weighted average price over the last N bars. A token trading
above VWAP is in a short-term uptrend; below is a downtrend. Computed on
CEX OHLCV for majors, estimated from trade tape for DEX pairs.

### 2. Daily SFP (Swing Failure Pattern)
A "sweep + reclaim" pattern: price sweeps below a recent low, then
closes back above it within the same bar. Bearish in a bull trend,
bullish in a bear trend.

### 3. Engulfing bars
A bar whose body fully engulfs the prior bar's body. Bullish engulfing
in a downtrend, bearish engulfing in an uptrend.

### 4. CVD divergence
Cumulative Volume Delta — net buy volume minus sell volume over a
window. Price up + CVD down (or inverse) is a divergence: a meaningful
signal that the price move is not supported by flow.

### 5. Volume spike
`vol_24h / vol_7d_avg >= 2.0`. A 2x or more volume surge against the
rolling 7-day average. With price change < 25%, this is a classic
"smart money loading" pattern.

Each of these is a single boolean or number in the normalised
summary. The LLM sees all five; it cites whichever one flipped its vote.

## Adding a new chain

Adding a new chain is a one-file change. Create an adapter that maps the
chain's data source to the normalised signal shape:

```python
# feeds/arbitrum.py
def to_signal(pair_data, ohlcv):
    return {
        "chain": "arbitrum",
        "token_age_hours": pair_data["age_hours"],
        "holders": pair_data["holders"],
        "top_10_pct": pair_data["top_10_pct"],
        "liquidity_usd": pair_data["liquidity_usd"],
        # ...etc, mapping chain-specific to normalised fields
    }
```

The signal computation runs on the normalised output. The LLM prompt
is the same regardless of which chain the token came from. **The model
never knows — and never needs to know — whether a candidate came from
Solana, Base, or Ethereum.**

## What the LLM never sees

Three categories of data are deliberately absent from the normalised
summary:

1. **Token name and ticker.** The model doesn't need them; the prompt
   doesn't include them. Removing the name removes a class of "name
   effect" bias where the model prefers tokens with familiar names.
2. **Social signal.** Twitter followers, Discord members, Reddit
   mentions. Adding social signal would let the LLM be swayed by
   hype. The pipeline is purely on-chain.
3. **Historical price charts.** The model gets the 24h summary, not a
   full chart. Adding a chart would let it pattern-match, which is
   exactly what the signals already do deterministically.

The discipline is: **the LLM is the last gate, not the only gate.
Everything it judges is already normalised. Everything it does not
need to judge is excluded from the prompt.**

## What this looks like end-to-end

1. The scanner runs every 15 minutes (or on every block, depending on
   chain).
2. For every DEX pair that surfaced, the adapter produces a normalised
   signal object.
3. The technical-signal layer adds the 5 computed signals.
4. The output is written to `state/signals_<timestamp>.json`.
5. The LLM reasoner reads each signal, sends it to the model with the
   BUY/SKIP prompt, parses the response, and acts on the vote.

The whole pipeline runs in a few hundred milliseconds per token. The
LLM call is the slowest part (~2 seconds on freeinference). The
throughput is dozens of tokens per minute on a single free-tier
provider.

## Why this is the most important piece

Everything else in the system is downstream of the normalised signal.
If the data is bad, the signals are bad, and the LLM vote is bad no
matter how clever the prompt is. If the data is good, even an average
prompt produces useful votes.

Spend the most time on the adapters and the normalisation. The
prompt engineering is the last 10%.

---

See [`case-study/`](https://github.com/Rawbeew/dont-get-rekt/blob/master/case-study/)
for a worked example showing what one BUY/SKIP vote looks like
end-to-end.
