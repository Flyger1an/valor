#!/usr/bin/env python3
"""Recover the PAPER/DEMO worker halted by the 24/7 session boundary (run as root from its release dir).

Incident: after the recorded session migration (b8cae8cf -> 9788de47) the Oct 7 cash-journal review,
stamped with the pre-migration book identity, no longer matched and accounting halted with
broker_accounting_mismatch. Fix: accounting accepts identities linked to the current one by an
unbroken RECORDED migration chain (same broker only).

Procedure (same shape as the Oct 7 accounting repair):
  1. overlay the fixed package on the running runtime image; module hashes + full trading suite
  2. stop ONLY the worker; consistent backup of book.sqlite
  3. reconcile with a GET-only broker transport; clear the halt only if balance_match == confirmed
  4. repin the four source services to the fixed image, restart, verify
Any failure before the clear re-asserts the halt and restarts the original worker unchanged.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RID = HERE.name
NEW = "9788de47539c4e781baaba1859e6e4ea957d5bab36361b1822bc64ec9b88d93c"
BOOK = Path("/var/lib/docker/volumes/valor-demo_state/_data/book.sqlite")
TRADING = Path("/opt/valor-study/infra/trading")
COMPOSE = TRADING/"compose.yaml"
CMD = ["docker", "compose", "-p", "valor-demo", "--env-file", str(TRADING/"deployment.env"), "-f", str(COMPOSE)]
SOURCE = ["valor-demo-feed-1", "valor-demo-worker-1", "valor-demo-agents-1", "valor-demo-research-1"]
LOG = (HERE/"recovery.log").open("a")


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def run(args, timeout=600, **kw):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
    LOG.write("$ " + " ".join(args[:8]) + "\n" + r.stdout[-4000:] + r.stderr[-4000:] + "\n")
    if r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(args[:6])} ... {r.stderr.strip()[-800:]}")
    return r.stdout


def tag_of(name):
    return run(["docker", "inspect", "--format", "{{.Config.Image}}", name]).strip()


RECOVER = r'''
import json,os,sys,time
from evolver.trading.alpaca import AlpacaBroker,AlpacaHTTP,BrokerError
from evolver.trading.contracts import Policy
from evolver.trading.ledger import Ledger
from dataclasses import replace
NEW=sys.argv[1]
policy=Policy.from_dict(json.load(open('/config/policy.json')))
assert policy.mode=='demo' and policy.fingerprint==NEW
class ReadOnlyHTTP(AlpacaHTTP):
 def request(self,method,path,body=None,params=None,**kwargs):
  assert method=='GET' and body is None,'recovery transport prohibits broker mutations'
  return super().request(method,path,params=params,**kwargs)
http=ReadOnlyHTTP('demo',os.environ['ALPACA_PAPER_KEY'],os.environ['ALPACA_PAPER_SECRET'])
assert http.base=='https://paper-api.alpaca.markets'
try:AlpacaBroker(replace(policy,mode='live'),http,os.environ['ALPACA_PAPER_ACCOUNT_ID'])
except BrokerError:pass
else:raise AssertionError('live guard did not fail closed')
broker=AlpacaBroker(policy,http,os.environ['ALPACA_PAPER_ACCOUNT_ID'])
book=Ledger('/runtime/state/book.sqlite',policy,broker.identity);broker.bind(book)
meta={k:json.loads(v) for k,v in book.db.execute('SELECT key,value FROM meta')}
boundary=book.db.execute("SELECT max(seq) FROM events WHERE event='policy.session_changed'").fetchone()[0]
since=[r[0] for r in book.db.execute('SELECT event FROM events WHERE seq>? ORDER BY seq',(boundary or 0,))]
try:
 assert meta['identity']['policy']==NEW and meta['halt']=='broker_accounting_mismatch'
 assert boundary and since.count('risk.halted')==1 and set(since)<={'risk.halted','news.context'},since
 assert not book.positions() and not book.orders(pending_only=True,include_protection=True)
 supervisor=meta['supervisor']
 assert broker.reconcile(time.time(),protect=False),'reconciliation did not complete'
 acct=book.get('accounting')
 assert acct['balance_match']=='confirmed' and not acct['fees_provisional'] and not acct['fee_reserves'],acct
 assert acct['cash_flow_count']==0 and not acct['order_activity_lag'],acct
 assert book.get('cash')==meta['cash'] and book.get('realized_pnl')==meta['realized_pnl']
 assert not book.get('entry_pause') and book.get('halt')=='broker_accounting_mismatch'
 with book.db:
  book.set('halt','')
  book.set('broker_reconciled_at',time.time())
  book.event(time.time(),'risk.accounting_halt_cleared',{'previous_reason':'broker_accounting_mismatch',
   'cause':'pre-migration identity stamp on the Oct 7 cash-journal review after the recorded session boundary',
   'scope':'owner-approved 24/7 session release recovery','release':os.environ.get('VALOR_RELEASE','')})
 assert broker.reconcile(time.time(),protect=False)
 assert book.get('accounting')['balance_match']=='confirmed' and book.get('supervisor')==supervisor
 print(json.dumps({'cleared':True,'cash':book.get('cash'),'realized_pnl':book.get('realized_pnl'),
  'balance_match':book.get('accounting')['balance_match'],'halt':book.get('halt'),'entry_pause':book.get('entry_pause'),
  'broker_methods':['GET']}))
except Exception:
 book.halt('broker_accounting_mismatch',time.time())
 raise
finally:book.close()
'''


def main():
    assert os.geteuid() == 0, "run as root on the droplet"
    log(f"== Valor session recovery {RID}")
    base = tag_of("valor-demo-worker-1")
    assert all(tag_of(n) == base for n in SOURCE), "source services run different images"
    status = json.loads(run(["curl", "-fsS", "http://127.0.0.1:9001/status"]))
    assert status.get("policy_hash") == NEW and status.get("halt") == "broker_accounting_mismatch", \
        (status.get("policy_hash"), status.get("halt"))
    assert not status.get("positions") and not status.get("pending_orders")
    log(f"   preflight OK: {base}, halt={status['halt']}, cash {status.get('cash')}")

    new = base.split(":")[0] + ":" + RID
    ctx = HERE/"build"
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir()
    shutil.copytree(HERE/"trading", ctx/"trading")
    user = run(["docker", "inspect", "--format", "{{.Config.User}}", base]).strip()
    (ctx/"Dockerfile").write_text("\n".join([f"FROM {base}", "USER root", "RUN rm -rf /app/evolver/trading",
                                             "COPY trading /app/evolver/trading"] + ([f"USER {user}"] if user else [])) + "\n")
    run(["docker", "build", "-q", "-t", new, str(ctx)], timeout=900)
    expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE/"trading").glob("*.py")}
    actual = json.loads(run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", new, "-c",
        "import hashlib,json,pathlib;print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in pathlib.Path('/app/evolver/trading').glob('*.py')}))"]))
    assert actual == expected, "module hashes differ"
    run(["docker", "run", "--rm", "--network", "none", "-v", f"{HERE/'repo'}:/repo:ro", "-w", "/repo/evolver/tests",
         "-e", "PYTHONPATH=/app:/repo/evolver/tests", "-e", "PYTHONDONTWRITEBYTECODE=1", "--entrypoint", "python",
         new, "-m", "unittest", "discover", "-s", "/repo/evolver/tests", "-p", "test_trading*.py"], timeout=900)
    log(f"   {new}: {len(expected)} modules match, full trading suite passed")

    backup = HERE/"backup"
    backup.mkdir(mode=0o700, exist_ok=True)
    shutil.copy2(COMPOSE, backup/"compose.yaml")
    repinned = False
    log("== stopping worker only")
    run(["docker", "stop", "--time", "30", "valor-demo-worker-1"], timeout=120)
    try:
        a, b = sqlite3.connect("file:" + str(BOOK) + "?mode=ro", uri=True), sqlite3.connect(backup/"book.sqlite")
        a.backup(b)
        a.close()
        assert b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        b.execute("PRAGMA journal_mode=DELETE")
        b.close()
        (backup/"book.sqlite").chmod(0o600)
        log("   backup:", backup/"book.sqlite")
        compose = COMPOSE.read_text()
        assert compose.count("image: " + base + "\n") >= 1
        tmp = COMPOSE.with_name("compose.recover.tmp")
        tmp.write_text(compose.replace("image: " + base + "\n", "image: " + new + "\n"))
        os.chmod(tmp, COMPOSE.stat().st_mode & 0o777)
        tmp.replace(COMPOSE)
        repinned = True
        run(CMD + ["config", "--quiet"])
        r = subprocess.run(CMD + ["run", "--rm", "--no-deps", "-T", "--pull", "never", "-e", f"VALOR_RELEASE={RID}",
                                  "--entrypoint", "python", "worker", "-", NEW],
                           input=RECOVER, capture_output=True, text=True, timeout=180)
        LOG.write(r.stdout + r.stderr + "\n")
        if r.returncode:
            raise RuntimeError("reconciliation recovery failed (halt retained): " + r.stderr.strip()[-1200:])
        result = json.loads(r.stdout.strip().splitlines()[-1])
        log("   halt cleared after confirmed GET-only reconciliation:", json.dumps(result))
    except Exception as exc:
        log("!! recovery failed; halt retained, restoring compose and restarting the original worker:", exc)
        if repinned:
            shutil.copy2(backup/"compose.yaml", COMPOSE)
        run(["docker", "start", "valor-demo-worker-1"], timeout=120)
        return 1

    run(CMD + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "feed", "worker", "agents", "research"], timeout=300)
    log("== verifying (up to 2 minutes)")
    checks = {}
    for _ in range(8):
        time.sleep(15)
        try:
            s = json.loads(run(["curl", "-fsS", "http://127.0.0.1:9001/status"]))
            checks = {"healthy": s.get("healthy") is True, "demo": s.get("mode") == "demo",
                      "policy_new": s.get("policy_hash") == NEW, "unhalted": not s.get("halt"),
                      "no_entry_pause": not s.get("entry_pause"), "flat": not s.get("positions"),
                      "reconciled_fresh": 0 <= time.time()-(s.get("broker_reconciled_at") or 0) <= 60,
                      "accounting_confirmed": (s.get("accounting") or {}).get("balance_match") == "confirmed",
                      "images": all(tag_of(n) == new for n in SOURCE)}
            if all(checks.values()):
                break
        except Exception as exc:
            checks = {"error": str(exc)}
    ok = bool(checks) and all(v is True for v in checks.values())
    log(json.dumps(checks, indent=1))
    log("== RESULT:", "ALL CHECKS PASSED" if ok else "CHECKS INCOMPLETE - inspect before changing anything")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
