from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional

from app.domain.models.cad_intake import (
    ExtractedContent,
    ExtractedImage,
    ExtractedSourceFile,
    ExtractedTable,
    ExtractedTextBlock,
)
from app.infrastructure.cad_parsers.base import CADFileParser


class DoclingCADParser(CADFileParser):
    name = "docling"

    def __init__(self, fallback: Optional[CADFileParser] = None):
        self._fallback = fallback

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            return await self._fallback_or_warning(file, data, "Docling is not installed.")

        with tempfile.TemporaryDirectory(prefix="cad_docling_") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / file.filename
            input_path.write_bytes(data.getvalue())
            source_path = self._prepare_source_path(file, input_path, temp_path)
            if source_path is None:
                return await self._fallback_or_warning(
                    file,
                    BytesIO(input_path.read_bytes()),
                    f"{file.filename} could not be converted to a Docling-supported file.",
                )

            try:
                result = DocumentConverter().convert(source_path)
                document = result.document
            except Exception as exc:
                return await self._fallback_or_warning(file, BytesIO(input_path.read_bytes()), f"Docling failed: {exc}")

            return self._content_from_docling_document(file, document, source_path)

    def _prepare_source_path(self, file: ExtractedSourceFile, input_path: Path, temp_path: Path) -> Optional[Path]:
        if file.extension in {".doc", ".xls"}:
            target = "docx" if file.extension == ".doc" else "xlsx"
            converter = shutil.which("libreoffice") or shutil.which("soffice")
            if not converter:
                return None
            try:
                subprocess.run(
                    [
                        converter,
                        "--headless",
                        "--convert-to",
                        target,
                        "--outdir",
                        str(temp_path),
                        str(input_path),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return None
            converted = temp_path / f"{input_path.stem}.{target}"
            return converted if converted.exists() else None
        return input_path

    def _content_from_docling_document(self, file: ExtractedSourceFile, document, source_path: Path) -> ExtractedContent:
        content = ExtractedContent()
        markdown = self._safe_export_markdown(document)
        doc_dict = self._safe_export_dict(document)

        if markdown:
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=markdown[:50000],
                    label="docling_markdown",
                    metadata={
                        "converted_source": source_path.name,
                        "truncated": len(markdown) > 50000,
                    },
                )
            )

        for index, table in enumerate(self._collect_items(doc_dict, "table")[:50], start=1):
            rows = self._table_rows(table)
            if rows:
                content.tables.append(
                    ExtractedTable(
                        source_file=file.filename,
                        rows=rows[:200],
                        label=f"docling_table_{index}",
                        metadata={"converted_source": source_path.name},
                    )
                )

        for index, image in enumerate(self._collect_items(doc_dict, "image")[:100], start=1):
            content.images.append(
                ExtractedImage(
                    source_file=file.filename,
                    label=image.get("label") or image.get("name") or f"docling_image_{index}",
                    metadata={"converted_source": source_path.name},
                )
            )

        if not markdown and not content.tables and not content.images:
            content.uncertain_items.append(f"Docling parsed {file.filename}, but no usable content was exported.")

        return content

    async def _fallback_or_warning(self, file: ExtractedSourceFile, data: BytesIO, warning: str) -> ExtractedContent:
        if self._fallback:
            content = await self._fallback.parse(file, data)
            content.uncertain_items.append(warning)
            content.uncertain_items.append(f"Fell back to {self._fallback.name} parser for {file.filename}.")
            return content
        return ExtractedContent(uncertain_items=[warning])

    def _safe_export_markdown(self, document) -> str:
        try:
            return document.export_to_markdown() or ""
        except Exception:
            return ""

    def _safe_export_dict(self, document) -> dict:
        for method_name in ("export_to_dict", "model_dump", "dict"):
            method = getattr(document, method_name, None)
            if not method:
                continue
            try:
                value = method()
                if isinstance(value, dict):
                    return value
            except Exception:
                continue
        return {}

    def _collect_items(self, node, kind: str) -> list[dict]:
        found: list[dict] = []
        if isinstance(node, dict):
            marker = " ".join(str(node.get(key, "")).lower() for key in ("type", "label", "name", "self_ref"))
            if kind in marker:
                found.append(node)
            for value in node.values():
                found.extend(self._collect_items(value, kind))
        elif isinstance(node, list):
            for value in node:
                found.extend(self._collect_items(value, kind))
        return found

    def _table_rows(self, table: dict) -> list[list[str]]:
        for key in ("data", "table_data", "rows"):
            value = table.get(key)
            rows = self._rows_from_value(value)
            if rows:
                return rows
        return []

    def _rows_from_value(self, value) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        rows: list[list[str]] = []
        for row in value:
            if isinstance(row, list):
                rows.append([self._cell_text(cell) for cell in row])
            elif isinstance(row, dict):
                cells = row.get("cells") or row.get("data")
                if isinstance(cells, list):
                    rows.append([self._cell_text(cell) for cell in cells])
        return [row for row in rows if any(cell for cell in row)]

    def _cell_text(self, cell) -> str:
        if isinstance(cell, dict):
            for key in ("text", "content", "value"):
                if key in cell and cell[key] is not None:
                    return str(cell[key])
            return ""
        if cell is None:
            return ""
        return str(cell)

