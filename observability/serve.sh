#!/usr/bin/env bash
# Launch the live Agentic SDLC dashboard.
# Serves the repo root so the dashboard can read projects/<name>/artifacts/*.
# Usage:  ./observability/serve.sh [project] [port]
set -euo pipefail

PROJECT="${1:-neural-sync}"
PORT="${2:-8777}"

# repo root = parent of this script's directory
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="http://localhost:${PORT}/observability/dashboard.html?project=${PROJECT}"

echo "Serving repo root: ${ROOT}"
echo "Open the dashboard:  ${URL}"
echo "(Ctrl+C to stop)"

cd "$ROOT"
exec python3 -m http.server "$PORT"
