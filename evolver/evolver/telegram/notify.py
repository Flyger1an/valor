"""Outbound Telegram notifications (stdlib urllib — safe to call from sync loop code)."""
from __future__ import annotations

import json
import os
import urllib.request


def _send(chat_id: str, text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"content-type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _admins() -> list[str]:
    return [x.strip() for x in os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "").split(",") if x.strip()]


def _observers() -> list[str]:
    return [x.strip() for x in os.getenv("TELEGRAM_OBSERVER_CHAT_IDS", "").split(",") if x.strip()]


def alert_admins(text: str) -> None:
    for cid in _admins():
        _send(cid, text)


def broadcast_observers(text: str) -> None:
    for cid in _observers():
        _send(cid, text)


def notify_admin_proposal(thread_id: str, p: dict) -> None:
    alert_admins(
        f"🧪 Optimizer proposal {p.get('version')} (thread {thread_id})\n"
        f"OOS Δsharpe {p.get('oos_delta_sharpe')} | p={p.get('pvalue')} | risk_ok={p.get('risk_ok')}\n"
        f"changes: {p.get('proposals')}\n→ /approve to promote, /reject to discard."
    )
