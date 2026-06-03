from io import BytesIO
from zipfile import BadZipFile, ZipFile

from app.domain.models.cad_intake import ExtractedContent, ExtractedSourceFile, ExtractedTextBlock
from app.infrastructure.cad_parsers.base import CADFileParser


class ArchiveCADParser(CADFileParser):
    name = "archive"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        if file.extension != ".zip":
            content.uncertain_items.append(
                f"{file.filename} is an archive. Only .zip listing is supported in the first parser version."
            )
            return content

        try:
            with ZipFile(data) as archive:
                names = archive.namelist()
        except BadZipFile as exc:
            content.uncertain_items.append(f"Failed to read zip archive {file.filename}: {exc}")
            return content

        content.text_blocks.append(
            ExtractedTextBlock(
                source_file=file.filename,
                text="\n".join(names[:500]),
                label="archive_file_list",
                metadata={"file_count": len(names), "truncated": len(names) > 500},
            )
        )
        content.uncertain_items.append(
            f"{file.filename} was listed but nested files were not recursively parsed yet."
        )
        return content

