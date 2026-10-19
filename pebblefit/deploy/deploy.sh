#!/usr/bin/env bash
# Sync source to the server and rebuild.  Data, .env and downloads stay on the
# server and are never overwritten.
set -euo pipefail
HOST="${HOST:?set HOST=user@server, e.g. HOST=root@my-server ./deploy/deploy.sh}"
DEST="${DEST:-/root/compose/pebble}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ssh "$HOST" "mkdir -p $DEST/data $DEST/downloads $DEST/watchapp"
rsync -az --delete --exclude __pycache__ "$ROOT/web/" "$HOST:$DEST/web/"
rsync -az "$ROOT"/bridge/{app,fitness,training,hrv,recovery,dashboard,watch_api,weight,physiology,briefing}.py "$ROOT"/deploy/{Dockerfile,docker-compose.yml,.dockerignore} "$HOST:$DEST/"
ssh "$HOST" "cd $DEST && docker compose up -d --build && sleep 3 && docker compose ps"
