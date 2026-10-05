# Paper-order Telegram notifications

`telegram_alerts.py` is an outbound-only service for the existing Alpaca **demo**
book. It imports no broker, trading engine, model client or legacy Telegram bot.
It does not poll Telegram updates, register a webhook, process commands, resume
trading or place test orders. The legacy bot contains operational commands and
is deliberately not started by this deployment.

The notifier reads the original execution book through a read-only Docker
volume and SQLite `mode=ro` / `query_only` connection. Short read transactions
include the active WAL; `immutable=1` must not be used because it can omit WAL
updates. It uses its own persistent SQLite outbox and process lock. The source
book, policy, worker, feed, agents, virtual books and existing dashboard need no
code changes or restarts. No trading credential is mounted in the notifier.

## Events and privacy

One alert is created for each newly broker-acknowledged order. A durable
`order.status` event must say `open`, `partial` or `filled`, and the original
Alpaca receipt must contain a matching client ID and broker order ID. Intent,
review, rejected or uncertain submission events do not trigger a message.
Protective orders are identified explicitly. Subsequent fills/status/fee changes
do not create additional placement alerts. A filled/partial label reflects the
validated ledger report; an open acknowledgement says the fill is unconfirmed.

Every message starts `VALOR — PAPER/DEMO` and contains only instrument, side,
recorded status, UTC observation time and a short opaque reference. It contains
no balances, quantities, account IDs, broker order IDs, P&L, strategy prose or
credentials. Baseline, Kelly and Henry virtual-book events are not subscribed;
adding those is a separate notification-volume decision.

The source policy and `alpaca:demo:` identity are pinned. The configured bot
username and private recipient are checked with Telegram before delivery and
periodically thereafter. Groups, changed recipients, other bots and a live or
replaced source book fail closed. Use the already-verified private owner chat;
do not substitute a different chat or copy credentials without operator setup.

## Delivery behavior

- First activation baselines the source journal and existing broker receipts;
  old orders and their later status/fee updates are not replayed.
- A persistent order key suppresses duplicates across polls and restarts.
- At most one message is attempted every 15 seconds, with 100 pending messages
  maximum. Events/queued messages older than one hour expire instead of flooding
  the chat after a prolonged outage.
- Rate limits honor `retry_after`; failures known to precede submission use
  exponential backoff. A possibly accepted send (lost response, HTTP 5xx or crash
  during delivery) is recorded as **uncertain** and is not automatically resent.
  Telegram has no send-message idempotency parameter, so strict exactly-once
  delivery cannot be promised. This choice can leave an alert undelivered.
- Permanent failures and uncertain attempts make notifier health unhealthy.
  Inspect its private `status.json` and outbox before resolving them; do not
  erase state to retry. Telegram outages never block the trading worker.
- Redirects and raw exception logging are disabled. Logs/status contain fixed
  error codes and counts, not URLs with bot tokens or raw Telegram responses.

## Operator setup

Build `Dockerfile.telegram` from a minimal context using an official
`python:3.11-slim` base resolved to a digest. Set `VALOR_TELEGRAM_IMAGE` to the
verified image and the two private directory paths required by
`telegram.compose.yaml`. There are no new Python dependencies or published ports.

Create `notifications.json` in the private configuration directory:

```json
{
  "schema_version": 1,
  "policy_hash": "<existing demo policy hash>",
  "broker_identity": "alpaca:demo:<existing identity suffix>",
  "recipient_hash": "<SHA-256 of the existing private numeric chat ID>",
  "bot_username": "<verified existing bot username>",
  "instruments": ["ADA-USD", "BTC-USD", "ETH-USD", "SHIB-USD", "SKY-USD", "WIF-USD"]
}
```

An existing notifier must use the explicit `migrate_config` boundary described
in [UNIVERSE.md](UNIVERSE.md). Set `acceptance_identity_policy_hash` to its
original policy hash so accepted-order identities keep the same deduplication
namespace. Preserve the source cursor, historical orders, delivery outbox,
recipient, bot identity and credential mounts. Replacing configuration without
migrating its recorded identity fails closed.

The operator supplies the **existing** token and sole verified private chat ID
as `bot-token` and `chat-id` files in the credential directory. Keep that
directory mode 0700 and files mode 0400, readable only by UID 10001. Do not put
them in Git, image layers, command arguments, Compose environment values or chat.
Creating/rotating credentials or adding them to a new host requires an operator
handoff. Source verification can be completed before credentials are supplied:

```sh
docker compose --env-file /private/deployment.env -f telegram.compose.yaml \
  run --rm --no-deps notifier --check-source
# Only after the operator has configured the existing credential:
docker compose --env-file /private/deployment.env -f telegram.compose.yaml \
  up -d --no-deps --pull never notifier
```

Confirm health, exact bot/private recipient, source mount read-only, persisted
cursor and no historical backlog. Restart **only** the notifier to verify cursor
and dedupe persistence. Test delivery with a clearly labeled connection message
to the verified owner chat, never by submitting a broker order. A Telegram API
acceptance confirms submission, not that the owner read the message.

Synthetic tests: `python3 -m unittest discover -s evolver/tests -p 'test_trading_telegram_alerts.py' -v`.

API reference: [Telegram Bot API](https://core.telegram.org/bots/api), including
`getMe`, `getChat`, `sendMessage`, and flood-control `retry_after`.
