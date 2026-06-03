from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class CADSourceKind(str, Enum):
    TEXT = "text"
    OFFICE_DOCUMENT = "office_document"
    PDF = "pdf"
    IMAGE = "image"
    CAD_2D = "cad_2d"
    MODEL_3D = "model_3d"
    SPREADSHEET = "spreadsheet"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


class ExtractedSourceFile(BaseModel):
    file_id: Optional[str] = None
    filename: str
    content_type: Optional[str] = None
    extension: str = ""
    kind: CADSourceKind = CADSourceKind.UNKNOWN
    size: Optional[int] = None
    parser: str = ""
    parse_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)


class ExtractedTextBlock(BaseModel):
    source_file: str
    text: str
    label: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedTable(BaseModel):
    source_file: str
    rows: list[list[str]]
    label: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedImage(BaseModel):
    source_file: str
    label: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedCADEntity(BaseModel):
    source_file: str
    entity_type: str
    data: dict[str, Any] = Field(default_factory=dict)


class ExtractedModelFeature(BaseModel):
    source_file: str
    feature_type: str
    data: dict[str, Any] = Field(default_factory=dict)


class ExtractedContent(BaseModel):
    source_files: list[ExtractedSourceFile] = Field(default_factory=list)
    text_blocks: list[ExtractedTextBlock] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    images: list[ExtractedImage] = Field(default_factory=list)
    cad_entities: list[ExtractedCADEntity] = Field(default_factory=list)
    model_features: list[ExtractedModelFeature] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    equipment: list[dict[str, Any]] = Field(default_factory=list)
    connections: list[dict[str, Any]] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)

    def merge(self, other: "ExtractedContent") -> None:
        self.source_files.extend(other.source_files)
        self.text_blocks.extend(other.text_blocks)
        self.tables.extend(other.tables)
        self.images.extend(other.images)
        self.cad_entities.extend(other.cad_entities)
        self.model_features.extend(other.model_features)
        self.dimensions.extend(other.dimensions)
        self.equipment.extend(other.equipment)
        self.connections.extend(other.connections)
        self.uncertain_items.extend(other.uncertain_items)

