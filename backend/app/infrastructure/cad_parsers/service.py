from io import BytesIO
import logging

from app.core.config import get_settings
from app.domain.external.file import FileStorage
from app.domain.models.cad_intake import CADSourceKind, ExtractedContent, ExtractedSourceFile
from app.infrastructure.cad_parsers.archive_parser import ArchiveCADParser
from app.infrastructure.cad_parsers.cad_file_parser import CAD2DFileParser, Model3DFileParser
from app.infrastructure.cad_parsers.classifier import classify_file, extension_for
from app.infrastructure.cad_parsers.docling_parser import DoclingCADParser
from app.infrastructure.cad_parsers.image_parser import ImageCADParser
from app.infrastructure.cad_parsers.office_parser import OfficeCADParser
from app.infrastructure.cad_parsers.pdf_parser import PDFCADParser
from app.infrastructure.cad_parsers.spreadsheet_parser import SpreadsheetCADParser
from app.infrastructure.cad_parsers.text_parser import TextCADParser

logger = logging.getLogger(__name__)


class CADFileParserService:
    def __init__(self, file_storage: FileStorage):
        self._file_storage = file_storage
        settings = get_settings()
        office_parser = OfficeCADParser()
        pdf_parser = PDFCADParser()
        image_parser = ImageCADParser()
        spreadsheet_parser = SpreadsheetCADParser()
        document_parser = DoclingCADParser(fallback=office_parser) if settings.cad_docling_enabled else office_parser
        pdf_intake_parser = DoclingCADParser(fallback=pdf_parser) if settings.cad_docling_enabled else pdf_parser
        image_intake_parser = DoclingCADParser(fallback=image_parser) if settings.cad_docling_enabled else image_parser
        spreadsheet_intake_parser = (
            DoclingCADParser(fallback=spreadsheet_parser) if settings.cad_docling_enabled else spreadsheet_parser
        )
        self._parsers = {
            CADSourceKind.TEXT: TextCADParser(),
            CADSourceKind.OFFICE_DOCUMENT: document_parser,
            CADSourceKind.PDF: pdf_intake_parser,
            CADSourceKind.IMAGE: image_intake_parser,
            CADSourceKind.CAD_2D: CAD2DFileParser(),
            CADSourceKind.MODEL_3D: Model3DFileParser(),
            CADSourceKind.SPREADSHEET: spreadsheet_intake_parser,
            CADSourceKind.ARCHIVE: ArchiveCADParser(),
        }

    async def parse_attachments(self, attachments: list[dict] | None, user_id: str | None = None) -> ExtractedContent:
        result = ExtractedContent()
        for attachment in attachments or []:
            file_id = attachment.get("file_id")
            if not file_id:
                result.uncertain_items.append("Attachment without file_id was skipped.")
                continue

            try:
                file_data, file_info = await self._file_storage.download_file(file_id, user_id)
            except Exception as exc:
                result.uncertain_items.append(f"Failed to download attachment {file_id}: {exc}")
                continue

            source_file = ExtractedSourceFile(
                file_id=file_id,
                filename=file_info.filename,
                content_type=file_info.content_type,
                extension=extension_for(file_info.filename),
                kind=classify_file(file_info.filename, file_info.content_type),
                size=file_info.size,
            )

            parser = self._parsers.get(source_file.kind)
            if not parser:
                source_file.parse_status = "unsupported"
                source_file.warnings.append("Unsupported file type for CAD intake.")
                result.source_files.append(source_file)
                result.uncertain_items.append(f"Unsupported file type: {file_info.filename}")
                continue

            source_file.parser = parser.name
            try:
                parsed = await parser.parse(source_file, BytesIO(file_data.read()))
                source_file.parse_status = "parsed"
                result.merge(parsed)
            except Exception as exc:
                logger.exception("Failed to parse CAD attachment %s", file_info.filename)
                source_file.parse_status = "failed"
                source_file.warnings.append(str(exc))
                result.uncertain_items.append(f"Failed to parse {file_info.filename}: {exc}")
            finally:
                result.source_files.append(source_file)

        return result
