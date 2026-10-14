# Agent-CAD – Cloud Agent Starter Skill

> Use this skill when setting up, running, or testing any part of the Agent-CAD codebase.

---

## Architecture at a Glance

| Service | Language / Framework | Default Port | Entry Point |
|---|---|---|---|
| **Frontend** | Vue 3 + TypeScript, Vite | 5173 (dev) / 80 (prod) | `frontend/src/main.ts` |
| **Backend** | Python 3.12, FastAPI | 8000 | `backend/app/main.py` |
| **Sandbox** | Python 3.10, FastAPI | 8080 (API), 5900 (VNC) | `sandbox/app/main.py` |
| **Mockserver** | Python, FastAPI | 8090 | `mockserver/main.py` |
| **MongoDB** | mongo:7.0 | 27017 | — |
| **Redis** | redis:7.0 | — | — |

---

## 1 · Quick Start (Docker Compose Dev Stack)

The fastest way to bring up everything:

```bash
cp .env.example .env          # create env file (edit as needed)
./dev.sh up -d                # brings up all services via docker-compose-development.yml
./dev.sh logs -f backend      # tail backend logs
```

To stop: `./dev.sh down`

### Key `.env` knobs for development

| Variable | Recommended Dev Value | Purpose |
|---|---|---|
| `AUTH_PROVIDER` | `none` (skip login) or `local` | Controls auth; `local` uses `LOCAL_AUTH_EMAIL`/`LOCAL_AUTH_PASSWORD` |
| `LOCAL_AUTH_EMAIL` | `admin@example.com` | Single-user local auth email |
| `LOCAL_AUTH_PASSWORD` | `admin` | Single-user local auth password |
| `API_BASE` | `http://mockserver:8090/v1` | Points backend at the mock LLM server |
| `API_KEY` | any non-empty string | Required – set to anything when using mockserver |
| `SEARCH_PROVIDER` | `bing_web` | No API key needed |
| `SANDBOX_ADDRESS` | `sandbox` | Uses the single dev sandbox container |
| `LOG_LEVEL` | `DEBUG` | Verbose logs for development |

### Bypassing Auth Entirely

Set `AUTH_PROVIDER=none` in `.env`. The frontend treats the user as an anonymous authenticated user, and the backend skips token checks. This is the easiest option for Cloud agents that don't need to test auth.

### Using Local Auth

Set `AUTH_PROVIDER=local`. Login at `http://localhost:5173/login` with `LOCAL_AUTH_EMAIL` / `LOCAL_AUTH_PASSWORD` (defaults: `admin@example.com` / `admin`).

---

## 2 · Running Services Individually (Without Docker)

### Backend

```bash
cd backend
# Install deps (requires uv – https://github.com/astral-sh/uv)
uv sync
# Needs running MongoDB and Redis (start via docker or locally)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Requires `API_KEY` env var set (or a `.env` file in `backend/`). The app calls `get_settings().validate()` on startup and raises if `API_KEY` is empty.

### Frontend

```bash
cd frontend
npm install
# Set BACKEND_URL so the Vite dev server proxies /api to the backend
BACKEND_URL=http://localhost:8000 npm run dev
```

Opens on `http://localhost:5173`. The Vite config auto-creates a proxy for `/api` when `BACKEND_URL` is set.

### Sandbox

The sandbox is typically used inside Docker (it runs Xvfb, Chrome, VNC via supervisord). Running it standalone requires those system dependencies.

### Mockserver

```bash
cd mockserver
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8090 --reload
```

Controls: `MOCK_DATA_FILE` (default: `default.yaml`), `MOCK_DELAY` (seconds, default: `1`).  
Mock data files live in `mockserver/mock_datas/` — switch scenarios by changing `MOCK_DATA_FILE` (options: `default.yaml`, `shell_tools.yaml`, `file_tools.yaml`, `browser_tools.yaml`, `search_tools.yaml`, `message_tools.yaml`).

---

## 3 · Testing Workflows by Codebase Area

### 3.1 Backend (pytest, end-to-end against running server)

Tests live in `backend/tests/` and hit `http://localhost:8000` via `requests`. They require a **running** backend + MongoDB + Redis.

```bash
# Start infra
./dev.sh up -d mongodb redis backend

# Run all tests
cd backend
uv run pytest

# Run specific file or marker
uv run pytest tests/test_auth_routes.py
uv run pytest -m file_api
```

Key test files:
- `tests/test_auth_routes.py` – registration, login, token refresh, logout, admin endpoints
- `tests/test_api_file.py` – file upload / download API
- `tests/test_sandbox_file.py` – sandbox file operations

Fixtures in `conftest.py` provide a `client` (requests.Session) and a `BASE_URL = "http://localhost:8000/api/v1"`.

### 3.2 Sandbox (pytest)

```bash
# Start sandbox
./dev.sh up -d sandbox

cd sandbox
uv run pytest
```

### 3.3 Frontend

The CAD V2 workspace has a Node test runner. Validate with:

```bash
cd frontend
npm test             # CAD job/revision/SSE state regression tests
npm run type-check    # vue-tsc type checking
npm run build         # production build (catches template + TS errors)
```

For manual UI testing, start the full dev stack (`./dev.sh up -d`) and open `http://localhost:5173`.
For standalone V2, use the deployment instructions below. After building, run
`python frontend/tests/browser_cad_v2.py --output /tmp/cad-v2-browser` from the
repository root in a CAD Python environment with Playwright/Chromium installed.
This starts its own local server and tests real login, WebGL loading, parameter
rebuilds, revision-bound exports and history restoration without model calls.

### 3.4 Mockserver

The mockserver has no tests. Verify it responds:

```bash
curl -X POST http://localhost:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mock","messages":[{"role":"user","content":"hi"}]}'
```

### 3.5 Integration / End-to-End (full stack)

1. `./dev.sh up -d` — start all services.
2. Open `http://localhost:5173`.
3. Login (or bypass with `AUTH_PROVIDER=none`).
4. Create a new session, send a message — the mockserver returns canned tool calls so the agent loop runs without a real LLM.
5. Check backend logs: `./dev.sh logs -f backend`.
6. Check sandbox VNC at `localhost:5902` (dev port mapping) to see browser/desktop actions.

---

## 4 · Common Environment Notes

### Exact 3D CAD V2 without the legacy infrastructure

Use Linux and Python 3.12. The full application dependencies are large because
document processing includes ML libraries; the independent CAD runtime is locked
in `backend/requirements-cad-v2.txt`.

```bash
cd backend
uv venv --python 3.12 .venv-cad
uv pip install --python .venv-cad/bin/python -r requirements-cad-v2.txt
export CAD_V2_LOCAL_TOKEN='set-your-own-private-token-at-least-16-characters'
export CAD_V2_DATA_DIR="$PWD/.cad-v2-data"
.venv-cad/bin/python -m uvicorn app.interfaces.cad_v2_app:app --host 127.0.0.1 --port 8000 --workers 1
```

Start frontend with `BACKEND_URL=http://127.0.0.1:8000 npm run dev`, then open
`/chat/cad-v2` and log in with `local@localhost` and the configured token.
Only one supervisor may open the same SQLite data directory. Never delete it
to bypass a lock. Keep the persistent volume and absolute data-root stable when
backing up/restoring revisions. Local token logout clears browser state; rotate
the environment token and restart to revoke it.

For tests, install pytest, pytest-asyncio, requests, ezdxf, agentscope==2.0.0,
`mcp>=1.9,<2` and pydantic-settings in the CAD environment. Run
`python -m pytest tests/cad_v2 tests/test_agentscope_runtime.py tests/test_skills.py -q`.
These tests do not require Mongo, Redis or paid models; CAD tests execute the real
kernel and subprocesses. Do not run the unrelated HTTP/sandbox tests without their
services. `python -m benchmarks.cad_v2.run --output /tmp/cad-v2-benchmark` runs the
fixed 30-design corpus. Frontend uses `npm test`, `npm run type-check`, `npm run build`.
Keep production model credentials out of worker environments, reports and commits.

<!-- Added 2026-10-14: independently runnable exact CAD V2, real-kernel testing and revision persistence. -->

### Generate a real CAD sample without infrastructure

For a focused preview of the existing deterministic workbench generator:

```bash
cd backend
python examples/generate_cad_preview.py --output /tmp/cad-source-preview
```

The script calls the real `CADService.create_document_from_prompt` and
`export_dxf`, checks the exported geometry with ezdxf, and renders that DXF to
PNG. It needs `pydantic`, `ezdxf` and, for the preview, `matplotlib`; use
`--no-render` when matplotlib is unavailable. It does not exercise the LLM,
frontend, MongoDB, Redis or sandbox. The unmodified service's incomplete
dimension annotations remain visible in the sample.

<!-- Added 2026-10-14: reproducible source-generated CAD preview without full-stack services. -->

### Docker socket

The backend container mounts `/var/run/docker.sock` (read-only) to manage sandbox containers in production mode. In dev mode with `SANDBOX_ADDRESS=sandbox`, it talks directly to the single sandbox container instead.

### Debugging the backend

The dev compose starts the backend with `debugpy` listening on port `5678`. Attach a remote Python debugger (VS Code "Remote Attach" config: host `localhost`, port `5678`).

### Resetting the mock server

The mockserver tracks a `current_index` for sequential canned responses. It auto-resets when it receives a fresh 2-message conversation. To force-reset, restart the container: `./dev.sh restart mockserver`. The dev compose also mounts the source, so touching `mockserver/main.py` triggers an auto-reload.

### MongoDB data

Dev data persists in a named volume `agent-cad-mongodb-data`. To wipe: `./dev.sh down -v`.

---

## 5 · API Quick Reference

### Auth endpoints (`/api/v1/auth/`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | No | `{fullname, email, password}` |
| POST | `/auth/login` | No | `{email, password}` → tokens |
| POST | `/auth/refresh` | No | `{refresh_token}` → new access token |
| POST | `/auth/logout` | Bearer | Invalidates session |
| GET | `/auth/status` | No | Returns `{authenticated, auth_provider}` |
| GET | `/auth/me` | Bearer | Current user info |
| POST | `/auth/change-password` | Bearer | `{old_password, new_password}` |

### Session endpoints (`/api/v1/sessions/`)

Create, list, delete sessions; send chat messages; subscribe to SSE events.

### File endpoints (`/api/v1/file/`)

Upload and download files.

### Sandbox endpoints (port 8080, `/api/v1/`)

- `/shell/*` – execute shell commands
- `/file/*` – read/write files inside sandbox
- `/supervisor/*` – manage supervised processes

---

## 6 · Updating This Skill

When you discover a new testing trick, environment workaround, or operational runbook step:

1. **Open** `.cursor/skills/starter.md`.
2. **Add** the new knowledge to the appropriate section (or create a new `##` section if it doesn't fit).
3. **Keep it concrete** — include exact commands, env var values, and file paths. Avoid vague advice.
4. **Date your addition** with a short comment at the end of the new content: `<!-- Added YYYY-MM-DD: brief reason -->`.

Examples of things worth adding:
- A new mock data file was created → add it to the mockserver section.
- A new pytest marker was introduced → add it to the backend testing section.
- A new env var controls behavior → add it to the `.env` knobs table.
- A workaround for a flaky test or Docker issue → add a troubleshooting subsection.
