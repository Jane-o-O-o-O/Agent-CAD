#!/usr/bin/env bash
# Smoke the ORIGINAL development application, not the standalone CAD V2 image.
# Requires Docker Compose >= 2.24.4, Python 3, and network access for image builds.
# This is a startup/API gate: no paid models, CAD execution, browser UI, or Claw.
# Named volumes are always preserved. Existing development resources are untouched.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo 'Original full-stack smoke unavailable: docker CLI is not installed.' >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Original full-stack smoke requires Python 3 on the host.' >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Original full-stack smoke unavailable: Docker daemon is not accessible.' >&2
  exit 2
fi
if ! compose_version="$(docker compose version --short 2>/dev/null)"; then
  echo 'Original full-stack smoke requires Docker Compose >= 2.24.4.' >&2
  exit 2
fi
python3 - "$compose_version" <<'PY'
import re
import sys
parts = re.match(r"v?(\d+)\.(\d+)\.(\d+)", sys.argv[1])
if not parts or tuple(map(int, parts.groups())) < (2, 24, 4):
    sys.exit("Original full-stack smoke requires Compose >= 2.24.4 for !override.")
PY

# A new project per invocation prevents reuse/removal of a user's existing stack.
smoke_project="agent-cad-full-smoke-$(date +%s)-$$"
smoke_temp="$(mktemp -d)"
export FULL_STACK_SMOKE_PROJECT="$smoke_project"
export FULL_STACK_SMOKE_EMPTY_ENV="$smoke_temp/empty.env"
: > "$FULL_STACK_SMOKE_EMPTY_ENV"
compose=(docker compose --env-file "$FULL_STACK_SMOKE_EMPTY_ENV"
  --project-directory "$PWD" -p "$smoke_project"
  -f docker-compose-development.yml -f "$smoke_temp/override.yml")
smoke_started=0
cleanup() {
  result=$?
  trap - EXIT
  if (( smoke_started )); then
    if [[ -n "${FULL_STACK_SMOKE_LOG_DIR:-}" ]]; then
      mkdir -p "$FULL_STACK_SMOKE_LOG_DIR"
      "${compose[@]}" logs --no-color > "$FULL_STACK_SMOKE_LOG_DIR/compose.log" 2>&1 || true
      "${compose[@]}" ps --all > "$FULL_STACK_SMOKE_LOG_DIR/services.txt" 2>&1 || true
    fi
    if (( result )); then
      echo 'Original full-stack smoke failed. Container status:' >&2
      "${compose[@]}" ps --all >&2 || true
    fi
    # Deliberately omit --volumes and --remove-orphans.
    if ! "${compose[@]}" down --timeout 15; then
      echo "Cleanup failed for project $smoke_project; its volumes were retained." >&2
      result=1
    fi
    echo "Preserved smoke volumes for project $smoke_project."
  fi
  rm -rf "$smoke_temp"
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Keep all seven original development services/builds/commands. Use the source
# copied into the images rather than hot-reload source mounts, so masking dotenv
# files cannot create files in the checkout. Only frontend is published, on a
# dynamic loopback port. The fixed development sandbox needs no Docker socket.
cat > "$smoke_temp/override.yml" <<'YAML'
services:
  frontend-dev:
    ports: !override ["127.0.0.1::5173"]
    environment: !override
      NODE_ENV: development
      VITE_API_URL: ""
      BACKEND_URL: http://backend:8000
    volumes: !override
      - /app/node_modules
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env:ro
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env.local:ro
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env.development:ro
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env.development.local:ro
  backend:
    build:
      args:
        USE_ALIYUN_MIRROR: "${FULL_STACK_USE_ALIYUN_MIRROR:-true}"
    ports: !override []
    env_file: !override []
    environment: !override
      API_KEY: smoke-mock-only
      API_BASE: http://mockserver:8090/v1
      MODEL_PROVIDER: openai
      MODEL_NAME: mock
      AUTH_PROVIDER: none
      JWT_SECRET_KEY: smoke-local-only-not-for-deployment
      MONGODB_URI: mongodb://mongodb:27017
      MONGODB_DATABASE: manus_smoke
      REDIS_HOST: redis
      SANDBOX_ADDRESS: sandbox
      CLAW_ENABLED: "false"
      MANUS_API_BASE_URL: http://backend:8000
      DATA_PROCESSOR_BASE_URL: http://data-processor:8010
      CAD_V2_DATA_DIR: /var/lib/agent-cad
      CAD_V2_API_KEY: ""
      CAD_V2_ISOLATION: strict
      CAD_DOCLING_ENABLED: "false"
      SEARCH_PROVIDER: bing_web
      LOG_LEVEL: INFO
    volumes: !override
      - /app/__pycache__
      - /app/.venv
      - cad_v2_data:/var/lib/agent-cad
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env:ro
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/app/.env:ro
  sandbox:
    build:
      args:
        USE_ALIYUN_MIRROR: "${FULL_STACK_USE_ALIYUN_MIRROR:-true}"
    ports: !override []
    environment: !override
      UVI_ARGS: "--reload"
      LOG_LEVEL: INFO
    volumes: !override
      - /app/__pycache__
      - /app/.venv
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env:ro
  data-processor:
    ports: !override []
    volumes: !override
      - /app/__pycache__
      - ${FULL_STACK_SMOKE_EMPTY_ENV}:/app/.env:ro
  mockserver:
    environment: !override
      MOCK_DATA_FILE: default.yaml
      MOCK_DELAY: "0"
    volumes: !override
      - /app/__pycache__
      - /app/.venv
  mongodb:
    ports: !override []
volumes:
  mongodb_data:
    name: ${FULL_STACK_SMOKE_PROJECT}-mongodb-data
  cad_v2_data:
    name: ${FULL_STACK_SMOKE_PROJECT}-cad-v2-data
networks:
  manus-network:
    name: ${FULL_STACK_SMOKE_PROJECT}-network
YAML

"${compose[@]}" config --quiet
echo 'Building and starting the original seven-service development stack.'
smoke_started=1
"${compose[@]}" up --build --detach

# Compose has no original service healthchecks, so wait for real database replies
# and HTTP responses instead of treating a running container as healthy.
cat > "$smoke_temp/probe.py" <<'PY'
import json
import time
import urllib.request

def get(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)

checks = {
    "backend OpenAPI": lambda: "/api/v1/cad-v2/jobs" in get("http://backend:8000/openapi.json")["paths"]
        and "/api/v1/sessions" in get("http://backend:8000/openapi.json")["paths"],
    "backend auth bypass": lambda: get("http://backend:8000/api/v1/auth/status")["data"]["auth_provider"] == "none"
        and get("http://backend:8000/api/v1/auth/me")["data"]["id"] == "anonymous",
    "frontend backend proxy": lambda: get("http://frontend-dev:5173/api/v1/auth/status")["data"]["auth_provider"] == "none",
    "sandbox OpenAPI": lambda: bool(get("http://sandbox:8080/openapi.json")["paths"]),
    "data processor health": lambda: get("http://data-processor:8010/health")["status"] == "ok",
    "mock LLM completion": lambda: bool(get("http://mockserver:8090/v1/chat/completions",
        {"model": "mock", "messages": [{"role": "user", "content": "smoke"}]} )["choices"]),
}
deadline = time.monotonic() + 240
while checks and time.monotonic() < deadline:
    for name, check in list(checks.items()):
        try:
            if check():
                print("PASS " + name, flush=True)
                del checks[name]
        except Exception:
            pass
    if checks:
        time.sleep(2)
if checks:
    raise SystemExit("Timed out waiting for: " + ", ".join(checks))
PY

wait_command() {
  local label="$1"
  shift
  local deadline=$((SECONDS + 240))
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for $label." >&2
      return 1
    fi
    sleep 2
  done
  echo "PASS $label"
}
wait_command MongoDB "${compose[@]}" exec -T mongodb mongosh --quiet --eval 'if (!db.adminCommand({ping: 1}).ok) quit(1)'
wait_command Redis "${compose[@]}" exec -T redis sh -c 'test "$(redis-cli ping)" = PONG'
wait_command 'backend Python runtime' "${compose[@]}" exec -T backend /app/.venv/bin/python -c 'pass'
"${compose[@]}" exec -T backend /app/.venv/bin/python - < "$smoke_temp/probe.py"

frontend_address="$("${compose[@]}" port frontend-dev 5173)"
python3 - "$frontend_address" <<'PY'
import json
import sys
import urllib.request
base = "http://" + sys.argv[1]
with urllib.request.urlopen(base + "/", timeout=10) as response:
    assert response.status == 200 and b'<div id="app">' in response.read(), "Frontend HTML missing app root"
with urllib.request.urlopen(base + "/api/v1/auth/me", timeout=10) as response:
    assert json.load(response)["data"]["id"] == "anonymous", "Frontend auth proxy failed"
print("PASS host-to-frontend HTML and auth proxy")
PY
echo 'PASS original full-stack startup/API smoke (7 services; authentication bypass).'
echo 'CAD generation/isolation, actual browser rendering, chat tool execution, and Claw require separate gates.'
