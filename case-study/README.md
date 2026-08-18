# Case study — one BUY/SKIP decision

A worked example showing one end-to-end decision the engine made.
Anonymised, illustrative, but using the actual signal shapes and prompt
the system produces.

---

## The setup

A token surfaces in the Solana DEX feed as a candidate:

```
token:    <REDACTED> (anonymised Solana memecoin)
chain:    solana
pair:     <REDACTED>/USDC
24h vol:  $184,300
liquidity: $48,200
age:      11 hours
holders:  1,847
```

The multi-chain scanner flags it because its 24h volume is 4.3x its
7-day average, and its liquidity ($48k) is in the sweet-spot band.

The LLM judge is the final stage. It receives a pre-normalised signal
summary — the model does **not** call any APIs itself.

---

## What the LLM saw (the normalised signal summary)

```
== CHAIN CONTEXT ==
chain:           solana
token_age_hours:  11
holders:         1,847
top_10_pct:       31.2%   (concentration: moderate)
mint_authority:  null    (revoked: good)
freeze_authority: null    (disabled: good)

== DEX CONTEXT ==
liquidity_usd:    48,200
vol_24h_usd:      184,300
vol_vs_7d_avg:    4.3x
buys_24h:         1,213
sells_24h:        814
buy_ratio:        0.598   (just below 0.60 threshold)
price_change_24h: +0.07   (essentially flat)

== TECHNICAL SIGNALS ==
vwap_position:    +0.42%  (above VWAP: bullish)
sfp_24h:           absent  (no swing-failure pattern)
engulfing_24h:     absent
cvd_divergence:    absent
volume_spike_2x:   YES    (4.3x ratio is well past 2x)

== PRIOR ACTIVITY ==
last_buy_minutes_ago: 3
trades_last_30min:     127
```

---

## The system prompt

```
You are a crypto trade analyst on Solana and EVM chains.
You review potential buys and vote BUY or SKIP. Consider:
- Mint authority MUST be revoked (ACTIVE = rug risk, SKIP)
- Freeze authority MUST be disabled (ENABLED = rug risk, SKIP)
- Liquidity: $12.5k-$200k is ideal. <12.5k = too risky, >200k = no upside
- Buy ratio >60% with price flat or slightly up = accumulation, BUY
- Buy ratio <50% with price down = dumping, SKIP
- Volume surge >1.5x with price <25% change = smart money loading, BUY
- Token last bought 30+ mins ago (no recent activity) = dead, SKIP
- Supply reasonable (<1 trillion)

Rules:
- Keep reasoning under 2 sentences
- MUST start with BUY: or SKIP:
- If mint auth is ACTIVE → SKIP: (rug risk)
- If freeze auth is ENABLED → SKIP: (rug risk)
```

---

## The model's response (truncated)

```
BUY: Top-10 holder concentration is moderate at 31%, mint/freeze
authorities are revoked, and liquidity ($48k) sits in the sweet-spot
band. The 4.3x volume surge with price essentially flat signals
accumulation, not dumping, and trades in the last 30 minutes are
active. Buy ratio (0.598) is just below the 0.60 threshold but the
volume signal is the stronger gate.
```

---

## What the engine did with the response

The response started with `BUY:`, was under 2 sentences, and referenced
real gates. The engine:

1. **Parsed the vote.** `BUY:` → open a paper position.
2. **Computed position size.** `PAPER_POSITION_SIZE_PCT` (default 5% of
   paper balance) on the paper portfolio.
3. **Set exits.** Stop-loss at `-PAPER_STOP_LOSS_PCT` (default -15%) and
   take-profit at `+PAPER_TAKE_PROFIT_PCT` (default +35%).
4. **Wrote the position to state.** JSON in `state/paper_positions.json`.
5. **Sent a Telegram alert.** With the reasoning trace and a link to
   the chart.

No real money moved. No broker called. The decision is fully replayable
from the state files.

---

## Why this is a useful artefact

Three things make this trace useful, even as a worked example:

1. **The signal summary is human-readable.** Anyone with on-chain
   knowledge can sanity-check the LLM's reasoning without trusting it.
2. **The format requirement is enforceable.** `must start with BUY: or
   SKIP:` means a downstream parser never has to guess the vote.
   `2-sentence reasoning` means the audit trail stays compact.
3. **The model is gated, not trusted.** Every gate the LLM cites
   (mint authority, liquidity, volume ratio, recent activity) is a
   value the pipeline already computed. The LLM is the *judge*, not
   the *source of truth*.

If the model returned `BUY: ...` but cited a liquidity of $300k (the
sweet-spot cap is $200k), the engine would have rejected the vote and
emitted a SKIP instead. That backstop is what makes the LLM safe to
include in the loop at all.

---

## A note on this version

This case study is **anonymised**. The token address and pair are
removed because they identify a specific on-chain asset. The signal
shapes, the prompt, and the model's reasoning trace are real patterns
the system produces — what is missing is only the specific token.

Every number in the signal summary is the shape of what the pipeline
emits. The model's response is illustrative — a real response for a
real token would have similar structure but different specifics.
