#!/usr/bin/env bash
# Back up the evolver_data Docker volume before deploy or migration.
# Usage: ./infra/backup-evolver-data.sh [output.tgz]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-$ROOT/evolver_data_backup_${STAMP}.tgz}"
VOLUME="${EVOLVER_DATA_VOLUME:-valor_evolver_data}"

if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  echo "Volume $VOLUME not found — nothing to back up (first deploy is fine)."
  exit 0
fi

docker run --rm \
  -v "${VOLUME}:/data:ro" \
  -v "$ROOT:/backup" \
  alpine:3.20 \
  tar czf "/backup/$(basename "$OUT")" -C /data .

echo "Backed up $VOLUME -> $OUT"