from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ParsedContent(BaseModel):
    text: str = ""
    tables: list[Any] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    ocr_text: str = ""
    extracted_sections: dict[str, Any] = Field(default_factory=dict)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    diagram_analysis: dict[str, Any] = Field(default_factory=dict)
    image_analysis_summary: list[dict[str, Any]] = Field(default_factory=list)


class ParseResponse(BaseModel):
    status: Literal["success"]
    filename: str
    source_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: ParsedContent
    errors: list[str] = Field(default_factory=list)


class CleanParseResponse(BaseModel):
    status: Literal["success"]
    filename: str
    source_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    diagram_analysis: dict[str, Any] = Field(default_factory=dict)
    image_analysis_summary: list[dict[str, Any]] = Field(default_factory=list)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    max_file_size_bytes: int
    supported_formats: list[str]
