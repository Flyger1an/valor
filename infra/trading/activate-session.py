#!/usr/bin/env python3
"""24/7 PAPER/DEMO session release for the existing Valor droplet. Run as root from its release directory.

  python3 activate-session.py            rehearsal only: preflight, staged images, both full test suites,
                                         and the complete migration against COPIES of the databases.
                                         No production file, volume, container or config is changed.
  python3 activate-session.py --apply    rehearsal, then the real migration with every writer stopped.

What changes: entry hours 13-20 UTC Mon-Fri -> all hours, all days; entry attempts 6/day -> 24/day;
model calls 60/day -> 200/day. Nothing else in the policy can change (session.validate_session_expansion).
The live book and the three virtual books move to the SAME policy (one shared fingerprint), so the
dashboard, notifier and mirror stay in sync. Cash, orders, fills and prior evidence are preserved. Any failure before services restart restores every database and config automatically.
"""
import argparse
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
OLD = "b8cae8cf555abb5a4b2b4bf59b90d40ca02aa4437bfa4fca626e9021aff93a68"
NEW = "9788de47539c4e781baaba1859e6e4ea957d5bab36361b1822bc64ec9b88d93c"
VOL = Path("/var/lib/docker/volumes")
DBS = {"book.sqlite": VOL/"valor-demo_state/_data/book.sqlite",
       "experiment.sqlite": VOL/"valor-experiment_state/_data/experiment.sqlite",
       "alerts.sqlite": VOL/"valor-telegram_delivery_state/_data/alerts.sqlite"}
MARKET = VOL/"valor-demo_market/_data"
EXP_SNAPSHOT = VOL/"valor-experiment_state/_data/snapshot.json"
CONFIGS = {"source-policy.json": Path("/opt/valor-study/infra/trading/policy.demo.json"),
           "source-compose.yaml": Path("/opt/valor-study/infra/trading/compose.yaml"),
           "notifications.json": Path("/opt/valor-telegram/config/notifications.json"),
           "access.json": Path("/opt/valor-dashboard/config/access.json"),
           "experiment-deployment.env": Path("/opt/valor-experiment/deployment.env")}
SOURCE = ["valor-demo-feed-1", "valor-demo-worker-1", "valor-demo-agents-1", "valor-demo-research-1"]
WRITERS = SOURCE + ["valor-experiment-ledgers-1", "valor-telegram-notifier-1"]
UNRELATED = ["valor-demo-news-1", "valor-demo-monitor-1", "valor-demo-watchdog-1", "valor-dashboard-web-1"]
LOG = None


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    if LOG:
        LOG.write(line + "\n")
        LOG.flush()


def run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if LOG:
        LOG.write("$ " + " ".join(args) + "\n" + r.stdout[-4000:] + r.stderr[-4000:] + "\n")
    if r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(args[:6])} ... {r.stderr.strip()[-600:]}")
    return r.stdout


def inspect(name):
    fmt = ('{"id":{{json .Id}},"image":{{json .Image}},"tag":{{json .Config.Image}},"user":{{json .Config.User}},'
           '"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"restarts":{{.RestartCount}},'
           '"labels":{{json .Config.Labels}}}')
    return json.loads(run(["docker", "inspect", "--format", fmt, name]))


def ro(path):
    return sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=5)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def in_image(image, code, mounts=(), user=None):
    args = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python"]
    if user:
        args += ["--user", user]
    for src, dst, mode in mounts:
        args += ["-v", f"{src}:{dst}:{mode}"]
    return run(args + [image, "-c", code], timeout=600)


def fingerprint(image, path):
    return in_image(image, "import json;from evolver.trading.contracts import Policy;"
                    "print(Policy.from_dict(json.load(open('/p.json'))).fingerprint)",
                    [(path, "/p.json", "ro")]).strip()


def preflight(require_flat=True):
    state = {name: inspect(name) for name in WRITERS + UNRELATED}
    stopped = [n for n, s in state.items() if not s["running"]]
    assert not stopped, f"containers not running: {stopped}"
    runtime = {state[n]["tag"] for n in SOURCE}
    assert len(runtime) == 1, f"source services run different images: {runtime}"
    runtime = runtime.pop()
    experiment = state["valor-experiment-ledgers-1"]["tag"]
    env = CONFIGS["experiment-deployment.env"].read_text().splitlines()
    assert sum(l == "VALOR_EXPERIMENT_IMAGE=" + experiment for l in env) == 1, "experiment image pin not found"
    compose = CONFIGS["source-compose.yaml"].read_text()
    assert compose.count("image: " + runtime) >= 1, "source compose does not pin the running runtime image"
    for name in ("source-policy.json",):
        assert fingerprint(runtime, CONFIGS[name]) == OLD, "host source policy is not the expected weekday policy"
    assert json.loads(CONFIGS["notifications.json"].read_text())["policy_hash"] == OLD, "notifier pin unexpected"
    assert json.loads(CONFIGS["access.json"].read_text())["source_policy_hash"] == OLD, "dashboard pin unexpected"
    quotes = json.loads((MARKET/"quotes.json").read_text())
    assert quotes["policy_hash"] == OLD and 0 <= time.time()-quotes["timestamp"] <= 60, "feed quotes not current"
    db = ro(DBS["book.sqlite"])
    meta = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM meta")}
    positions = db.execute("SELECT count(*) FROM positions").fetchone()[0]
    pending = db.execute("SELECT count(*) FROM orders WHERE status NOT IN ('filled','cancelled','rejected')").fetchone()[0]
    db.close()
    assert meta["identity"]["policy"] == OLD, "source ledger policy unexpected"
    assert not meta.get("halt"), "source ledger is halted; audit first"
    if require_flat:
        assert positions == 0 and pending == 0, f"source not flat (positions={positions}, pending={pending})"
    db = ro(DBS["experiment.sqlite"])
    exp_state = json.loads(db.execute("SELECT payload FROM experiment_meta WHERE key='state'").fetchone()[0])
    db.close()
    assert not exp_state["halt"], "virtual experiment is halted; audit first"
    assert exp_state.get("universe", {}).get("policy_hash") == OLD, "virtual experiment policy unexpected"
    assert not exp_state.get("session_history"), "session boundary already applied to the experiment"
    free = shutil.disk_usage("/").free
    assert free > 2_000_000_000, "less than 2 GB free"
    return state, runtime, experiment, {"positions": positions, "pending": pending, "cash": meta.get("cash")}


def build(base, tag, user, policy=None):
    ctx = HERE/("build-" + tag.split(":")[0].split("-")[-1])
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir()
    shutil.copytree(HERE/"trading", ctx/"trading")
    lines = [f"FROM {base}", "USER root", "RUN rm -rf /app/evolver/trading",
             "COPY trading /app/evolver/trading", "RUN find /app/evolver/trading -name '__pycache__' -prune -exec rm -rf {} +"]
    if policy:
        shutil.copy2(policy, ctx/"policy.json")
        lines.append("COPY policy.json /config/experiment-policy.json")
    if user:
        lines.append(f"USER {user}")
    (ctx/"Dockerfile").write_text("\n".join(lines) + "\n")
    run(["docker", "build", "-q", "-t", tag, str(ctx)], timeout=900)
    expected = {p.name: sha(p) for p in (HERE/"trading").glob("*.py")}
    actual = json.loads(in_image(tag, "import hashlib,json,pathlib;print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() "
                                      "for p in pathlib.Path('/app/evolver/trading').glob('*.py')}))"))
    assert actual == expected, f"module hashes differ in {tag}"
    out = run(["docker", "run", "--rm", "--network", "none", "-v", f"{HERE/'repo'}:/repo:ro", "-w", "/repo/evolver/tests",
               "-e", "PYTHONPATH=/app:/repo/evolver/tests", "-e", "PYTHONDONTWRITEBYTECODE=1", "--entrypoint", "python",
               tag, "-m", "unittest", "discover", "-s", "/repo/evolver/tests", "-p", "test_trading*.py"], timeout=900)
    log(f"   {tag}: {len(expected)} modules match, full trading suite passed")
    return out


MIGRATION = r'''
import copy,json,sqlite3,time,sys
from dataclasses import asdict
from pathlib import Path
from evolver.trading.contracts import Policy
from evolver.trading.experiment import Experiment,digest
from evolver.trading.engine import write_snapshot
from evolver.trading.session import migrate_ledger,record_feed_transition
from evolver.trading.telegram_alerts import migrate_config
from evolver.trading.quote_admission import violations
OLD,NEW=sys.argv[1],sys.argv[2]
def read(n):return json.loads(Path('/config/'+n).read_text())
def rows(path,table):
 d=sqlite3.connect(path);v=d.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall();d.close();return v
old,new=Policy.from_dict(read('old-policy.json')),Policy.from_dict(read('new-policy.json'))
assert old.fingerprint==OLD and new.fingerprint==NEW
source,experimental,alerts='/source/book.sqlite','/experiment/experiment.sqlite','/alerts/alerts.sqlite'
d=sqlite3.connect(source);tables=[r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")];d.close()
before={t:rows(source,t) for t in tables}
exp=Experiment(experimental,old);state=copy.deepcopy(exp.state());identity=copy.deepcopy(exp.identity)
prefix=rows(experimental,'experiment_events');exp.close()
deliveries={t:rows(alerts,t) for t in ['meta','outbox','historical_orders']}
now=time.time()
boundary=migrate_ledger(source,old,new,now)
exp=Experiment(experimental,old)
event={'type':'session_policy_update','id':'session:'+new.fingerprint,'observed_at':now,
       'from_policy':old.fingerprint,'to_policy':new.fingerprint,'new_policy':asdict(new)}
exp._activate_session(copy.deepcopy(exp.state()),event,now)
report=exp.apply(event)
assert exp.identity==identity
after=exp.state()
for name,book in state['books'].items():
 for k in ['cash','positions','pending','fills','lots','seen','attempts']:assert after['books'][name][k]==book[k],(name,k)
assert after['evidence']['blocks']==state['evidence']['blocks']
assert rows(experimental,'experiment_events')[:-1]==prefix
assert after['universe']['policy_hash']==NEW
exp.close();exp=Experiment(experimental,new);replay=exp.verify_replay();report=exp.report()
assert report['policy_hash']==NEW
write_snapshot(Path('/experiment/snapshot.json'),exp.report());exp.close()
migrate_config(alerts,read('old-notifications.json'),read('new-notifications.json'),now)
record_feed_transition('/market',old,new,now)
quotes=json.loads(Path('/market/quotes.json').read_text())
probe={**{k:v for k,v in quotes.items() if k!='quote_watermarks'},'policy_hash':NEW,'timestamp':quotes['timestamp']+1}
assert not violations(probe,quotes,new.allowed_instruments,[(OLD,NEW)]),'feed would reject the declared transition'
after_rows={t:rows(source,t) for t in tables}
for t in set(tables)-{'meta','events'}:assert before[t]==after_rows[t],t
assert before['events']==after_rows['events'][:-1]
for k,v in before['meta']:
 if k not in ('identity','session_history'):assert dict(after_rows['meta'])[k]==v,k
for t in ['outbox','historical_orders']:assert rows(alerts,t)==deliveries[t],t
for k,v in deliveries['meta']:
 if k!='config_identity':assert dict(rows(alerts,'meta'))[k]==v,k
print(json.dumps({'boundary_at':now,'replay':replay,'source_events_preserved':len(before['events']),
 'experiment_events_preserved':len(prefix),'source_prefix_sha256':digest(before['events']),
 'experiment_prefix_sha256':digest(prefix),'identity_hash':digest(identity),
 'added_hours':boundary['added_hours_utc'],'added_weekdays':boundary['added_weekdays_utc'],
 'entry_attempts_per_day':boundary['entry_attempts_per_day'],'model_calls_per_day':boundary['model_calls_per_day'],
 'source_cash':json.loads(dict(before['meta'])['cash']),
 'virtual_books':{b['name']:b['cash'] for b in report['books']}}))
'''


def migrate(runtime_image, roots, private):
    """roots: dict with source/experiment/alerts/market host directories (copies or real volumes)."""
    args = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "512m", "--tmpfs", "/tmp:rw,nosuid,size=32m",
            "--user", "10001:10001", "--entrypoint", "python"]
    for key in ("source", "experiment", "alerts", "market"):
        args += ["-v", f"{roots[key]}:/{key}:rw"]
    args += ["-v", f"{private}:/config:ro", runtime_image, "-", OLD, NEW]
    r = subprocess.run(args, input=MIGRATION, capture_output=True, text=True, timeout=300)
    if LOG:
        LOG.write(r.stdout + r.stderr + "\n")
    if r.returncode:
        raise RuntimeError("migration failed: " + r.stderr.strip()[-1500:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def write_private(directory, notifications_old):
    directory.mkdir(mode=0o750, exist_ok=True)
    os.chown(directory, 0, 10001)
    new_cfg = {**notifications_old, "policy_hash": NEW,
               "acceptance_identity_policy_hash": notifications_old.get("acceptance_identity_policy_hash", OLD)}
    files = {"old-policy.json": json.loads(CONFIGS["source-policy.json"].read_text()),
             "new-policy.json": json.loads((HERE/"repo/infra/trading/policy.demo.json").read_text()),
             "old-notifications.json": notifications_old, "new-notifications.json": new_cfg}
    for name, value in files.items():
        p = directory/name
        p.write_text(json.dumps(value) + "\n")
        p.chmod(0o640)
        os.chown(p, 0, 10001)
    return new_cfg


def backup_db(src, dst):
    a, b = ro(src), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    assert b.execute("PRAGMA integrity_check").fetchone()[0] == "ok", f"integrity check failed for {src}"
    b.execute("PRAGMA journal_mode=DELETE")
    b.close()


def rehearse(runtime_image, notifications_old):
    work = HERE/"rehearsal"
    if work.exists():
        shutil.rmtree(work)
    roots = {k: work/k for k in ("source", "experiment", "alerts", "market")}
    for path in roots.values():
        path.mkdir(parents=True)
    backup_db(DBS["book.sqlite"], roots["source"]/"book.sqlite")
    backup_db(DBS["experiment.sqlite"], roots["experiment"]/"experiment.sqlite")
    backup_db(DBS["alerts.sqlite"], roots["alerts"]/"alerts.sqlite")
    shutil.copy2(MARKET/"quotes.json", roots["market"]/"quotes.json")
    run(["chown", "-R", "10001:10001", str(work)])
    write_private(work/"config", notifications_old)
    result = migrate(runtime_image, roots, work/"config")
    log("   rehearsal on copies: OK", json.dumps({k: result[k] for k in ("replay", "source_events_preserved",
        "experiment_events_preserved", "entry_attempts_per_day", "model_calls_per_day", "virtual_books")}))
    shutil.rmtree(work)
    return result


def replace_file(path, data):
    prior = path.stat()
    temp = path.with_name(path.name + ".session-tmp")
    temp.write_bytes(data)
    os.chmod(temp, prior.st_mode & 0o777)
    os.chown(temp, prior.st_uid, prior.st_gid)
    temp.replace(path)


def compose_cmd(state, name):
    labels = state[name]["labels"]
    cmd = ["docker", "compose", "-p", labels["com.docker.compose.project"]]
    if labels.get("com.docker.compose.project.environment_file"):
        cmd += ["--env-file", labels["com.docker.compose.project.environment_file"]]
    for f in labels["com.docker.compose.project.config_files"].split(","):
        cmd += ["-f", f]
    return cmd


def main():
    global LOG
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    assert os.geteuid() == 0, "run as root on the droplet"
    LOG = (HERE/("activation.log" if args.apply else "rehearsal.log")).open("a")
    os.chmod(LOG.name, 0o600)
    log(f"== Valor 24/7 session release {RID} ({'APPLY' if args.apply else 'rehearsal only'})")
    state, runtime, experiment, book = preflight(require_flat=args.apply)
    log(f"   preflight OK: runtime {runtime}, experiment {experiment}, source cash {book['cash']}, "
        f"positions {book['positions']}, pending {book['pending']}")
    new_runtime, new_experiment = f"valor-runtime:{RID}", f"valor-experiment:{RID}"
    assert fingerprint(runtime, HERE/"repo/infra/trading/policy.demo.weekday.json") == OLD
    assert fingerprint(runtime, HERE/"repo/infra/trading/policy.demo.json") == NEW
    log("== staged images (overlay on the running images) + full test suites")
    build(runtime, new_runtime, state["valor-demo-worker-1"]["user"])
    build(experiment, new_experiment, state["valor-experiment-ledgers-1"]["user"], HERE/"repo/infra/trading/policy.demo.json")
    in_image(new_experiment, "import json;from evolver.trading.contracts import Policy;"
             f"assert Policy.from_dict(json.load(open('/config/experiment-policy.json'))).fingerprint=='{NEW}';print('ok')")
    log("   experiment image carries the shared 24/7 policy")
    notifications_old = json.loads(CONFIGS["notifications.json"].read_text())
    log("== rehearsal: full migration against database copies")
    rehearse(new_runtime, notifications_old)
    if not args.apply:
        log("== rehearsal complete. Nothing in production changed. Re-run with --apply to activate.")
        return 0

    backup = HERE/"backup"
    backup.mkdir(mode=0o700)
    transitions_existed = (MARKET/"policy-transitions.json").exists()
    log("== stopping writers:", " ".join(WRITERS))
    run(["docker", "stop", "--time", "30", *WRITERS], timeout=300)
    restarted = False
    try:
        preflight_state = {n: inspect(n) for n in WRITERS}
        assert all(not s["running"] for s in preflight_state.values()), "a writer is still running"
        db = ro(DBS["book.sqlite"])
        assert db.execute("SELECT count(*) FROM positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM orders WHERE status NOT IN ('filled','cancelled','rejected')").fetchone()[0] == 0
        db.close()
        for name, path in DBS.items():
            backup_db(path, backup/name)
            (backup/name).chmod(0o600)
        for name, path in CONFIGS.items():
            shutil.copy2(path, backup/name)
        if transitions_existed:
            shutil.copy2(MARKET/"policy-transitions.json", backup/"policy-transitions.json")
        log("   consistent backups:", backup)
        new_cfg = write_private(HERE/"migration-config", notifications_old)
        result = migrate(new_runtime, {"source": DBS["book.sqlite"].parent, "experiment": DBS["experiment.sqlite"].parent,
                                       "alerts": DBS["alerts.sqlite"].parent, "market": MARKET}, HERE/"migration-config")
        (HERE/"migration-result.json").write_text(json.dumps(result, indent=2) + "\n")
        log("   migration committed:", json.dumps({k: result[k] for k in ("replay", "source_events_preserved",
            "experiment_events_preserved", "virtual_books", "source_cash")}))
        replace_file(CONFIGS["source-policy.json"], (HERE/"repo/infra/trading/policy.demo.json").read_bytes())
        replace_file(CONFIGS["source-compose.yaml"],
                     CONFIGS["source-compose.yaml"].read_text().replace("image: " + runtime, "image: " + new_runtime).encode())
        replace_file(CONFIGS["notifications.json"], (json.dumps(new_cfg, indent=2) + "\n").encode())
        access = json.loads(CONFIGS["access.json"].read_text())
        access["source_policy_hash"] = NEW
        replace_file(CONFIGS["access.json"], (json.dumps(access, indent=2) + "\n").encode())
        env = CONFIGS["experiment-deployment.env"].read_text()
        replace_file(CONFIGS["experiment-deployment.env"],
                     env.replace("VALOR_EXPERIMENT_IMAGE=" + experiment, "VALOR_EXPERIMENT_IMAGE=" + new_experiment).encode())
        groups = [("valor-demo-worker-1", ["feed", "worker", "agents", "research"]),
                  ("valor-experiment-ledgers-1", ["ledgers"]), ("valor-telegram-notifier-1", ["notifier"])]
        for name, _ in groups:
            run(compose_cmd(state, name) + ["config", "--quiet"])
        log("== starting migrated services")
        for name, services in groups:
            run(compose_cmd(state, name) + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", *services], timeout=300)
        restarted = True
    except Exception as exc:
        log("!! FAILED before restart:", exc)
        if not restarted:
            log("== automatic rollback: restoring databases, configs and original containers")
            for name, path in DBS.items():
                if (backup/name).exists():
                    for suffix in ("-wal", "-shm"):
                        Path(str(path) + suffix).unlink(missing_ok=True)
                    shutil.copy2(backup/name, path)
                    os.chown(path, 10001, 10001)
            for name, path in CONFIGS.items():
                if (backup/name).exists():
                    shutil.copy2(backup/name, path)
            if not transitions_existed:
                (MARKET/"policy-transitions.json").unlink(missing_ok=True)
            elif (backup/"policy-transitions.json").exists():
                shutil.copy2(backup/"policy-transitions.json", MARKET/"policy-transitions.json")
            run(["docker", "start", *WRITERS], timeout=300)
            log("   rollback complete: original images, policy and data restored; writers restarted")
        return 1

    log("== verifying (up to 3 minutes)")
    deadline, checks = time.time() + 180, {}
    while time.time() < deadline:
        time.sleep(15)
        try:
            status = json.loads(run(["curl", "-fsS", "http://127.0.0.1:9001/status"]))
            quotes = json.loads((MARKET/"quotes.json").read_text())
            snap = json.loads(EXP_SNAPSHOT.read_text())
            images = {n: inspect(n)["tag"] for n in WRITERS}
            checks = {
                "source_policy_new": status.get("policy_hash") == NEW,
                "source_healthy_demo": status.get("healthy") is True and status.get("mode") == "demo",
                "source_flat_unhalted": not status.get("halt") and not status.get("positions"),
                "feed_publishing_new_policy": quotes.get("policy_hash") == NEW and 0 <= time.time()-quotes["timestamp"] <= 30,
                "experiment_running_unhalted": not snap.get("halt") and 0 <= time.time()-snap.get("timestamp", 0) <= 60,
                "experiment_policy_new": snap.get("policy_hash") == NEW,
                "images_pinned": all(images[n] == new_runtime for n in SOURCE)
                                 and images["valor-experiment-ledgers-1"] == new_experiment,
                "unrelated_services_untouched": all(
                    {k: inspect(n)[k] for k in ("id", "started", "restarts")} == {k: state[n][k] for k in ("id", "started", "restarts")}
                    for n in UNRELATED)}
            if all(checks.values()):
                break
        except Exception as exc:
            checks = {"error": str(exc)}
    ok = bool(checks) and all(v is True for v in checks.values())
    result = {"release": RID, "old_policy": OLD, "new_policy": NEW, "checks": checks, "ok": ok,
              "images": {"runtime": new_runtime, "experiment": new_experiment},
              "previous_images": {"runtime": runtime, "experiment": experiment}, "backup": str(backup)}
    (HERE/"activation-result.json").write_text(json.dumps(result, indent=2) + "\n")
    log(json.dumps(checks, indent=2))
    log("== RESULT:", "ALL CHECKS PASSED" if ok else "CHECKS INCOMPLETE - services left running in demo; inspect before changing anything")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
