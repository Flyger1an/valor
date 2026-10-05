#!/usr/bin/env python3
"""Root-owned forced SSH command: status or secret-free backup download, never a shell."""
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request

command = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
backup = Path("/var/backups/valor-study/latest.tar.gz")
if command == "backup":
    with backup.open("rb") as source:
        shutil.copyfileobj(source, sys.stdout.buffer)
elif command in {"", "status"}:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9001/status", timeout=5) as response:
            status = json.load(response)
    except Exception:
        status = {"healthy": False, "reason": "monitor_unavailable"}
    disk = shutil.disk_usage("/")
    status["host"] = {"disk_free_bytes": disk.free, "disk_used_pct": round(100*disk.used/disk.total, 1),
                      "last_backup_at": backup.stat().st_mtime if backup.exists() else None}
    print(json.dumps(status, sort_keys=True))
else:
    print('{"error":"read_only_monitor_accepts_only_status_or_backup"}')
    sys.exit(64)
