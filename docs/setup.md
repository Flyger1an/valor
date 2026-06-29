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
- `VALOR_OPS_SECRET`: Required in production for ops APIs. Send it with `X-Valor-Ops-Secret` or `Authorization: Bearer <secret>`.
- `VALOR_SESSION_SECRET`: Required in production for signed browser session cookies.
- `VALOR_ADMIN_PASSWORD_HASH`: Required in production for browser login. Use the scrypt hash format below.
- `VALOR_SESSION_TTL_SECONDS`: Browser session lifetime. Defaults to 12 hours.
- `VALOR_REQUIRE_BROWSER_AUTH`: Set to `true` to force browser login in local development.
- `VALOR_PUBLIC_READ_APIS`: Defaults to protected read APIs in production. Set to `true` only behind private network controls when scripts must read health/status/evidence endpoints without the ops header.
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

## Browser Session Auth

Production dashboard access uses a signed HttpOnly `valor_session` cookie. Browser API calls can use that session cookie; scheduler, soak, and external scripts should keep using `VALOR_OPS_SECRET`.

Generate a session secret:

```bash
openssl rand -base64 32
```

Generate an admin password hash:

```bash
node -e 'const { randomBytes, scryptSync } = require("crypto"); const password = process.argv[1]; const salt = randomBytes(16).toString("base64url"); const hash = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 }).toString("base64url"); console.log(`scrypt$16384$8$1$${salt}$${hash}`);' 'replace-with-operator-password'
```

Do not commit either value. Keep `VALOR_OPS_SECRET` separate from `VALOR_SESSION_SECRET` so machine access can be rotated independently from browser login.

## Local Persistence

Valor now uses a SQLite-backed state store for local file URLs such as `file:./valor.sqlite`. If `DATABASE_URL` is absent, it defaults to `.valor/valor.sqlite`. The store writes a dashboard snapshot plus normalized rows for market snapshots, signals, risk states, data-quality reports, backtests, paper trades/positions, live/dry-run attempts, alerts, audit events, kill-switch state, and action log entries.

Postgres URLs currently fall back to the JSON store until a Postgres adapter is added. Use `VALOR_STATE_BACKEND=json` when you explicitly want the old local JSON behavior.

## Scheduler

`npm run scheduler` runs a small HTTP driver that calls `POST /api/ops/scheduler` on the app. Each cycle refreshes data, computes signals and risk, stores a paper-trading preview, records audit/status information, and tracks alert events. External alert delivery remains off unless `SCHEDULER_SEND_ALERTS=true`. When `VALOR_OPS_SECRET` is set, the scheduler and soak scripts send it automatically.

Ops APIs apply in-process rate limits per route and client identity. Use a reverse proxy or private network for stronger cross-process/global rate limiting on a public VM.

Scheduler cycles now persist an active run id, heartbeat timestamp, skip timestamp, and stale-run recovery count. A fresh active heartbeat causes overlapping cycle requests to skip. A stale heartbeat is recovered and recorded before a new cycle starts.

Use `GET /api/ops/scheduler` to inspect the latest scheduler status.

Use `GET /api/ops/health` for a read-only deployment health report. It checks persisted state and guardrails without refreshing data, running scheduler cycles, or mutating the paper ledger. In production, include the ops header unless `VALOR_PUBLIC_READ_APIS=true`.

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
