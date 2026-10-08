#!/usr/bin/env python3
"""Consistent local SQLite backups; never includes credentials. Run on the VPS as root."""
import datetime as dt
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile


def main():
    deployment_path = Path("/opt/valor-study/infra/trading/deployment.json")
    deployment = json.loads(deployment_path.read_text()) if deployment_path.exists() else {"project": "valor-study", "policy": "policy.paper.json"}
    if deployment.get("project") not in {"valor-study", "valor-demo"} or deployment.get("policy") not in {"policy.paper.json", "policy.demo.json"}:
        raise ValueError("unrecognized backup deployment")
    destination = Path("/var/backups/valor-study")
    destination.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.umask(0o027)
    with tempfile.TemporaryDirectory(prefix="valor-backup-") as temp:
        root = Path(temp)
        for volume in ("state", "outbox", "inbox", "market", "research", "agent_usage", "news"):
            source = Path(subprocess.check_output([
                "docker", "volume", "inspect", "--format", "{{.Mountpoint}}",
                "valor-study_agent_usage" if volume == "agent_usage" else deployment["project"] + "_" + volume
            ], text=True).strip())
            target = root / volume
            target.mkdir()
            for file in source.rglob("*"):
                if not file.is_file() or file.is_symlink() or file.suffix not in {".json", ".sqlite", ".flag"}:
                    continue
                copy = target / file.relative_to(source)
                copy.parent.mkdir(parents=True, exist_ok=True)
                if file.suffix == ".sqlite":
                    with closing(sqlite3.connect(f"file:{file}?mode=ro", uri=True)) as src, closing(sqlite3.connect(copy)) as dst:
                        src.backup(dst)
                        # Standalone archive: do not hash transient WAL/shared-memory sidecars.
                        dst.execute("PRAGMA journal_mode=DELETE")
                        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise RuntimeError("backup integrity check failed")
                else:
                    shutil.copyfile(file, copy)
        shutil.copyfile("/opt/valor-study/infra/trading/"+deployment["policy"], root / "policy.json")
        (root / "deployment.json").write_text(json.dumps(deployment, sort_keys=True))
        manifest = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in root.rglob("*") if f.is_file()}
        (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = destination / (stamp + ".tar.gz")
        temporary = destination / (stamp + ".tmp")
        with tarfile.open(temporary, "w:gz") as archive:
            for file in root.iterdir():
                archive.add(file, arcname=file.name)
        temporary.chmod(0o640)
        shutil.chown(temporary, group="valor-monitor")
        temporary.replace(target)
        latest = destination / "latest.tar.gz"
        staged = destination / ".latest"
        staged.symlink_to(target.name)
        staged.replace(latest)
        print(json.dumps({"backup": target.name, "files": len(manifest), "bytes": target.stat().st_size}))


if __name__ == "__main__":
    main()
