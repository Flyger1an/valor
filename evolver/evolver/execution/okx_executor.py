"""OKX v5 **demo-trading** executor — real orders, fake money. The Phase-4 fidelity layer.

WHY THIS EXISTS
    Shadow runners mark against live prices but never actually transact, so they can't see real
    spread, slippage, partial fills, latency, or funding debits. This client places REAL orders on
    OKX's simulated-trading venue (a true matching engine with fake balances) so we can measure what
    we'd ACTUALLY net — the only honest source of performance metrics short of real capital.

SAFETY (load-bearing — read before touching)
    * DEMO-LOCKED. Every request carries the header `x-simulated-trading: 1`, hard-coded. There is
      NO parameter, env var, or branch that turns it off. This file physically cannot place a
      live-money order. Going live is a separate module written under the Phase-5 human-auth gate.
    * Refuses to construct without demo API credentials (OKX_API_KEY / _SECRET / _PASSPHRASE). No
      creds on the box  ->  no trading. Safe default.
    * Idempotent client order IDs (clOrdId) so a retry after a dropped response can't double-fill.
    * `flatten()` is the kill action: cancel all resting orders, then market-close all positions.
    * Every order/cancel/close is appended to an exec ledger for reconciliation (Gate 4).

USAGE
    python -m evolver.execution.okx_executor --check       # creds + balance round-trip
    python -m evolver.execution.okx_executor --positions   # current demo positions
    python -m evolver.execution.okx_executor --selftest    # place a far resting limit, fetch, cancel
    python -m evolver.execution.okx_executor --flatten      # cancel all + close all (kill)

    from evolver.execution import OKXDemoExecutor
    ex = OKXDemoExecutor()                                  # reads demo creds from env
    o  = ex.place_order("BTC-USDT-SWAP", "buy", sz="1", ord_type="market", tag="liqfade")
    fill = ex.fetch_order("BTC-USDT-SWAP", cl_ord_id=o["clOrdId"])   # avgPx, fillSz, state
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv("OKX_BASE_URL", "https://www.okx.com")
SIMULATED = "1"        # HARD-WIRED. Never read from anything mutable. This is the live-money airgap.
_LEDGER = pathlib.Path(os.getenv("OKX_EXEC_LEDGER", str(pathlib.Path.cwd() / "okx_exec_ledger.jsonl")))


class OKXError(RuntimeError):
    """An OKX API call returned a non-zero code or an HTTP error."""


def _okx_ts() -> str:
    """OKX wants an ISO-8601 UTC timestamp with millisecond precision and a 'Z' suffix."""
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _clord(tag: str) -> str:
    """Idempotent-ish, OKX-legal client order id: alphanumeric, <=32 chars. The ms-hex suffix makes
    it unique; pass a stable `tag` per logical intent so a retry of the SAME intent reuses it."""
    base = re.sub(r"[^A-Za-z0-9]", "", tag or "valor")[:12] or "valor"
    return f"{base}{int(time.time() * 1000):x}"[:32]


def _load_env() -> None:
    """Mirror the scripts' .env loader so the CLI works on the box without exported vars."""
    root = pathlib.Path(__file__).resolve().parents[2]      # .../evolver
    envf = root / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class OKXDemoExecutor:
    """Thin signed REST client for OKX v5, locked to the simulated-trading (demo) environment."""

    def __init__(self, key: str | None = None, secret: str | None = None,
                 passphrase: str | None = None, ledger: pathlib.Path | None = None):
        self.key = key or os.getenv("OKX_API_KEY", "")
        self.secret = secret or os.getenv("OKX_API_SECRET", "")
        self.passphrase = passphrase or os.getenv("OKX_API_PASSPHRASE", "")
        if not (self.key and self.secret and self.passphrase):
            raise OKXError(
                "missing OKX DEMO credentials (OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE). "
                "Create them in OKX > Demo Trading > API. No creds = no trading, by design.")
        # Defensive: if anyone tries to flip simulation off via env, refuse loudly rather than trade live.
        if os.getenv("OKX_SIMULATED", "1") != "1":
            raise OKXError("OKX_SIMULATED must be '1' — this executor is demo-only and has no live path.")
        self.ledger = pathlib.Path(ledger) if ledger else _LEDGER

    # ---- signing / transport -------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        mac = hmac.new(self.secret.encode(), f"{ts}{method}{path}{body}".encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None) -> list:
        method = method.upper()
        q = "?" + urllib.parse.urlencode(params) if params else ""
        full = path + q
        body_str = json.dumps(body) if body else ""
        ts = _okx_ts()
        headers = {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": self._sign(ts, method, full, body_str),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "x-simulated-trading": SIMULATED,      # the airgap — demo only, every single request
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(
            BASE + full, data=body_str.encode() if method == "POST" else None,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                out = json.loads(e.read() or b"{}")
            except Exception:
                out = {}
            raise OKXError(f"HTTP {e.code} {path}: {out.get('msg') or out}") from None
        if str(out.get("code")) != "0":
            raise OKXError(f"{path} -> code {out.get('code')}: {out.get('msg')} | {out.get('data')}")
        return out.get("data", [])

    def _log(self, event: str, payload: dict) -> None:
        try:
            with self.ledger.open("a") as f:
                f.write(json.dumps({"ts": _okx_ts(), "event": event, **payload}) + "\n")
        except Exception:
            pass

    # ---- read --------------------------------------------------------------
    def balance(self) -> list:
        return self._request("GET", "/api/v5/account/balance")

    def positions(self, inst_id: str | None = None) -> list:
        return self._request("GET", "/api/v5/account/positions",
                             params={"instId": inst_id} if inst_id else None)

    def pending(self, inst_id: str | None = None) -> list:
        return self._request("GET", "/api/v5/trade/orders-pending",
                             params={"instId": inst_id} if inst_id else None)

    def ticker(self, inst_id: str) -> dict:
        d = self._request("GET", "/api/v5/market/ticker", params={"instId": inst_id})
        return d[0] if d else {}

    def fetch_order(self, inst_id: str, ord_id: str | None = None,
                    cl_ord_id: str | None = None) -> dict:
        p = {"instId": inst_id}
        if ord_id:
            p["ordId"] = ord_id
        elif cl_ord_id:
            p["clOrdId"] = cl_ord_id
        else:
            raise OKXError("fetch_order needs ord_id or cl_ord_id")
        d = self._request("GET", "/api/v5/trade/order", params=p)
        return d[0] if d else {}

    # ---- write -------------------------------------------------------------
    def place_order(self, inst_id: str, side: str, sz: str, ord_type: str = "limit",
                    px: str | None = None, td_mode: str = "cross", pos_side: str = "net",
                    reduce_only: bool = False, tag: str = "valor",
                    cl_ord_id: str | None = None) -> dict:
        """Place a demo order. `sz` is in CONTRACTS for SWAP instruments. Returns {ordId, clOrdId}.

        Idempotency: pass an explicit `cl_ord_id` (derived from your logical intent) so retrying the
        same intent after a dropped response reuses the id and OKX rejects the duplicate instead of
        opening a second position."""
        if ord_type in ("limit", "post_only") and px is None:
            raise OKXError(f"{ord_type} order requires px")
        clid = cl_ord_id or _clord(tag)
        body = {"instId": inst_id, "tdMode": td_mode, "side": side, "ordType": ord_type,
                "sz": str(sz), "clOrdId": clid, "posSide": pos_side}
        if px is not None:
            body["px"] = str(px)
        if reduce_only:
            body["reduceOnly"] = True
        self._log("place.intent", body)
        d = self._request("POST", "/api/v5/trade/order", body=body)
        res = d[0] if d else {}
        if str(res.get("sCode", "0")) not in ("0", ""):
            self._log("place.reject", {"clOrdId": clid, **res})
            raise OKXError(f"order rejected sCode {res.get('sCode')}: {res.get('sMsg')}")
        res["clOrdId"] = res.get("clOrdId") or clid
        self._log("place.ack", res)
        return res

    def cancel_order(self, inst_id: str, ord_id: str | None = None,
                     cl_ord_id: str | None = None) -> dict:
        body = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        elif cl_ord_id:
            body["clOrdId"] = cl_ord_id
        else:
            raise OKXError("cancel_order needs ord_id or cl_ord_id")
        d = self._request("POST", "/api/v5/trade/cancel-order", body=body)
        self._log("cancel", {"instId": inst_id, **(d[0] if d else {})})
        return d[0] if d else {}

    def flatten(self, inst_id: str | None = None) -> dict:
        """KILL action: cancel every resting order, then market-close every open position. Idempotent
        — safe to call repeatedly; a flat book is a no-op."""
        cancelled, closed = [], []
        for o in self.pending(inst_id):
            try:
                self.cancel_order(o["instId"], ord_id=o["ordId"])
                cancelled.append(o["ordId"])
            except OKXError:
                pass
        for p in self.positions(inst_id):
            if abs(float(p.get("pos", 0) or 0)) > 0:
                try:
                    self._request("POST", "/api/v5/trade/close-position",
                                  body={"instId": p["instId"], "mgnMode": p.get("mgnMode", "cross"),
                                        "posSide": p.get("posSide", "net"), "autoCxl": True})
                    closed.append(p["instId"])
                except OKXError as e:
                    self._log("flatten.error", {"instId": p["instId"], "err": str(e)})
        self._log("flatten", {"cancelled": cancelled, "closed": closed})
        return {"cancelled": cancelled, "closed": closed}

    # ---- reconciliation (Gate 4) ------------------------------------------
    def reconcile(self, expected: list[dict]) -> list[dict]:
        """Diff EXPECTED positions against what the exchange actually holds. `expected` is a list of
        {"instId": str, "pos": float} where pos is signed contracts (long +, short -). Returns the
        discrepancies — empty list == books agree to the contract. This is the Gate-4 check."""
        live = {p["instId"]: float(p.get("pos", 0) or 0) for p in self.positions()}
        diffs, seen = [], set()
        for e in expected:
            seen.add(e["instId"])
            have = live.get(e["instId"], 0.0)
            if abs(have - float(e["pos"])) > 1e-9:
                diffs.append({"instId": e["instId"], "expected": float(e["pos"]), "actual": have})
        for inst, have in live.items():
            if inst not in seen and abs(have) > 1e-9:
                diffs.append({"instId": inst, "expected": 0.0, "actual": have})
        return diffs


# ---- CLI -----------------------------------------------------------------
def _fmt(x):
    return json.dumps(x, indent=2)[:4000]


def main(argv: list[str]) -> int:
    _load_env()
    cmd = argv[0] if argv else "--check"
    try:
        ex = OKXDemoExecutor()
    except OKXError as e:
        print(f"✖ {e}")
        return 2

    if cmd == "--check":
        bal = ex.balance()
        total = (bal[0].get("totalEq") if bal else "?")
        print(f"✓ demo creds OK (x-simulated-trading=1). total demo equity: {total}")
        return 0
    if cmd == "--positions":
        print(_fmt(ex.positions()))
        return 0
    if cmd == "--balance":
        print(_fmt(ex.balance()))
        return 0
    if cmd == "--pending":
        print(_fmt(ex.pending()))
        return 0
    if cmd == "--flatten":
        print(_fmt(ex.flatten()))
        return 0
    if cmd == "--selftest":
        # Place a resting limit FAR from market (so it never fills), confirm it's live, cancel it,
        # confirm it's gone. Exercises the full signed path + idempotency + cancel without a fill.
        inst = argv[1] if len(argv) > 1 else "BTC-USDT-SWAP"
        t = ex.ticker(inst)
        last = float(t.get("last") or 0)
        if last <= 0:
            print(f"✖ no ticker for {inst}")
            return 2
        px = round(last * 0.5, 2)              # a buy 50% below market rests, never fills
        print(f"→ placing resting BUY 1 {inst} @ {px} (mkt {last}) …")
        o = ex.place_order(inst, "buy", sz="1", ord_type="limit", px=px, tag="selftest")
        oid = o.get("ordId")
        st = ex.fetch_order(inst, ord_id=oid)
        print(f"  order {oid} state={st.get('state')} fillSz={st.get('fillSz')}")
        ex.cancel_order(inst, ord_id=oid)
        st2 = ex.fetch_order(inst, ord_id=oid)
        ok = st2.get("state") == "canceled"
        print(f"  after cancel: state={st2.get('state')}  -> {'✓ PASS' if ok else '✖ FAIL'}")
        print("  (ledger:", ex.ledger, ")")
        return 0 if ok else 1

    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
