# signal-then-vote

> The portfolio version of a paper-mode crypto signal engine: multi-chain
> data ingestion + a free-tier LLM casting the final structured BUY/SKIP vote.

This is the **public, watered-down version** of a larger private system
that runs as a side project. It's here to show how the pieces fit
together and what each one does. The full pipeline — every signal
threshold, every prompt tweak, every paper-position — stays private.

---

## The idea in one sentence

> Ingest many noisy signals from many chains, then ask a language model
> to cast one structured vote — BUY or SKIP — on a single token at a time,
> with explicit risk gates that a model can be reasoned with.

Not "this indicator says buy." **"All seven signals say X, the on-chain
context says Y, and a structured prompt forces the LLM to commit to BUY
or SKIP in one line, citing which gate flipped it."**

---

## What's in this portfolio

A curated selection of the design decisions and engineering work that
make the idea executable. Five things total:

### 1. The core feature
- **[The structured BUY/SKIP prompt as a final-stage judge](./case-study/)**
  — the one prompt that turns a pile of on-chain data into a single,
  defensible trade vote. Includes a worked example with a real
  Solana/EVM token and the model's full reasoning trace.

### 2. Four things that make it real (special features)
- **[`posts/multi-chain-dex-and-cex-signal-scanner.md`](./posts/multi-chain-dex-and-cex-signal-scanner.md)**
  — How CEX OHLCV + 90+ chain DEX feeds become one normalised signal
  stream the LLM can reason about, without the LLM having to call any
  APIs itself.
- **[`posts/why-paper-mode-by-design.md`](./posts/why-paper-mode-by-design.md)**
  — Why "no live broker, ever" is the design choice that makes the rest
  of the system possible — and what it costs and saves.
- **[`posts/structured-buy-skip-prompting.md`](./posts/structured-buy-skip-prompting.md)**
  — The prompt that turns an LLM into a single-line judge: explicit risk
  gates, a hard format requirement (must start with `BUY:` or `SKIP:`),
  and a 2-sentence reasoning cap.
- **[`posts/free-tier-llm-as-the-final-stage-judge.md`](./posts/free-tier-llm-as-the-final-stage-judge.md)**
  — Why the cost model for the LLM judge is free-tier-by-default, what
  breaks when it's not, and how the same failover router that runs
  the rest of your stack runs this one.

### 3. The case study
- **[`case-study/`](./case-study/)** — a worked example of one BUY/SKIP
  decision on a real (anonymised) Solana token, with the inputs the
  LLM saw and the reasoning trace it produced.

---

## Why this is a portfolio, not a product page

Three honest reasons:

1. **The full system is private.** Every signal threshold, every prompt
   weight, every paper-position outcome — none of it is here. What is
   here is the *shape of the pipeline* and the engineering decisions
   that make it work.
2. **The case study is the proof.** A real BUY/SKIP trace on a real
   token is more useful to another builder than the source code, because
   the prompt engineering is what makes or breaks the LLM-as-judge.
3. **The interesting work is the prompt.** The data ingestion is
   plumbing. The LLM vote is the product. Showing the *thinking* — how
   the prompt evolved, what gates work, what fails — is more useful
   than showing the code that calls the API.

---

## How to navigate this

- New here? Start at [`case-study/`](./case-study/) — see one BUY/SKIP
  decision end to end.
- Building a similar system? Read the four posts in order. Each is a
  design decision, not a tutorial.
- Hiring for an AI/LLM engineering role? This is the kind of work the
  role actually does — structured prompts, real data, real stakes.

---

## License

MIT. See [`LICENSE`](./LICENSE).
