from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from .parsers import ParseError, parse_upload
from .schemas import CleanParseResponse, HealthResponse, ParseResponse


app = FastAPI(
    title="Data Processor Service",
    description="Scoped file-to-JSON backend service for project agent integration.",
    version="0.1.0",
)


SUPPORTED_FORMATS = [
    "txt",
    "md",
    "html",
    "htm",
    "json",
    "csv",
    "xlsx",
    "xls",
    "doc",
    "docx",
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "bmp",
    "tif",
    "tiff",
]
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024


@app.get("/")
async def root() -> dict[str, object]:
    return {
        "service": "Data Processor Service",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "parse_endpoint": "/data/parse",
        "usage": "Open /docs in a browser, or POST a multipart file field named 'file' to /data/parse.",
    }


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        max_file_size_bytes=MAX_FILE_SIZE_BYTES,
        supported_formats=SUPPORTED_FORMATS,
    )


@app.post("/data/parse", response_model=ParseResponse | CleanParseResponse)
async def parse_file(
    file: UploadFile = File(...),
    include_raw: bool = Form(True),
    include_debug_images: bool = Form(True),
    output_mode: str = Form("clean"),
) -> ParseResponse | CleanParseResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large. Max size is {MAX_FILE_SIZE_BYTES} bytes.",
        )

    try:
        return parse_upload(
            filename=file.filename or "uploaded_file",
            data=data,
            content_type=file.content_type,
            include_raw=include_raw,
            include_debug_images=include_debug_images,
            output_mode=output_mode,
        )
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
