# Data Processor Service

This is a scoped backend data-processing service for converting uploaded files into normalized JSON. It is designed to be called by the main backend or a project-level agent as a tool, not to replace the project's main agent.

## Scope

The service is allowed to:

- Accept a single uploaded file.
- Validate file size and extension.
- Route the file to a fixed parser workflow.
- Extract text, tables, records, images, and metadata where supported.
- Return a stable JSON response.

The service must not:

- Execute scripts or macros from uploaded files.
- Decide the whole project's next action.
- Call the project main agent.
- Modify business data directly.
- Access business databases.

## Supported Formats

- `txt`, `md`
- `html`, `htm`
- `json`
- `csv`
- `xlsx`, `xls`
- `doc`, `docx`
- `pdf`
- `png`, `jpg`, `jpeg`, `bmp`, `tif`, `tiff`

## Run

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r data_processor_service\requirements.txt
```

Start the API:

```powershell
uvicorn app.main:app --app-dir data_processor_service --host 0.0.0.0 --port 8010 --reload
```

Open:

```text
http://127.0.0.1:8010/docs
```

## API

### `GET /health`

Returns service health and supported formats.

### `POST /data/parse`

Upload one file as form field `file`.

Optional form field:

- `include_raw`: boolean, defaults to `true`
- `include_debug_images`: boolean, defaults to `true`. Set to `false` for compact image records while keeping clean summary fields.
- `output_mode`: `clean` or `full`, defaults to `clean`. Use `full` only when you need parser/OCR/debug details.

Clean response shape:

```json
{
  "status": "success",
  "filename": "example.docx",
  "source_type": "docx",
  "metadata": {
    "size_bytes": 12345,
    "image_count": 1
  },
  "text": "...",
  "diagram_analysis": {
    "status": "analyzed",
    "source_image_index": 7,
    "source_filename": "image001.gif",
    "summary": "...",
    "nodes": [],
    "edges": [],
    "process_flow": [],
    "warnings": []
  },
  "image_analysis_summary": [],
  "structured_data": {},
  "errors": []
}
```

## Integration Recommendation

The main project agent should call this service as a deterministic tool:

```text
Main Agent -> POST /data/parse -> normalized JSON -> downstream logic
```

Keep LLM field extraction as a later layer. The first version should stay deterministic and easy to test.

## Legacy `.doc` Support

Legacy Word `.doc` files are converted to `.docx` first and then parsed by the same Word parser.

The converter requires one of these to be available on the machine running the service:

- LibreOffice / `soffice`, recommended for Linux servers.
- Microsoft Word with `pywin32`, useful on Windows development machines.

When a `.doc` file is parsed successfully, response metadata includes:

```json
{
  "image_count": 6,
  "word_conversion": "doc_to_docx"
}
```

## Image-to-JSON Support

Images embedded in Word files are returned as JSON objects with an `analysis` field.

The current deterministic analyzer supports:

- Basic image metadata for raster images: format, width, height, mode.
- OCR text extraction when `pytesseract` and the Tesseract executable are installed.
- Optional EMF/WMF to PNG conversion when ImageMagick or LibreOffice is installed.
- Extra raster image exports when Microsoft Word can save embedded drawings/images as HTML assets.

OCR environment variables:

- `TESSERACT_CMD`: optional absolute path to `tesseract.exe`.
- `DATA_PROCESSOR_OCR_LANG`: OCR language, defaults to `chi_sim+eng`.

Optional vision model environment variables:

- `DATA_PROCESSOR_VISION_API_KEY`: OpenAI-compatible vision API key. This is required for vision analysis.
- `DATA_PROCESSOR_VISION_PROVIDER`: provider label written into JSON results, defaults to `openai_compatible`.
- `DATA_PROCESSOR_ENABLE_VISION_MODEL`: set to `true` to force-enable vision analysis. If omitted, vision analysis is enabled when a vision API key is set.
- `DATA_PROCESSOR_VISION_MODEL`: OpenAI-compatible model name, for example `gpt-5.5`.
- `DATA_PROCESSOR_VISION_BASE_URL`: OpenAI-compatible chat completions endpoint.
- `DATA_PROCESSOR_VISION_JSON_MODE`: set to `false` for models that do not support OpenAI JSON mode, such as `zai-org/GLM-4.5V`.
- `DATA_PROCESSOR_VISION_DETAIL`: image detail level, defaults to `auto`.
- `DATA_PROCESSOR_VISION_MAX_TOKENS`: defaults to `3000`.
- `DATA_PROCESSOR_VISION_TIMEOUT_SECONDS`: defaults to `90`.
- `DATA_PROCESSOR_VISION_RETRIES`: defaults to `1`.
- `DATA_PROCESSOR_VISION_MODE`: defaults to `graphics_only`; use OCR for plain text images and call the vision model only for graphics/flow-like images or weak OCR.
- `DATA_PROCESSOR_VISION_SKIP_IF_OCR_TEXT_CHARS`: defaults to `120`; OCR text at or above this length is treated as sufficient unless graphics keywords are detected.
- `DATA_PROCESSOR_VISION_MAX_IMAGES_PER_FILE`: defaults to `1`.
- `DATA_PROCESSOR_VISION_SKIP_EMBEDDED_WORD_IMAGES`: defaults to `true`; Word's raw EMF/WMF images are skipped and exported raster images are analyzed instead.
- `DATA_PROCESSOR_VISION_SKIP_CAD_DRAWINGS`: defaults to `false`; CAD/mechanical drawings use a CAD-specific vision prompt when local OCR rules are not enough.
- `DATA_PROCESSOR_VISION_MAX_SIDE_PIXELS`: defaults to `960`; raster images are normalized before being sent to the vision model.
- `DATA_PROCESSOR_VISION_MAX_IMAGE_BYTES`: defaults to `8388608`.

The service automatically loads `data_processor_service/.env` on startup. Copy `.env.example` to `.env` and fill in the key:

```text
DATA_PROCESSOR_VISION_API_KEY=sk-...
DATA_PROCESSOR_ENABLE_VISION_MODEL=true
DATA_PROCESSOR_VISION_MODEL=gpt-5.5
DATA_PROCESSOR_VISION_BASE_URL=https://api.example.com/v1/chat/completions
```

Then start the service normally:

```powershell
uvicorn app.main:app --app-dir data_processor_service --host 0.0.0.0 --port 8010 --reload
```

When vision analysis is enabled, each image `analysis` object may include:

```json
{
  "vision_model": {
    "status": "analyzed",
    "provider": "openai_compatible",
    "model": "gpt-5.5"
  },
  "vision_structured_data": {
    "summary": "...",
    "nodes": [],
    "edges": [],
    "equipment": [],
    "process_flow": [],
    "warnings": []
  }
}
```

If an image cannot be analyzed yet, the API still returns it as JSON with:

```json
{
  "analysis": {
    "status": "unsupported",
    "ocr_text": "",
    "structured_data": {},
    "errors": ["..."]
  }
}
```
