---
title: "Free-tier LLM as the final-stage judge: cost, reliability, and the failover router"
date: 2026-08-18
tags: [llm, free-tier, failover, multi-provider, openai-compatible, reliability, crypto]
canonical: https://github.com/promptcracka/dont-get-rekt
---

# Free-tier LLM as the final-stage judge: cost, reliability, and the failover router

The most underrated design choice in this system is also the most
mundane: **the LLM judge runs on the free tier.** No paid inference in
the loop by default. The same free-tier failover router that runs the
rest of your stack runs this one.

This post is about why that choice is correct, what it costs, what it
breaks, and how the failover router makes it reliable.

## The cost argument

A free-tier LLM call costs $0. A paid-tier LLM call costs
$0.0001–$0.01 depending on the model and the prompt. Over a few hundred
votes a day, the difference is single-digit dollars per month.

That is not a meaningful cost for a real system. The cost is a real
consideration only if you are running millions of votes a day —
which you shouldn't be, because the model is the *bottleneck on
quality*, not on throughput.

So the cost argument is not the reason. The reason is **reliability and
discipline**, which is where the free tier genuinely wins.

## The reliability argument

Paid-tier inference has a 99.9% SLA. Free-tier inference has... well,
nothing in writing. In practice:

- Freeinference, Groq, NVIDIA NIM, and Cloudflare Workers AI all have
  free tiers that are *reliable enough* for a side project but each
  has its own failure modes.
- One provider rate-limits you at peak. Another is queued for a few
  minutes. A third returns empty content because the model is
  configured for trivial prompts.
- If you depend on a single paid provider, you have one point of
  failure. If you depend on a free-tier router, you have N points of
  failure — and N is much bigger than 1.

The fix is a **failover router**: a small piece of code that tries
provider A, on failure tries provider B, on failure tries provider C,
and reports which one succeeded. The router is a few hundred lines of
Python. It runs in 10ms per request when nothing fails.

## The discipline argument

Using a free-tier router for the LLM judge has a side effect that is
*good*, not bad: **you cannot afford to be wasteful with calls.**

A paid tier tolerates "throw a thousand calls at it, see what works."
A free-tier router rewards "think about what to call, then call it
once." That discipline shows up in the design of the system:

- The LLM judge is called **once per token**, after the signal pipeline
  has already pre-filtered candidates. There's no "let me ask the
  model to also fetch the data" — the data is already there.
- The prompt is **structured**, so the model's first token is
  `BUY:` or `SKIP:`. No retry loop, no "let me parse that", no
  "let me ask again with a different prompt." One vote, one prompt,
  one decision.
- The state is **replayable**. If the LLM is down for an hour, the
  system runs the next cycle and asks again. There is no "we lost
  yesterday's votes" — yesterday's votes are in the JSON.

These are not compromises forced by the free tier. They are good
designs that happen to be free-tier compatible.

## The router in practice

The actual router used here is `src/ai_failover.py` from the
`promptcracka/flippy` portfolio. It does five things:

1. **Free-first ordering.** Freeinference → Groq → NVIDIA → Cloudflare.
   A paid fallback is included but commented out by default.
2. **Fail fast.** A 429 or 5xx from one provider triggers an
   immediate attempt on the next.
3. **Cooldown.** A provider that just failed twice is left alone for
   60 seconds.
4. **Pure stdlib.** No `litellm`, no `requests`, no installation
   required. Drop it into any project.
5. **Secrets stay secrets.** Reads API keys from environment
   variables. No keys in code, no keys in git.

The same router handles the LLM judge, the RAG embeddings, the
document summarisation, and the rest of the system's LLM needs. One
component, many uses.

## What breaks when the free tier fails

Three failure modes are common:

### 1. Provider rate-limits at peak
Symptom: 429 response from one provider. Fix: the router moves to the
next. If all four providers 429 simultaneously (rare but possible),
the engine treats it as a SKIP vote (no LLM vote → no BUY).

### 2. Model returns empty content
Symptom: 200 response, but `choices[0].message.content` is empty or
whitespace. Some models (notably reasoning-heavy ones on trivial
prompts) spend the response budget on reasoning and leave the content
empty.

Fix: the router's "empty content" check catches it and moves to the
next provider. If the first provider returned empty, the second
provider is asked with the same prompt (usually succeeds — empty
content is rarely correlated across providers).

### 3. Model is queued or rate-limited server-side
Symptom: 200 response, but content is a "queue position" message or a
"please try again later" message.

Fix: the router's `is_rate_limit` check catches it and moves to the
next. The current providers (freeinference, Groq, NVIDIA, Cloudflare)
are unlikely to queue — but the check is in place for when they do.

## What this looks like in production

A typical day:

- 150 BUY/SKIP votes triggered by the signal pipeline.
- 3 votes fail on the first provider, succeed on the second.
- 1 vote fails on all four providers, gets logged as a SKIP with
  reason "LLM unavailable".

Total: 150 votes resolved, 0 dropped, 0 stuck. Cost: $0.

## What I'd change if I were starting over

Three improvements I'd make to the current router, ordered by
importance:

1. **Per-provider confidence.** Some providers consistently return
   better votes for this domain. Track vote-outcome correlations and
   weight the router accordingly. Not a free-tier concern — a quality
   concern.
2. **Streaming for the LLM judge.** Currently the router waits for
   the full response. Streaming would cut perceived latency by ~50%
   for the operator. Adds complexity to the parser; deferred.
3. **Async batching.** When the signal pipeline produces 30 candidates
   in the same second, batch them into one model call. Could 5x the
   throughput on the same free tier. Significant parser rework.

None of these are required for the system to work. They are
optimisations for a hypothetical future where the system is voting on
hundreds of tokens per minute, not dozens per day.

## The single most useful thing

If you remember nothing else: **the LLM is one component of your
system, not the whole thing.** Treat it like a paid dependency you
have to budget for, but use the free tier so the budget is invisible.
The discipline of "one vote per call, structured prompt, parseable
response" is what makes the free tier work. The free tier is not the
constraint — the discipline is.

---

See [`posts/structured-buy-skip-prompting.md`](https://github.com/promptcracka/dont-get-rekt/blob/master/posts/structured-buy-skip-prompting.md)
for what the prompt looks like.
