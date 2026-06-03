from io import BytesIO
import csv
import json
import xml.etree.ElementTree as ET

from app.domain.models.cad_intake import ExtractedContent, ExtractedSourceFile, ExtractedTable, ExtractedTextBlock
from app.infrastructure.cad_parsers.base import CADFileParser


class TextCADParser(CADFileParser):
    name = "text"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        raw = data.getvalue()
        text, encoding = self._decode(raw)
        extension = file.extension.lower()

        if extension in {".csv", ".tsv"}:
            delimiter = "\t" if extension == ".tsv" else ","
            rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
            content.tables.append(
                ExtractedTable(
                    source_file=file.filename,
                    rows=rows[:500],
                    label="uploaded_table",
                    metadata={"encoding": encoding, "truncated": len(rows) > 500},
                )
            )
        elif extension == ".json":
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=json.dumps(json.loads(text), ensure_ascii=False, indent=2)[:20000],
                    label="json",
                    metadata={"encoding": encoding},
                )
            )
        elif extension == ".xml":
            root = ET.fromstring(text)
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=ET.tostring(root, encoding="unicode")[:20000],
                    label="xml",
                    metadata={"encoding": encoding, "root": root.tag},
                )
            )
        else:
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=text[:20000],
                    label="text",
                    metadata={"encoding": encoding, "truncated": len(text) > 20000},
                )
            )
        return content

    def _decode(self, data: bytes) -> tuple[str, str]:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace"), "utf-8-replace"

