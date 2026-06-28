# Setup

1. Install dependencies:

```bash
npm install
```

2. Copy `.env.example` to `.env.local` and change only the settings you need. Do not commit `.env.local`.

3. Run tests:

```bash
npm run test
```

4. Start the app:

```bash
npm run dev
```

The MVP defaults to sample data. Leave `ENABLE_PUBLIC_MARKET_FETCH` absent or `false` for deterministic fixtures. Use `coingecko` for the spot-only CoinGecko path, or `true`, `public`, or `live` for the broader public connector with fixture fallback.

## Environment Variables

- `DATABASE_URL`: SQLite file path for the planned local store.
- `VALOR_STATE_BACKEND`: Optional override. Set to `json` to force `.valor/state.json`; otherwise file/SQLite `DATABASE_URL` values use the SQLite state store.
- `ENABLE_PUBLIC_MARKET_FETCH`: `false`/absent uses sample fixtures; `coingecko` uses CoinGecko spot data; `true`/`public`/`live` uses the broader public connector.
- `ENABLE_LIVE_TRADING`: Must be `true` before any future executor can even be evaluated.
- `REQUIRE_MANUAL_LIVE_CONFIRMATION`: Defaults to `true`.
- `LIVE_TRADING_DRY_RUN`: Defaults to `true`; the v0.2 executor records local order intents only while this remains true.
- `LIVE_KILL_SWITCH`: Defaults to `true`.
- `LIVE_MAX_TRADE_USD`: Per-trade live notional cap.
- `LIVE_DAILY_LOSS_LIMIT_USD`: Daily live loss cutoff.
- `LIVE_MAX_LEVERAGE`: Defaults to `1`.
- `LIVE_ALLOWED_VENUES`: Comma-separated live venue allowlist.
- `LIVE_ALLOWED_ASSETS`: Comma-separated live asset allowlist.
- `VALOR_APP_URL`: Base URL used by the scheduler process to call the app, such as `http://127.0.0.1:3000` locally or `http://app:3000` in Docker Compose.
- `SCHEDULER_INTERVAL_MS`: Recurring scheduler cadence. Defaults to five minutes.
- `SCHEDULER_RUN_ON_START`: Runs one cycle when the scheduler starts unless set to `false`.
- `SCHEDULER_RUN_ONCE`: Runs a single scheduler cycle and exits.
- `SCHEDULER_SEND_ALERTS`: Defaults to `false`; set to `true` only when scheduler-triggered alert delivery should call the configured providers.
- `SCHEDULER_ALERT_LIMIT`: Maximum alert events processed per cycle when scheduler alert sending is enabled.
- `SCHEDULER_STALE_AFTER_MS`: Scheduler run lease timeout. Defaults to three scheduler intervals, with a floor of fifteen minutes.

## LLM Analyst Plug

- `LLM_API_ENABLED`: Enables external LLM calls when true.
- `LLM_API_BASE_URL`: OpenAI-compatible base URL, such as `https://api.openai.com/v1`.
- `LLM_API_KEY`: Provider key. Never expose it to the browser.
- `LLM_MODEL`: Model name.

The copilot uses retrieval over local risk/signals/backtests/paper state. It is never final trading authority.

## Local Persistence

Valor now uses a SQLite-backed state store for local file URLs such as `file:./valor.sqlite`. If `DATABASE_URL` is absent, it defaults to `.valor/valor.sqlite`. The store writes a dashboard snapshot plus normalized rows for market snapshots, signals, risk states, data-quality reports, backtests, paper trades/positions, live/dry-run attempts, alerts, audit events, kill-switch state, and action log entries.

Postgres URLs currently fall back to the JSON store until a Postgres adapter is added. Use `VALOR_STATE_BACKEND=json` when you explicitly want the old local JSON behavior.

## Scheduler

`npm run scheduler` runs a small HTTP driver that calls `POST /api/ops/scheduler` on the app. Each cycle refreshes data, computes signals and risk, stores a paper-trading preview, records audit/status information, and tracks alert events. External alert delivery remains off unless `SCHEDULER_SEND_ALERTS=true`.

Scheduler cycles now persist an active run id, heartbeat timestamp, skip timestamp, and stale-run recovery count. A fresh active heartbeat causes overlapping cycle requests to skip. A stale heartbeat is recovered and recorded before a new cycle starts.

Use `GET /api/ops/scheduler` to inspect the latest scheduler status.

Use `GET /api/ops/health` for a read-only deployment health report. It checks persisted state and guardrails without refreshing data, running scheduler cycles, or mutating the paper ledger.

Before deploying or after a droplet update, run a short scheduler soak against a running app:

```bash
SOAK_CYCLES=3 SOAK_DELAY_MS=2000 npm run soak:scheduler
```

The soak command intentionally calls `POST /api/ops/scheduler`, so it advances the paper/evidence loop. It fails fast on scheduler errors and finishes by reading `/api/ops/health`.

Restart recovery is part of the pre-deploy proof. The test suite verifies that SQLite-backed state can be closed, reopened, and compared against the pre-restart evidence snapshot without losing market data, data quality, paper ledger, scheduler status, audit trail, or health posture.

## Alerts

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_AUTHORIZED_CHAT_IDS`: Comma-separated authorized chat IDs.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_TO_NUMBERS`: SMS fallback settings.
- `TWILIO_DRY_RUN`: Keep true until SMS delivery has been manually tested.
