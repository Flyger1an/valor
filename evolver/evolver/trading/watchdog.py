"""Outbound-only dead-man heartbeat. No broker, SSH, or model credentials."""
from __future__ import annotations

import re
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never leak the secret check ID to a redirected host.


def health_reasons(snapshot, now):
    reasons = []
    if not snapshot.get("healthy"):
        reasons.append("execution_or_market_stale")
    if snapshot.get("halt"):
        reasons.append("execution_halted")
    if snapshot.get("entry_pause"):
        reasons.append("broker_reconciliation_or_protection_paused")
    if snapshot.get("news", {}).get("required") and snapshot["news"].get("entry_blocked", True):
        reasons.append("news_unavailable_or_stale")
    pipeline = snapshot.get("pipeline", {})
    for key, limit in (("signals_updated_at", 180), ("research_updated_at", 7200)):
        if key in pipeline and not 0 <= now-pipeline[key] <= limit:
            reasons.append(key.replace("_updated_at", "_worker_stale"))
    if snapshot.get("mode") in {"demo", "live"} and not snapshot.get("protection", {}).get("complete"):
        reasons.append("broker_protection_incomplete")
    usage = snapshot.get("model_usage", {})
    if "timestamp" in usage and not 0 <= now-usage["timestamp"] <= 60:
        reasons.append("agent_worker_stale")
    if usage.get("model_connection", {}).get("status") == "blocked":
        reasons.append("model_authentication_blocked")
    return reasons


def ping(url, snapshot, now, send=None):
    if not re.fullmatch(r"https://hc-ping\.com/[a-f0-9-]{36}", url or ""):
        raise ValueError("watchdog requires a dedicated HTTPS Healthchecks UUID endpoint")
    reasons = health_reasons(snapshot, now)
    if reasons and not snapshot.get("halt"):
        # Withhold success during an outage. The independent provider's two-minute
        # deadline handles persistence; a single stale quote must not send an instant alarm.
        return {"healthy": False, "reasons": reasons, "sent": False}
    target = url + ("/fail" if reasons else "")
    # Send a fixed, nonfinancial health message. No account IDs, positions, logs, or keys.
    request = urllib.request.Request(target, data=("unhealthy" if reasons else "healthy").encode(), method="POST")
    opener = send or urllib.request.build_opener(NoRedirect()).open
    with opener(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("heartbeat provider did not acknowledge health")
    return {"healthy": not reasons, "reasons": reasons, "sent": True}
