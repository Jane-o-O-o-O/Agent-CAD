from io import BytesIO

from app.domain.models.cad_intake import (
    ExtractedCADEntity,
    ExtractedContent,
    ExtractedModelFeature,
    ExtractedSourceFile,
    ExtractedTextBlock,
)
from app.infrastructure.cad_parsers.base import CADFileParser


class CAD2DFileParser(CADFileParser):
    name = "cad_2d"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        if file.extension == ".dxf":
            text = data.getvalue().decode("utf-8", errors="replace")
            content.text_blocks.append(
                ExtractedTextBlock(
                    source_file=file.filename,
                    text=text[:20000],
                    label="dxf_raw_head",
                    metadata={"truncated": len(text) > 20000},
                )
            )
            content.cad_entities.append(
                ExtractedCADEntity(
                    source_file=file.filename,
                    entity_type="dxf_file",
                    data={"parser_status": "raw_head_only"},
                )
            )
            content.uncertain_items.append("DXF entity parsing is not installed yet. Add ezdxf for full layer/entity/dimension extraction.")
        else:
            content.uncertain_items.append(
                f"{file.filename} is a DWG file. Configure DWGConverter to convert DWG to DXF before parsing."
            )
        return content


class Model3DFileParser(CADFileParser):
    name = "model_3d"

    async def parse(self, file: ExtractedSourceFile, data: BytesIO) -> ExtractedContent:
        content = ExtractedContent()
        size = len(data.getvalue())
        content.model_features.append(
            ExtractedModelFeature(
                source_file=file.filename,
                feature_type="model_file",
                data={"extension": file.extension, "size": size},
            )
        )
        content.uncertain_items.append(
            f"{file.filename} is a 3D model. Configure STEP/IGES/STL parsers for bbox, projection, and feature extraction."
        )
        return content

