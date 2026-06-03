from io import BytesIO

from app.domain.models.cad_intake import ExtractedContent, ExtractedSourceFile, ExtractedTextBlock
from app.infrastructure.cad_parsers.base import CADFileParser


class PDFCADParser(CADFileParser):
    name = "pdf"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        try:
            from pypdf import PdfReader
        except ImportError:
            content.uncertain_items.append("pypdf is not installed; cannot extract PDF text.")
            return content

        try:
            reader = PdfReader(data)
            texts: list[str] = []
            for index, page in enumerate(reader.pages[:50], start=1):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    texts.append(f"[Page {index}]\n{page_text.strip()}")
        except Exception as exc:
            content.uncertain_items.append(f"Failed to parse PDF {file.filename}: {exc}")
            return content

        if texts:
            text = "\n\n".join(texts)
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=text[:30000],
                    label="pdf_text",
                    metadata={"page_count": len(reader.pages), "truncated": len(text) > 30000},
                )
            )
        else:
            content.uncertain_items.append(
                f"{file.filename} has no extractable PDF text. It may need a fixed PDF rendering + OCR parser."
            )
        return content

