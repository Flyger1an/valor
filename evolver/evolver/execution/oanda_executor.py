"""OANDA v20 **practice** executor — the FX twin of okx_executor.py. Real orders, fake money.

WHY: the FX shadow marks against live rates but never transacts, so it can't see real fills/spread/
slippage. This places REAL orders on OANDA's PRACTICE (demo) account so we can measure true fills —
the only honest FX performance short of real capital.

SAFETY (load-bearing):
  * PRACTICE-LOCKED. The base URL is hard-wired to api-fxpractice.oanda.com; it REFUSES to construct
    if OANDA_ENV looks live. There is no live-money code path here — going live is a separate module.
  * Refuses without OANDA_API_KEY + OANDA_ACCOUNT_ID (no creds -> no trading). Safe default.
  * flatten() is the kill action: close every open position. Idempotent (a flat book is a no-op).
  * Every order/cancel/close is appended to an exec ledger.
  * IDEMPOTENCY CAVEAT: unlike OKX's clOrdId, OANDA does not reject duplicate orders by client id —
    clientExtensions.id only TAGS an order. A safe retry must fetch by that id / reconcile FIRST.

    python -m evolver.execution.oanda_executor --check      # creds + account summary
    python -m evolver.execution.oanda_executor --positions
    python -m evolver.execution.oanda_executor --flatten      # close all (kill)
    python -m evolver.execution.oanda_executor --selftest     # tiny far limit -> fetch -> cancel
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

_BASE = "https://api-fxpractice.oanda.com"     # HARD-WIRED practice. No live path.
_LEDGER = pathlib.Path(os.getenv("OANDA_EXEC_LEDGER", str(pathlib.Path.cwd() / "oanda_exec_ledger.jsonl")))


class OANDAError(RuntimeError):
    """An OANDA API call returned an HTTP error."""


def _clid(tag: str) -> str:
    base = "".join(ch for ch in (tag or "valor") if ch.isalnum())[:12] or "valor"
    return f"{base}{int(time.time() * 1000):x}"


def _load_env() -> None:
    envf = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class OANDADemoExecutor:
    """Thin REST client for OANDA v20, locked to the practice (demo) environment."""

    def __init__(self, key: str | None = None, account: str | None = None,
                 ledger: pathlib.Path | None = None):
        if os.getenv("OANDA_ENV", "practice") == "live":
            raise OANDAError("OANDA_ENV=live — this executor is PRACTICE-only and has no live path.")
        self.key = key or os.getenv("OANDA_API_KEY", "")
        self.account = account or os.getenv("OANDA_ACCOUNT_ID", "")
        if not (self.key and self.account):
            raise OANDAError("missing OANDA_API_KEY / OANDA_ACCOUNT_ID — create a PRACTICE account + "
                             "token at oanda.com (Demo). No creds = no trading, by design.")
        self.ledger = pathlib.Path(ledger) if ledger else _LEDGER

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            _BASE + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read() or b"{}")
            except Exception:
                err = {}
            raise OANDAError(f"HTTP {e.code} {path}: {err.get('errorMessage') or err}") from None

    def _log(self, event: str, payload: dict) -> None:
        try:
            with self.ledger.open("a") as f:
                f.write(json.dumps({"ts": int(time.time() * 1000), "event": event, **payload}) + "\n")
        except Exception:
            pass

    # ---- read ----
    def summary(self) -> dict:
        return self._req("GET", f"/v3/accounts/{self.account}/summary").get("account", {})

    def pricing(self, instruments: list[str]) -> list:
        q = ",".join(instruments)
        return self._req("GET", f"/v3/accounts/{self.account}/pricing?instruments={q}").get("prices", [])

    def positions(self) -> list:
        return self._req("GET", f"/v3/accounts/{self.account}/openPositions").get("positions", [])

    def open_trades(self) -> list:
        return self._req("GET", f"/v3/accounts/{self.account}/openTrades").get("trades", [])

    def pending_orders(self) -> list:
        return self._req("GET", f"/v3/accounts/{self.account}/pendingOrders").get("orders", [])

    # ---- write ----
    def place_order(self, instrument: str, units: float, order_type: str = "MARKET",
                    price: float | None = None, tif: str | None = None, tag: str = "valor",
                    client_id: str | None = None) -> dict:
        """units > 0 long, < 0 short. MARKET (immediate) or LIMIT/STOP (needs price). Returns the
        transaction. NOTE: pass a STABLE client_id to track a retry — OANDA won't reject the dup itself."""
        if order_type != "MARKET" and price is None:
            raise OANDAError(f"{order_type} order needs a price")
        order = {"type": order_type, "instrument": instrument, "units": str(int(units)),
                 "timeInForce": tif or ("FOK" if order_type == "MARKET" else "GTC"),
                 "positionFill": "DEFAULT", "clientExtensions": {"id": client_id or _clid(tag)}}
        if price is not None:
            order["price"] = f"{price:.5f}"
        self._log("place.intent", {"order": order})
        d = self._req("POST", f"/v3/accounts/{self.account}/orders", {"order": order})
        if "orderRejectTransaction" in d:
            self._log("place.reject", d["orderRejectTransaction"])
            raise OANDAError(f"order rejected: {d['orderRejectTransaction'].get('reason')}")
        self._log("place.ack", {"id": (d.get("orderCreateTransaction") or {}).get("id"),
                                "fill": bool(d.get("orderFillTransaction"))})
        return d

    def cancel_order(self, order_id: str) -> dict:
        d = self._req("PUT", f"/v3/accounts/{self.account}/orders/{order_id}/cancel")
        self._log("cancel", {"id": order_id})
        return d

    def close_position(self, instrument: str) -> dict:
        """Close any long AND short for an instrument (market). Idempotent — no-op if already flat."""
        out = {}
        for side in ("longUnits", "shortUnits"):
            try:
                out[side] = self._req("PUT", f"/v3/accounts/{self.account}/positions/{instrument}/close",
                                      {side: "ALL"})
            except OANDAError:
                pass        # no open units on that side
        self._log("close", {"instrument": instrument})
        return out

    def flatten(self) -> list[str]:
        """KILL: close every open position. Idempotent."""
        closed = []
        for p in self.positions():
            inst = p.get("instrument")
            if inst:
                self.close_position(inst)
                closed.append(inst)
        self._log("flatten", {"closed": closed})
        return closed

    # ---- reconciliation ----
    def reconcile(self, expected: list[dict]) -> list[dict]:
        """Diff EXPECTED vs actual net position units. expected: [{instrument, units}] (signed). Empty
        list == books agree. Mirrors the OKX executor's Gate-4 check."""
        live = {}
        for p in self.positions():
            net = float((p.get("long") or {}).get("units", 0)) + float((p.get("short") or {}).get("units", 0))
            live[p["instrument"]] = net
        diffs, seen = [], set()
        for e in expected:
            seen.add(e["instrument"])
            have = live.get(e["instrument"], 0.0)
            if abs(have - float(e["units"])) > 1e-6:
                diffs.append({"instrument": e["instrument"], "expected": float(e["units"]), "actual": have})
        for inst, have in live.items():
            if inst not in seen and abs(have) > 1e-6:
                diffs.append({"instrument": inst, "expected": 0.0, "actual": have})
        return diffs


def _fmt(x):
    return json.dumps(x, indent=2)[:4000]


def main(argv: list[str]) -> int:
    _load_env()
    cmd = argv[0] if argv else "--check"
    try:
        ex = OANDADemoExecutor()
    except OANDAError as e:
        print(f"✖ {e}")
        return 2
    if cmd == "--check":
        s = ex.summary()
        print(f"✓ practice creds OK. account {ex.account} NAV {s.get('NAV')} {s.get('currency')} | "
              f"open positions {s.get('openPositionCount')}")
        return 0
    if cmd == "--positions":
        print(_fmt(ex.positions()))
        return 0
    if cmd == "--flatten":
        print("closed:", ex.flatten())
        return 0
    if cmd == "--selftest":
        inst = argv[1] if len(argv) > 1 else "EUR_USD"
        px = ex.pricing([inst])
        bid = float(px[0]["bids"][0]["price"]) if px else 0.0
        if bid <= 0:
            print(f"✖ no pricing for {inst}")
            return 2
        far = round(bid * 0.5, 5)                        # a buy 50% below market rests, never fills
        print(f"→ placing resting LIMIT buy 1 {inst} @ {far} (bid {bid}) …")
        d = ex.place_order(inst, 1, order_type="LIMIT", price=far, tag="selftest")
        oid = (d.get("orderCreateTransaction") or {}).get("id")
        pend = {o["id"] for o in ex.pending_orders()}
        print(f"  order {oid} pending={oid in pend}")
        ex.cancel_order(oid)
        gone = oid not in {o["id"] for o in ex.pending_orders()}
        print(f"  after cancel: gone={gone}  -> {'✓ PASS' if gone else '✖ FAIL'}  (ledger: {ex.ledger})")
        return 0 if gone else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
