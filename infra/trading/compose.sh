#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
exec docker compose --env-file infra/trading/deployment.env -f infra/trading/compose.yaml "$@"
