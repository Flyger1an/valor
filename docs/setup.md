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

Live public market data is the **default** (OKX primary, Binance/CoinGecko fallback) — no API key needed. Set `ENABLE_PUBLIC_MARKET_FETCH=false` to force an all-fixture bundle, or `=coingecko` / `=binance` to pin a single live source.

## Environment Variables

- `DATABASE_URL`: SQLite file path for the planned local store.
- `ENABLE_PUBLIC_MARKET_FETCH`: Routes the market-data connector. **Defaults to live** — unset or any value falls through to `PublicCryptoMarketConnector` (OKX + Binance/CoinGecko fallback). `false` → fixtures only; `coingecko` → CoinGecko spot only; `binance` → Binance spot+perp.
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
