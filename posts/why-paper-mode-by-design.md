---
title: "Why paper mode by design: what removing the broker unlocks"
date: 2026-08-18
tags: [crypto, trading-bot, paper-trading, llm, safety, backtesting]
canonical: https://github.com/Rawbeew/dont-get-rekt
---

# Why paper mode by design: what removing the broker unlocks

The single most important design decision in this system is also the
least interesting-sounding: **there is no live broker.** No API keys.
No exchange integration. No real money. Every trade is simulated.

This post is about why that's not a missing feature — it's the choice
that makes everything else possible.

## What you lose

Be honest first. What you give up by skipping a live broker:

1. **No real P&L.** The system cannot make you money. Whatever P&L the
   paper ledger shows is fictional.
2. **No "edge" claim.** A real trading bot is sold on its edge.
   "Last quarter our bot made 23%." This system cannot make that
   claim, because the trades never happened.
3. **No live validation.** A live broker integration would prove the
   model and the prompts against reality. Paper mode keeps the
   validation in simulation, which is weaker.

That's a real cost.

## What you gain

What you get back is much bigger. Specifically, removing the broker
removes four entire classes of risk:

### 1. Account risk
A live trading bot needs an exchange account. The exchange account
needs API keys. The API keys live somewhere — on disk, in env vars,
in a secret store. Each of those is an attack surface. If the keys
leak, the account is at risk; the bot can be turned against you;
someone can drain funds.

Paper mode has no keys, no account, no funds. The only thing that can
leak is a paper-P&L JSON file.

### 2. Strategy-leak risk
A live bot's strategy is valuable. A 2x-volume-spike detector with the
right liquidity band and the right mint-authority gate is, in
principle, a money-making strategy. The moment a live bot starts
trading, that strategy is observable: the trades on-chain are public;
the timing is public; the patterns are public.

Paper mode hides the strategy entirely. You can run it for months,
publicly, while iterating. The strategy only becomes public when you
decide to deploy it.

### 3. Prompt-injection risk
A live bot with an LLM is a juicy target. If an attacker can influence
what the LLM sees — by manipulating the prompt, by injecting content
into the data sources, by social-engineering the operator — they can
make the bot trade in their favour. Every external data source becomes
an attack surface.

Paper mode turns this from "catastrophic" to "annoying." A prompt-
injected vote produces a paper position. You see it in the ledger. No
real money moved. You can iterate on the defence without risking the
account.

### 4. Tail-risk
A live bot can lose real money in a market crash, a flash loan attack,
or an exchange insolvency. Each of these is a black-swan event the
LLM probably has never seen in training data.

Paper mode turns these into "interesting notes in the trade log." You
see the paper position absorb the crash. You learn how the model
behaves under tail conditions. **You learn without paying for the
lesson.**

## Why this enables the LLM

A live trading bot with an LLM is a system where a hallucination has a
direct dollar cost. Every prompt is loaded with the implicit question:
"are you sure you want to send this?" Every safety gate is fighting
against the model's tendency to be helpful.

Paper mode inverts the question. The LLM's job is to *vote*, not to
*execute*. The vote is acted on by the engine only if it passes the
parser (`must start with BUY: or SKIP:`) and only if the cited gate
values match the pipeline's values. The LLM is free to be curious,
skeptical, or wrong — the system has backstops.

This is the part most "LLM-as-trader" projects get wrong. They hand the
model too much authority. They remove the backstops because they make
the demo "less impressive." The result is a system that works in the
demo, blows up in production.

## What paper mode still answers

The most useful question paper mode answers is **"is the LLM adding
value over a pure quant baseline?"** Run the same signal pipeline
without the LLM vote (just the buy-ratio + volume-spike gates). Run it
with the LLM vote. Compare the paper P&L over a few months. If the
LLM-augmented version does not beat the pure-quantile baseline, the
prompt is not earning its keep.

This is the honest benchmark. It does not require live trading. It
requires accurate paper execution, accurate price simulation, and
enough time for the difference to mean something.

## When to add a live broker

There is a real moment to add a live broker — and it's not "the model
seems good." It's:

1. **The paper P&L has been positive for a statistically meaningful
   window** (months, not weeks).
2. **The LLM beats a no-LLM baseline on the same window.**
3. **The risk gates have been tested adversarially** (see the
   adversarial-testing post in the outreach portfolio for the
   pattern; the same idea applies here).
4. **You can afford to lose everything you're about to deploy.** Not
   "would be a shame to lose" — actually lose. If the answer to this
   fourth question is no, the system stays in paper mode.

This is the discipline. Paper mode is the default. Live mode is the
exception, gated by four checkpoints.

## What the paper ledger looks like

The state file is plain JSON, one record per open position:

```json
{
  "token": "<REDACTED>",
  "chain": "solana",
  "side": "long",
  "entry_price_usd": 0.000043,
  "entry_time": "2026-08-18T13:42:00Z",
  "position_size_usd": 5000,
  "stop_loss_usd": 0.0000366,
  "take_profit_usd": 0.0000581,
  "vote_reason": "BUY: top-10 holder concentration moderate, mint/freeze revoked, liquidity $48k in sweet-spot, 4.3x volume surge with price flat = accumulation",
  "status": "open"
}
```

Every record carries the **vote reason** verbatim from the LLM. That
single field is the audit trail — anyone can later look at the position
and see exactly what the model said and why.

When the position closes (stop, target, or expiry), the trade log gets
a matching `closed` entry with the realised P&L.

## The single most useful thing

If you remember nothing else: **paper mode is the design, not a
phase.** Most projects treat paper mode as "we'll go live once we have
a good model." This project treats paper mode as "we'll go live once
we have evidence the model adds value over six months." Those are
different bars. The second one is much higher.

It is also much safer to clear.

---

See [`case-study/`](https://github.com/Rawbeew/dont-get-rekt/blob/master/case-study/)
for a worked example.
