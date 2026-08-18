---
title: "Structured BUY/SKIP prompting: turning an LLM into a single-line judge"
date: 2026-08-18
tags: [llm, structured-prompting, prompt-engineering, json-output, crypto, decision-systems]
canonical: https://github.com/Rawbeew/signal-then-vote
---

# Structured BUY/SKIP prompting: turning an LLM into a single-line judge

The single most useful thing you can do when an LLM is one *component*
of a larger system — not the whole thing — is to make its output
**parseable**. A model that returns a free-text paragraph is a model
your downstream code can't safely act on. A model that returns exactly
`BUY: <2-sentence reason>` or `SKIP: <2-sentence reason>` is one you
*can* act on, with the parser and the prompt as the contract.

This post is about that contract — what it looks like in practice, why
each part matters, and what to do when the model breaks it.

## The full prompt

The system prompt that turns the model into a single-line judge:

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

Three things in this prompt do most of the work:

1. **"MUST start with BUY: or SKIP:"** — this is the format contract.
   The parser relies on it. Every prompt iteration has strengthened
   this line, because models drift toward politeness ("I'd consider
   this a...") without it.
2. **"Keep reasoning under 2 sentences"** — caps the reasoning length.
   Without this, the model produces paragraphs that the parser has to
   truncate and the audit trail becomes hard to read.
3. **Explicit rug-risk gates at the bottom** — `if mint auth is
   ACTIVE → SKIP:`. The model is asked to consider these, but the
   pipeline has the *same* gate. If the model returns `BUY:` but
   mint authority is ACTIVE, the engine overrides to `SKIP:`. The
   prompt and the engine form a redundancy pair.

## The shape of a good response

```
BUY: Top-10 holder concentration is moderate at 31%, mint/freeze
authorities are revoked, and liquidity ($48k) sits in the sweet-spot
band. The 4.3x volume surge with price essentially flat signals
accumulation, not dumping, and trades in the last 30 minutes are
active.
```

Two sentences. One BUY vote. Cites four concrete gates. Easy to audit.

## The parser

The parser is small — fifteen lines of Python — and it does four
things in order:

```python
def parse_vote(response):
    text = response.strip()
    if not text.startswith(("BUY:", "SKIP:")):
        raise VoteParseError("missing vote prefix")
    vote = "BUY" if text.startswith("BUY:") else "SKIP"
    reason = text[len(vote)+1:].strip()
    # hard cap at 2 sentences — anything past is truncated for the audit log
    sentences = reason.split(". ")
    if len(sentences) > 2:
        reason = ". ".join(sentences[:2]) + "."
    return vote, reason
```

The parser is the second half of the prompt/parser contract. If the
prompt asks for `BUY: ...` and the parser requires `BUY: ...`, the model
that fails either will be obvious in the first ten votes.

## What to do when the model breaks the format

This happens more than you'd hope. Common failures:

- **Politeness preamble.** "Sure! I'd consider this a BUY because..."
  Fix: tighten the prompt, drop the politeness pattern, re-run.
- **Two votes in one response.** "BUY but also consider SKIP if..."
  Fix: the prompt's "MUST start with BUY: or SKIP:" plus a parser that
  takes the first token. If it happens often, add: "one vote per
  response, no hedges."
- **Format drift.** Model starts responding `BUY -` or `BUY:` followed
  by markdown. Fix: explicit format examples in the prompt.
- **Empty response.** Model says nothing. Fix: the parser raises, the
  engine defaults to SKIP (since the model couldn't confirm a BUY).

In every case, **the engine treats a parse failure as a SKIP vote.** The
worst case of "we couldn't read the model" is "we didn't trade this
token." The worst case of "we trusted the model when we shouldn't have"
is much worse.

## Why "2 sentences" is the right cap

Two reasons:

1. **Auditability.** A two-sentence reason fits in a Telegram
   notification. A paragraph does not. Operationally, you want to be
   able to read each vote in 10 seconds during a fast market.
2. **Forces specificity.** A model with a 2-sentence cap has to choose
   *which* gates to cite. The choice is informative: a model that
   cites only the volume spike is making a momentum play; a model that
   cites mint authority + liquidity is making a rug-screen play.

The cap is also enforced server-side in the parser. If the model
produces 3 sentences, the third is truncated from the audit log
(but the vote still stands). This is a soft enforcement — the model
sees the soft cap in the prompt and the engine sees the hard cap in
the parser.

## Why the gates are in the prompt *and* in the engine

This is the single most important design decision. The model sees the
gates. The engine sees the gates. **They are independent.** The model
cannot approve a trade that the engine has flagged, and the engine
cannot approve a trade the model has rejected (unless explicitly
overridden by the operator).

This is the redundancy pattern in a single system:

- The model is a judge with its own perspective and biases.
- The engine is a verifier with deterministic rules.
- Both must agree for a BUY vote to execute.

When they disagree, the system default is **SKIP**. Conservative by
default. Live trading, when it eventually happens, will inherit this
default — the engine still says SKIP if the model and the rules
disagree.

## What the prompt is not

The prompt is not:

- **A personality.** No "you are an experienced trader" framing. The
  model is not playing a role; it is making a decision. Adding
  personality adds variance without adding signal.
- **A risk-management framework.** Risk management is the engine's
  job (position size, stops, targets). The prompt focuses on the
  decision; the engine focuses on the size.
- **A long-term memory.** The model has no memory across calls.
  Whatever context the prompt gives is the only context. That
  discipline is what makes the audit trail replayable.

Each of these is a thing the prompt could do. Each of them was tried
and removed because it added variance or reduced auditability.

## What the prompt evolved through

The current version is the sixth iteration. The five before it:

1. Free-text paragraph. (No format. The model produced beautiful
   analyses that the parser couldn't read.)
2. JSON output. (The model produced valid JSON about 60% of the time.
   The other 40% was the parser raising.)
3. "Vote + reason" with no prefix. (The model drifted toward "I'd
   recommend" or "Looking at this".)
4. Hard prefix `BUY:` / `SKIP:` with no length cap. (The model
   produced 4-paragraph reasons. Hard to scan in Telegram.)
5. Hard prefix + 2-sentence cap + explicit rug gates. (Current
   version.)

Iteration 5 has held up over a few hundred votes. The next likely
change is adding a confidence level (`BUY (high):` / `BUY (med):`)
once we have enough votes to compare confidence calibration against
outcomes.

## The single most useful thing

If you remember nothing else: **the prompt and the parser are a
contract.** If the prompt says `BUY: ...` and the parser requires
`BUY: ...`, the model that fails either is obvious. If the prompt
says "vote and explain" and the parser does its best to interpret,
the model that fails is silent — and that's the dangerous one.

Make the contract explicit. Make the parser strict. Default to SKIP
on parse failure. Iterate the prompt until the model is reliable, not
until the model is impressive.

---

See [`posts/multi-chain-dex-and-cex-signal-scanner.md`](https://github.com/Rawbeew/signal-then-vote/blob/master/posts/multi-chain-dex-and-cex-signal-scanner.md)
for what the LLM sees. See [`case-study/`](https://github.com/Rawbeew/signal-then-vote/blob/master/case-study/)
for a worked example.
