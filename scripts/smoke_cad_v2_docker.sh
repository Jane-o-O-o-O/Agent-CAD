#!/usr/bin/env bash
# Requires Docker Compose and a Linux host exposing Landlock ABI >= 3.
# Keeps the named data volume so revision state is never destroyed by this gate.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null 2>&1; then
  echo 'Docker full-stack gate unavailable: docker CLI is not installed.' >&2
  exit 2
fi
docker info >/dev/null
docker compose version >/dev/null
export CAD_V2_PORT="${CAD_V2_PORT:-5179}"
export CAD_V2_LOCAL_TOKEN="${CAD_V2_LOCAL_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
export CAD_V2_API_KEY=''
cad_smoke_project="${CAD_V2_SMOKE_PROJECT:-agent-cad-v2-smoke-$(date +%s)-$$}"
cad_smoke_temp="$(mktemp -d)"
compose=(docker compose -p "$cad_smoke_project" -f docker-compose.cad-v2.yml)
cleanup() {
  "${compose[@]}" logs --no-color > "$cad_smoke_temp/compose.log" 2>&1 || true
  if [[ -n "${CAD_V2_SMOKE_LOG_DIR:-}" ]]; then
    mkdir -p "$CAD_V2_SMOKE_LOG_DIR"
    cp "$cad_smoke_temp/compose.log" "$CAD_V2_SMOKE_LOG_DIR/compose.log"
  fi
  "${compose[@]}" down || true
  rm -rf "$cad_smoke_temp"
}
trap cleanup EXIT
"${compose[@]}" config --quiet
"${compose[@]}" up --build --detach --wait --wait-timeout 240
"${compose[@]}" exec -T cad-v2 python - < scripts/probe_cad_v2_isolation.py
python3 scripts/smoke_cad_v2_http.py --base-url "http://127.0.0.1:$CAD_V2_PORT" --phase create --state "$cad_smoke_temp/state.json"
"${compose[@]}" restart cad-v2
python3 scripts/smoke_cad_v2_http.py --base-url "http://127.0.0.1:$CAD_V2_PORT" --phase verify --state "$cad_smoke_temp/state.json"
