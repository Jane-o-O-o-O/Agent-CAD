"""Bounded structured-model gateway; geometry never executes model-supplied code."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.domain.cad_v2.errors import InfrastructureError, SchemaValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = 60
    retries: int = 2
    max_tokens: int = 10000

    @classmethod
    def from_env(cls, role: str = "planner") -> "ModelConfig":
        key = os.getenv("CAD_V2_API_KEY") or os.getenv("API_KEY", "")
        base = os.getenv("CAD_V2_API_BASE") or os.getenv("API_BASE", "https://api.openai.com/v1")
        model = os.getenv(f"CAD_V2_{role.upper()}_MODEL") or os.getenv("CAD_V2_MODEL") or os.getenv("MODEL_NAME", "gpt-6.1-sol")
        return cls(base.rstrip("/"), key, model,
                   timeout=float(os.getenv("CAD_V2_MODEL_TIMEOUT", "60")),
                   retries=int(os.getenv("CAD_V2_MODEL_RETRIES", "2")))

    def validate(self) -> None:
        parsed = urlsplit(self.base_url)
        if not self.api_key:
            raise InfrastructureError("CAD model API key is not configured")
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise InfrastructureError("Invalid CAD model base URL")
        if not 1 <= self.timeout <= 180 or not 0 <= self.retries <= 3 or not self.model:
            raise InfrastructureError("Invalid CAD model timeout, retry budget or model")


@dataclass
class ModelResponse:
    payload: dict[str, Any]
    usage: dict[str, int]
    elapsed_seconds: float
    model: str
    attempts: int


class StructuredModelGateway:
    def __init__(self, config: ModelConfig | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config or ModelConfig.from_env()
        self.transport = transport

    async def generate(self, *, role: str, instruction: str, request: str, schema: dict[str, Any]) -> ModelResponse:
        config = self.config
        config.validate()
        started = time.monotonic()
        # JSON-object mode supports OpenAI-compatible gateways; the complete local
        # schema is still mandatory and validation happens before compilation.
        body = {"model": config.model, "temperature": 0,
                "max_tokens": config.max_tokens, "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instruction + "\nReturn one JSON object conforming to this schema:\n" + json.dumps(schema, separators=(",", ":"))},
                    {"role": "user", "content": request},
                ]}
        async with httpx.AsyncClient(timeout=config.timeout, transport=self.transport) as client:
            for attempt in range(config.retries + 1):
                try:
                    response = await client.post(config.base_url + "/chat/completions", json=body,
                                                 headers={"Authorization": "Bearer " + config.api_key})
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt < config.retries:
                        await asyncio.sleep(min(0.5 * 2 ** attempt, 2))
                        continue
                    raise InfrastructureError("CAD model request timed out or could not connect",
                                              details={"attempts": attempt + 1}) from exc
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < config.retries:
                        await asyncio.sleep(min(0.5 * 2 ** attempt, 2))
                        continue
                if response.is_error:
                    # Do not persist provider response bodies or authorization headers.
                    raise InfrastructureError("CAD model provider rejected the request",
                                              details={"http_status": response.status_code, "attempts": attempt + 1})
                if len(response.content) > 2_000_000:
                    raise SchemaValidationError("CAD model response exceeds the size limit")
                try:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    payload = json.loads(content)
                    if not isinstance(payload, dict):
                        raise ValueError("JSON object required")
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    raise SchemaValidationError("CAD model returned invalid structured JSON") from exc
                usage = {key: value for key, value in (result.get("usage") or {}).items()
                         if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)}
                elapsed = time.monotonic() - started
                logger.info("cad_model_call role=%s model=%s attempts=%d elapsed=%.3f usage=%s",
                            role, config.model, attempt + 1, elapsed, usage)
                return ModelResponse(payload, usage, elapsed, config.model, attempt + 1)
        raise InfrastructureError("CAD model retry budget exhausted")
