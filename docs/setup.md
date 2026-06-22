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

The MVP defaults to sample data. Set `ENABLE_PUBLIC_MARKET_FETCH=true` only when you want the CoinGecko public adapter to replace spot fixtures.

## Environment Variables

- `DATABASE_URL`: SQLite file path for the planned local store.
- `ENABLE_PUBLIC_MARKET_FETCH`: Enables public spot price fetches. Defaults to `false`.
- `ENABLE_LIVE_TRADING`: Must be `true` before any future executor can even be evaluated.
- `REQUIRE_MANUAL_LIVE_CONFIRMATION`: Defaults to `true`.
- `LIVE_TRADING_DRY_RUN`: Defaults to `true`.
- `LIVE_KILL_SWITCH`: Defaults to `true`.
- `LIVE_MAX_TRADE_USD`: Per-trade live notional cap.
- `LIVE_DAILY_LOSS_LIMIT_USD`: Daily live loss cutoff.
- `LIVE_MAX_LEVERAGE`: Defaults to `1`.
- `LIVE_ALLOWED_VENUES`: Comma-separated live venue allowlist.
- `LIVE_ALLOWED_ASSETS`: Comma-separated live asset allowlist.

## LLM Analyst Plug

- `LLM_API_ENABLED`: Enables external LLM calls when true.
- `LLM_API_BASE_URL`: OpenAI-compatible base URL, such as `https://api.openai.com/v1`.
- `LLM_API_KEY`: Provider key. Never expose it to the browser.
- `LLM_MODEL`: Model name.

The copilot uses retrieval over local risk/signals/backtests/paper state. It is never final trading authority.

## Alerts

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `TELEGRAM_AUTHORIZED_CHAT_IDS`: Comma-separated authorized chat IDs.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_TO_NUMBERS`: SMS fallback settings.
- `TWILIO_DRY_RUN`: Keep true until SMS delivery has been manually tested.
