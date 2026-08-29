#!/usr/bin/env bash
set -euo pipefail
cd /opt/sentero/box

# A factory-fresh box may intentionally have no Internet yet. In that state we
# start only the Sentero web application; its network wizard brings up the host
# setup AP. Once WiFi succeeds, sentero-network starts the remaining stack.
CONNECTIVITY="$(nmcli -t networking connectivity check 2>/dev/null || true)"
if [ "$CONNECTIVITY" = "full" ]; then
  exec /usr/bin/docker compose up -d
fi
exec /usr/bin/docker compose up -d --no-deps sentero
