from __future__ import annotations

import io
from pathlib import PurePosixPath
from typing import Any

import httpx

from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, tool


class DataProcessorToolkit(BaseToolkit):
    """File-to-JSON parsing tools backed by the data processor service."""

    name: str = "data_processor"

    def __init__(
        self,
        sandbox: Sandbox,
        base_url: str | None = None,
        timeout_seconds: float = 120.0,
    ):
        super().__init__()
        self.sandbox = sandbox
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_seconds = timeout_seconds

    @tool(parse_docstring=True)
    async def data_processor_parse_file(
        self,
        file: str,
        include_raw: bool = False,
        include_debug_images: bool = False,
        output_mode: str = "clean",
    ) -> ToolResult:
        """Parse an uploaded sandbox file into normalized JSON.

        Use this for deterministic extraction from TXT, Markdown, HTML, JSON, CSV,
        Excel, Word, PDF, and common image files before doing field extraction or CAD analysis.

        Args:
            file: Absolute path of the file in the sandbox.
            include_raw: Include raw text/content where available. Keep false for compact results.
            include_debug_images: Include verbose image/debug records. Keep false unless debugging.
            output_mode: Response shape, usually "clean"; use "full" only for parser details.
        """
        if not self.base_url:
            return ToolResult(
                success=False,
                message="DATA_PROCESSOR_BASE_URL is not configured.",
            )

        try:
            downloaded = await self.sandbox.file_download(file)
            payload = _read_all_bytes(downloaded)
            filename = PurePosixPath(file).name or "uploaded_file"

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/data/parse",
                    files={
                        "file": (
                            filename,
                            payload,
                            "application/octet-stream",
                        ),
                    },
                    data={
                        "include_raw": str(include_raw).lower(),
                        "include_debug_images": str(include_debug_images).lower(),
                        "output_mode": output_mode,
                    },
                )
                response.raise_for_status()

            parsed: Any = response.json()
            return ToolResult(
                success=True,
                message=f"Parsed {filename} with data processor.",
                data=parsed,
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                message=f"Data processor rejected {file}: {_response_detail(exc.response)}",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                message=f"Failed to parse {file} with data processor: {exc}",
            )


def _read_all_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, io.BytesIO):
        return data.getvalue()
    if hasattr(data, "read"):
        content = data.read()
        if isinstance(content, str):
            return content.encode("utf-8")
        return content
    raise TypeError(f"Unsupported downloaded file type: {type(data)!r}")


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        detail = body.get("detail")
        if detail:
            return str(detail)
    return str(body)
