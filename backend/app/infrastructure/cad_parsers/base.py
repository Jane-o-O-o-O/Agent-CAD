from abc import ABC, abstractmethod
from io import BytesIO

from app.domain.models.cad_intake import ExtractedContent, ExtractedSourceFile


class CADFileParser(ABC):
    name: str = "base"

    @abstractmethod
    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        ...

