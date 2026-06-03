from app.domain.external.file import FileStorage
from app.domain.models.cad_intake import ExtractedContent
from app.infrastructure.cad_parsers import CADFileParserService


class CADIntakeService:
    def __init__(self, file_storage: FileStorage):
        self._parser_service = CADFileParserService(file_storage)

    async def analyze_uploads(
        self,
        attachments: list[dict] | None,
        user_id: str | None = None,
    ) -> ExtractedContent:
        return await self._parser_service.parse_attachments(attachments, user_id)

