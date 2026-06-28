# LLM Analyst Copilot

The LLM layer is intentionally not a trading engine.

It can:

- Retrieve relevant local context from risk alerts, signals, paper trading, backtests, and audit state.
- Retrieve operational runbook and tiny-live readiness evidence for no-go/watchlist review.
- Explain why risk state or signal rankings changed.
- Summarize source-grounded advisories.
- Extract candidate risk items from unstructured text.

It cannot:

- Authorize live trades.
- Override kill switches.
- Size orders.
- Bypass venue/asset allowlists.
- Reveal secrets, full balances, addresses, private labels, or account identifiers.

## Environment

```bash
LLM_API_ENABLED=false
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=gpt-5.5
```

The provider interface uses OpenAI-compatible chat completions. If no key is configured, `/api/analyst/copilot` returns an offline extractive answer from local RAG context.

## Local Context

The analyst corpus includes redacted market, risk, paper, edge-scoreboard, system-trust, operational-runbook, and tiny-live-readiness summaries. Readiness answers can explain current blockers and next evidence, but they cannot promote the system into live trading.

## Extension Path

To add embeddings or reranking later, keep them behind the same RAG boundary. Never put raw secrets or complete account identifiers into the LLM context.
