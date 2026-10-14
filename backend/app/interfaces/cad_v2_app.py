"""Authenticated local CAD V2 server, independent of Mongo/Redis/browser services."""
from __future__ import annotations

import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.application.services.cad_v2_service import CADV2Service, get_cad_v2_service
from app.infrastructure.cad_v2.store import utcnow
from app.interfaces.api.cad_v2_routes import CADRequestSizeLimit, build_router, success


class LocalLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=2048)


class LocalRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=1, max_length=2048)


def create_app(service: CADV2Service | None = None, token: str | None = None) -> FastAPI:
    configured_token = token if token is not None else os.environ.get("CAD_V2_LOCAL_TOKEN", "")
    security = HTTPBearer(auto_error=False)
    created_at = utcnow()
    local_user = {"id": "cad-v2-local", "fullname": "Local CAD user", "email": "local@localhost", "role": "user",
                  "is_active": True, "created_at": created_at, "updated_at": created_at}
    failed_logins: deque[float] = deque(maxlen=10)
    def valid_token(value: str) -> bool:
        return bool(configured_token) and secrets.compare_digest(value.encode("utf-8"), configured_token.encode("utf-8"))
    def service_dep() -> CADV2Service:
        return service if service is not None else get_cad_v2_service()
    async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if not credentials or not valid_token(credentials.credentials):
            raise HTTPException(401, "Valid CAD V2 local bearer token required", headers={"WWW-Authenticate": "Bearer"})
        return local_user.copy()
    @asynccontextmanager
    async def lifespan(_app):
        if not configured_token or len(configured_token) < 16:
            raise RuntimeError("Set CAD_V2_LOCAL_TOKEN to a private token of at least 16 characters before starting the local server")
        await service_dep().start()
        try:
            yield
        finally:
            await service_dep().shutdown()
    app = FastAPI(title="Agent-CAD V2", lifespan=lifespan)
    app.add_middleware(CADRequestSizeLimit)
    app.include_router(build_router(service_dep, current_user), prefix="/api/v1")
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        return JSONResponse({"code": exc.status_code, "msg": str(exc.detail), "data": None}, status_code=exc.status_code, headers=exc.headers)
    @app.exception_handler(RequestValidationError)
    async def input_error(_request: Request, _exc: RequestValidationError):
        return JSONResponse({"code": 422, "msg": "Invalid request fields or parameter types", "data": None}, status_code=422)
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "cad-v2", "deployment": "single-host-single-supervisor"}
    @app.get("/api/v1/auth/status")
    async def auth_status(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        authenticated = bool(credentials and valid_token(credentials.credentials))
        return success({"auth_provider": "local", "authenticated": authenticated,
                        "user": local_user.copy() if authenticated else None, "local_token_required": True})
    @app.post("/api/v1/auth/login")
    async def login(body: LocalLogin):
        now = time.monotonic()
        while failed_logins and now - failed_logins[0] >= 60:
            failed_logins.popleft()
        if len(failed_logins) >= 10:
            raise HTTPException(429, "Too many failed local login attempts; retry after one minute")
        matches = valid_token(body.password)
        if body.email != "local@localhost" or not matches:
            failed_logins.append(now)
            raise HTTPException(401, "Invalid local email or private token")
        failed_logins.clear()
        return success({"user": local_user.copy(), "access_token": configured_token,
                        "refresh_token": configured_token, "token_type": "bearer"})
    @app.post("/api/v1/auth/refresh")
    async def refresh(body: LocalRefresh):
        if not valid_token(body.refresh_token):
            raise HTTPException(401, "Invalid refresh token")
        return success({"access_token": configured_token, "token_type": "bearer"})
    @app.post("/api/v1/auth/logout")
    async def logout(_user=Depends(current_user)):
        # Fixed local credentials are revoked by rotating the environment token;
        # the existing frontend clears its local access/refresh state on logout.
        return success({})
    @app.get("/api/v1/auth/me")
    async def me(user=Depends(current_user)):
        return success(user)
    @app.get("/api/v1/config")
    @app.get("/api/v1/config/frontend")
    async def config():
        return success({"auth_provider": "local", "cad_v2_enabled": True, "cad_v2_standalone": True, "show_github_button": False,
                        "github_repository_url": "", "google_analytics_id": None, "claw_enabled": False})
    # Optional same-origin built SPA; API requests never fall back to index.html.
    dist = Path(os.environ.get("CAD_V2_FRONTEND_DIST", str(Path(__file__).resolve().parents[3] / "frontend" / "dist")))
    if dist.is_dir():
        @app.get("/{path:path}")
        async def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "API route not found")
            root = dist.resolve()
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                raise HTTPException(404, "File not found")
            if candidate.is_file():
                return FileResponse(candidate)
            index = root / "index.html"
            if not index.is_file():
                raise HTTPException(404, "Frontend build not found")
            return FileResponse(index)
    return app


app = create_app()
