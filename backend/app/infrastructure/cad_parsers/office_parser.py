from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.domain.models.cad_intake import (
    ExtractedContent,
    ExtractedImage,
    ExtractedSourceFile,
    ExtractedTable,
    ExtractedTextBlock,
)
from app.infrastructure.cad_parsers.base import CADFileParser


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class OfficeCADParser(CADFileParser):
    name = "office"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        if file.extension == ".docx":
            return self._parse_docx(file, data)

        if file.extension == ".doc":
            return self._parse_doc(file, data)

        return ExtractedContent(
            uncertain_items=[f"{file.filename} is not a supported Word document type."]
        )

    def _parse_doc(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        converter = shutil.which("libreoffice") or shutil.which("soffice")
        if not converter:
            content.uncertain_items.append(
                f"{file.filename} is a legacy .doc file, but LibreOffice/soffice is not available for conversion."
            )
            return content

        with tempfile.TemporaryDirectory(prefix="cad_doc_") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / file.filename
            input_path.write_bytes(data.getvalue())
            try:
                subprocess.run(
                    [
                        converter,
                        "--headless",
                        "--convert-to",
                        "docx",
                        "--outdir",
                        str(temp_path),
                        str(input_path),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                content.uncertain_items.append(f"Failed to convert {file.filename} to docx: {exc}")
                return content

            docx_path = temp_path / f"{input_path.stem}.docx"
            if not docx_path.exists():
                content.uncertain_items.append(f"LibreOffice did not produce a docx for {file.filename}.")
                return content

            converted_file = file.model_copy(
                update={
                    "filename": docx_path.name,
                    "extension": ".docx",
                    "parser": self.name,
                }
            )
            parsed = self._parse_docx(converted_file, BytesIO(docx_path.read_bytes()))
            parsed.uncertain_items.append(f"{file.filename} was converted to docx before parsing.")
            return parsed

    def _parse_docx(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        try:
            with ZipFile(data) as docx:
                names = set(docx.namelist())
                if "word/document.xml" not in names:
                    content.uncertain_items.append(f"{file.filename} has no word/document.xml")
                    return content

                root = ET.fromstring(docx.read("word/document.xml"))
                paragraphs = self._extract_paragraphs(root)
                tables = self._extract_tables(root)
                media_names = sorted(name for name in names if name.startswith("word/media/"))
        except (BadZipFile, ET.ParseError) as exc:
            content.uncertain_items.append(f"Failed to parse {file.filename} as docx: {exc}")
            return content

        if paragraphs:
            text = "\n".join(paragraphs)
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=text[:30000],
                    label="docx_text",
                    metadata={"paragraph_count": len(paragraphs), "truncated": len(text) > 30000},
                )
            )

        for index, rows in enumerate(tables[:50], start=1):
            content.tables.append(
                ExtractedTable(
                    source_file=file.filename,
                    rows=rows,
                    label=f"docx_table_{index}",
                    metadata={"row_count": len(rows)},
                )
            )

        for media_name in media_names[:100]:
            content.images.append(
                ExtractedImage(
                    source_file=file.filename,
                    label=Path(media_name).name,
                    format=Path(media_name).suffix.lstrip(".").lower() or None,
                    metadata={"docx_path": media_name},
                )
            )

        return content

    def _extract_paragraphs(self, root: ET.Element) -> list[str]:
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{WORD_NS}p"):
            text = self._text_from_node(paragraph).strip()
            if text:
                paragraphs.append(text)
        return paragraphs

    def _extract_tables(self, root: ET.Element) -> list[list[list[str]]]:
        tables: list[list[list[str]]] = []
        for table in root.iter(f"{WORD_NS}tbl"):
            rows: list[list[str]] = []
            for tr in table.iter(f"{WORD_NS}tr"):
                row: list[str] = []
                for tc in tr.iter(f"{WORD_NS}tc"):
                    row.append(self._text_from_node(tc).strip())
                if any(cell for cell in row):
                    rows.append(row)
            if rows:
                tables.append(rows)
        return tables

    def _text_from_node(self, node: ET.Element) -> str:
        parts: list[str] = []
        for child in node.iter():
            if child.tag == f"{WORD_NS}t":
                parts.append(child.text or "")
            elif child.tag == f"{WORD_NS}tab":
                parts.append("\t")
        return "".join(parts)
