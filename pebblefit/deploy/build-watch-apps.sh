#!/usr/bin/env bash
# Build the three watch apps for your server and copy them to downloads/.
#
#   PEBBLE_HEALTH_URL=https://pebble-health.my.lan ./deploy/build-watch-apps.sh
#
# Needs the Pebble SDK (`pebble` on PATH, or PEBBLE=/path/to/pebble).  Each app
# is built from a temporary copy with the server URL substituted, so the
# source tree keeps its placeholder.
set -euo pipefail
URL="${PEBBLE_HEALTH_URL:?set PEBBLE_HEALTH_URL, e.g. https://pebble-health.my.lan}"
URL="${URL%/}"
PEBBLE="${PEBBLE:-pebble}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/downloads}"
mkdir -p "$OUT"
declare -A NAMES=([hrv-watch]=pebblefit-hrv.pbw [history-watch]=pebblefit-history.pbw [today-watch]=pebblefit-today.pbw)
for app in hrv-watch history-watch today-watch; do
  # The SDK names the bundle after its directory, so build in tmp/<app>.
  tmp="$(mktemp -d)"
  cp -r "$ROOT/$app" "$tmp/$app"
  rm -rf "$tmp/$app/build" "$tmp/$app"/*.pbw
  sed -i "s#https://pebble-health.example.lan#$URL#g" "$tmp/$app/src/pkjs/index.js"
  (cd "$tmp/$app" && "$PEBBLE" build >/dev/null)
  cp "$tmp/$app/build/$app.pbw" "$OUT/${NAMES[$app]}"
  rm -rf "$tmp"
  echo "built $OUT/${NAMES[$app]}"
done
