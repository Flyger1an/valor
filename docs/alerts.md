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

## Alert Sources

- `risk-engine`: market risk alerts converted from active risk state.
- `system-trust-gate`: data trust, kill switch, scheduler, alert-delivery, and paper-ledger health issues. Caution produces `WATCH`; paper-blocking issues produce `CRITICAL`; global halt conditions such as kill switch or Black risk produce `BLACK`.
- `edge-scoreboard`: underperforming signal families. These alerts explain paper PnL evidence and mirror the edge policy that marks that family watch-only.
- `relative-value-signal-engine`: top paper-eligible signal candidates.
- `system-digest`: refresh summary with signal count, paper PnL, and market risk state.

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
