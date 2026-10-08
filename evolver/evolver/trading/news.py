"""Bounded news evidence, never executable instructions or an order generator."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

from .contracts import encode, utc_timestamp

FED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
SOURCES = ("alpaca_crypto", "federal_reserve")
MAX_FETCH_AGE = 600
LOOKBACK = 72 * 3600
MAX_ITEMS = 10
RELEVANT = re.compile(r"\b(bitcoin|btc|ethereum|ether|eth|buterin|crypto(?:currency)?|digital assets|stablecoins?|federal reserve|interest rates?|inflation|sec|regulation|hacks?|exploits?|exchanges?|liquidations?|alpaca|coinbase|binance|kraken)\b", re.I)


def clean(value, limit):
    if not isinstance(value, str):
        raise ValueError("invalid news text")
    text = html.unescape(re.sub(r"<[^>]*>", " ", value))
    return " ".join(text.split())[:limit]


def item(source, identity, headline, url, published, updated, symbols, now):
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or not 0 < published <= updated <= now):
        raise ValueError("invalid news provenance or future timestamp")
    return {"id": source + ":" + str(identity)[:100], "source": source,
            "headline": clean(headline, 220), "url": url[:400], "published_at": published,
            "updated_at": updated, "instruments": sorted(set(symbols))}


def crypto_news(http, now):
    # Omitting end respects the account's entitlement; do not assert zero delivery delay.
    start = dt.datetime.fromtimestamp(now-LOOKBACK, dt.timezone.utc).isoformat()
    params = {"symbols": "BTCUSD,ETHUSD", "start": start, "sort": "desc", "limit": 50,
              "include_content": "false"}
    rows, tokens, truncated = [], set(), False
    for page in range(3):
        value = http.request("GET", "/v1beta1/news", data=True, params=params)
        if not isinstance(value, dict) or not isinstance(value.get("news"), list):
            raise ValueError("invalid news provider response")
        for raw in value["news"]:
            symbols = [s for s, aliases in (("BTC-USD", {"BTC", "BTCUSD", "BTC/USD"}),
                                           ("ETH-USD", {"ETH", "ETHUSD", "ETH/USD"}))
                       if aliases.intersection(raw.get("symbols", []))]
            # Provider symbol tags can include BTC/ETH on unrelated altcoin promotions.
            if not symbols or not RELEVANT.search(clean(raw["headline"], 1000)):
                continue
            rows.append(item("alpaca_crypto", raw["id"], raw["headline"], raw["url"],
                             utc_timestamp(raw["created_at"]), utc_timestamp(raw["updated_at"]), symbols, now))
        token = value.get("next_page_token")
        if not token:
            break
        if not isinstance(token, str) or token in tokens:
            raise ValueError("news pagination did not advance")
        tokens.add(token)
        params = {**params, "page_token": token}
        truncated = page == 2
    return rows, truncated


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fed_news(now, fetch=None):
    request = urllib.request.Request(FED_URL, headers={"User-Agent": "Valor-paper-study/1.0", "Accept": "application/rss+xml, application/xml, text/xml"})
    with (fetch or urllib.request.build_opener(NoRedirect()).open)(request, timeout=8) as response:
        raw = response.read(250_001)
    if len(raw) > 250_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe or oversized news XML")
    rows = []
    for entry in ET.fromstring(raw).findall(".//item")[:100]:
        url = entry.findtext("link", "")
        published = parsedate_to_datetime(entry.findtext("pubDate", "")).timestamp()
        if urllib.parse.urlparse(url).hostname != "www.federalreserve.gov":
            raise ValueError("unexpected central-bank source")
        rows.append(item("federal_reserve", hashlib.sha256(url.encode()).hexdigest()[:20],
                         entry.findtext("title", ""), url, published, published, ["BTC-USD", "ETH-USD"], now))
    return rows, False


def evidence_hash(items):
    return hashlib.sha256(encode(items).encode()).hexdigest()


def assess(value, now):
    """Fetch freshness is separate from headline age: a quiet feed is not a dead feed."""
    value = value or {}
    sources = value.get("sources", {})
    items = value.get("items", [])
    valid = value.get("schema_version") == 1 and isinstance(items, list) and len(items) <= MAX_ITEMS
    fresh = {s: sources.get(s, {}).get("ok") is True
             and 0 <= now-sources[s].get("checked_at", 0) <= MAX_FETCH_AGE for s in SOURCES}
    valid = valid and value.get("evidence_hash") == evidence_hash(items)
    valid = valid and all(0 < i["published_at"] <= i["updated_at"] <= now
                          and now-i["updated_at"] <= LOOKBACK for i in items)
    status = ("current" if all(fresh.values()) else "degraded") if valid and fresh["alpaca_crypto"] else "unavailable_or_stale"
    return {"required": True, "status": status, "entry_blocked": status == "unavailable_or_stale",
            "evidence_hash": value.get("evidence_hash") if valid else evidence_hash([]),
            "items": items if valid else [], "sources": sources,
            "fetched_at": value.get("timestamp", 0), "lookback_hours": 72,
            "coverage": "Latest bounded crypto headlines via Alpaca/Benzinga and official Fed monetary-policy releases; not exhaustive, not an economic calendar",
            "delivery_latency": "Provider delivery delay is not verified; polling is once per minute",
            "degraded_rule": "Missing Fed coverage must be acknowledged as elevated uncertainty; crypto news must be current for entries",
            "truncated": value.get("truncated", False)}


class NewsFeed:
    def __init__(self, http, target):
        self.http, self.target = http, Path(target)
        self.target.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.target / "evidence.sqlite")
        self.db.execute("CREATE TABLE IF NOT EXISTS bundles(hash TEXT PRIMARY KEY, first_seen REAL, payload TEXT)")
        self.db.commit()

    def tick(self, now):
        from .engine import write_snapshot
        rows, sources, truncated = [], {}, False
        for name, fetch in (("alpaca_crypto", lambda: crypto_news(self.http, now)),
                            ("federal_reserve", lambda: fed_news(now))):
            try:
                found, limited = fetch()
                rows.extend(found)
                truncated |= limited
                sources[name] = {"ok": True, "checked_at": now, "items_returned": len(found)}
            except Exception as exc:
                sources[name] = {"ok": False, "checked_at": now, "error_type": type(exc).__name__}
        # Preserve revisions using the latest updated timestamp, never count one wire story twice.
        unique = {}
        for row in sorted(rows, key=lambda r: (r["updated_at"], r["id"])):
            if now-row["updated_at"] <= LOOKBACK:
                unique[row["id"]] = row
        latest = sorted(unique.values(), key=lambda r: (r["updated_at"], r["id"]), reverse=True)
        # Reserve room for each source so a busy crypto feed cannot hide a Fed release.
        chosen = {r["id"]: r for name in SOURCES for r in [r for r in latest if r["source"] == name][:2]}
        for row in latest:
            if len(chosen) >= MAX_ITEMS:
                break
            chosen[row["id"]] = row
        selected = sorted(chosen.values(), key=lambda r: (r["updated_at"], r["id"]), reverse=True)
        snapshot = {"schema_version": 1, "timestamp": now, "sources": sources, "items": selected,
                    "evidence_hash": evidence_hash(selected), "truncated": truncated or len(latest) > len(selected)}
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO bundles VALUES (?,?,?)",
                            (snapshot["evidence_hash"], now, encode(snapshot)))
        write_snapshot(self.target / "snapshot.json", snapshot)
        return assess(snapshot, now)
