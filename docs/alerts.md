# Alerts and Telegram/SMS

Alerting code lives under `src/lib/alerts` and `src/lib/telegram`.

## Severities

- `INFO`: Telegram only. Routine digest or refresh information.
- `WATCH`: Telegram only. Interesting but not actionable yet; cooldown defaults to 45 minutes.
- `TRADEABLE`: Telegram only. Paper-review candidate with edge, risk, liquidity, and explanation.
- `CRITICAL`: Telegram plus Twilio SMS. Blocks affected live venue/asset and requires acknowledgement.
- `BLACK`: Telegram plus Twilio SMS. Global live halt and repeat-until-acknowledged policy.

## Safety Rules

Messages are redacted before delivery. The router must never send API keys, full balances, withdrawal addresses, private wallet labels, raw secrets, or full account identifiers.

## Telegram Commands

Only `TELEGRAM_AUTHORIZED_CHAT_IDS` are accepted.

- `/status`
- `/risk`
- `/signals`
- `/positions`
- `/alerts`
- `/ack ALERT_ID`
- `/pause 1h|6h|24h`
- `/kill`
- `/resume`

`/kill` activates a BLACK-state workflow. `/resume` only requests review; dashboard confirmation is required before any live trading can exist.

## Official APIs

- Telegram Bot API: https://core.telegram.org/bots/api
- Twilio Messaging: https://www.twilio.com/docs/messaging/api/message-resource
