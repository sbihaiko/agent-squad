#!/usr/bin/env bash
# Check, and with --apply set up, phone access to the hub over the tailnet (ADR-0152).
set -euo pipefail
root=$(git rev-parse --show-toplevel)   # the project the hub serves
[ -f "$root/.env" ] || { echo "no $root/.env; see commands/start-mobile.md" >&2; exit 2; }
set -a; . "$root/.env"; set +a
: "${SQUAD_TAILNET_HOST:?set SQUAD_TAILNET_HOST in .env}" "${SQUAD_TAILNET_ALLOW:?set SQUAD_TAILNET_ALLOW in .env}"
ts=${TAILSCALE_BIN:-tailscale}; port=${HUB_PORT:-7700}; plug=${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}

if [ "${1:-}" = --apply ]; then
  "$ts" serve --bg "http://127.0.0.1:$port"
fi

echo "== tailscale"; "$ts" status | head -5 || true
echo "== serve";     "$ts" serve status || true
echo "== squad";     uv run -q --project "$plug" squad tailnet check || true
echo
echo "Owner steps if any check failed:"
echo "  1. Approve Serve and HTTPS certificates: https://login.tailscale.com/admin/dns"
echo "  2. /agent-squad:start-mobile --apply"
echo "Phone (Tailscale on): https://$SQUAD_TAILNET_HOST/agent-squad/mobile"
