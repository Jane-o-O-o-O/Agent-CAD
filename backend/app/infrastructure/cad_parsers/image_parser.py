from io import BytesIO

from app.domain.models.cad_intake import ExtractedContent, ExtractedImage, ExtractedSourceFile
from app.infrastructure.cad_parsers.base import CADFileParser


class ImageCADParser(CADFileParser):
    name = "image"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        try:
            from PIL import Image
        except ImportError:
            content.images.append(ExtractedImage(source_file=file.filename, label=file.filename))
            content.uncertain_items.append("Pillow is not installed; image dimensions and format were not parsed.")
            return content

        try:
            image = Image.open(data)
            content.images.append(
                ExtractedImage(
                    source_file=file.filename,
                    label=file.filename,
                    width=image.width,
                    height=image.height,
                    format=image.format,
                    metadata={"frames": getattr(image, "n_frames", 1)},
                )
            )
            content.uncertain_items.append(
                f"{file.filename} is an image. Configure OCR/VisionAnalyzer to extract drawing text and geometry."
            )
        except Exception as exc:
            content.uncertain_items.append(f"Failed to inspect image {file.filename}: {exc}")
        return content

