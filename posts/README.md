# Posts

The engineering design decisions that make an LLM-as-judge crypto
scanner work — written for engineers building similar systems.

| Post | What you'll learn |
|---|---|
| [Multi-chain DEX and CEX signal scanner](./multi-chain-dex-and-cex-signal-scanner.md) | How CEX OHLCV + 90+ chain DEX feeds become one normalised signal stream the LLM can reason about, without the LLM calling any APIs itself. |
| [Why paper mode by design](./why-paper-mode-by-design.md) | What "no live broker, ever" unlocks — four classes of risk removed, plus the discipline that makes the rest of the system possible. |
| [Structured BUY/SKIP prompting](./structured-buy-skip-prompting.md) | The prompt that turns an LLM into a single-line judge: hard format requirement, explicit risk gates, 2-sentence reasoning cap. |
| [Free-tier LLM as the final-stage judge](./free-tier-llm-as-the-final-stage-judge.md) | Why the cost model for the LLM judge is free-tier-by-default, what breaks when it's not, and how the same failover router that runs the rest of your stack runs this one. |

For the worked example, see [`case-study/`](./case-study/) — one
BUY/SKIP decision on a real (anonymised) token, with the inputs the
LLM saw and the reasoning trace it produced.

There's also a machine-readable summary at [`LLM.txt`](../LLM.txt) for
AI crawlers.
