from pathlib import Path

from app.domain.models.cad_intake import CADSourceKind


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".xml"}
OFFICE_EXTENSIONS = {".doc", ".docx"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
CAD_2D_EXTENSIONS = {".dxf", ".dwg"}
MODEL_3D_EXTENSIONS = {".step", ".stp", ".iges", ".igs", ".stl"}
SPREADSHEET_EXTENSIONS = {".xls", ".xlsx"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz"}


def extension_for(filename: str) -> str:
    return Path(filename).suffix.lower()


def classify_file(filename: str, content_type: str | None = None) -> CADSourceKind:
    extension = extension_for(filename)
    if extension in TEXT_EXTENSIONS:
        return CADSourceKind.TEXT
    if extension in OFFICE_EXTENSIONS:
        return CADSourceKind.OFFICE_DOCUMENT
    if extension in PDF_EXTENSIONS:
        return CADSourceKind.PDF
    if extension in IMAGE_EXTENSIONS:
        return CADSourceKind.IMAGE
    if extension in CAD_2D_EXTENSIONS:
        return CADSourceKind.CAD_2D
    if extension in MODEL_3D_EXTENSIONS:
        return CADSourceKind.MODEL_3D
    if extension in SPREADSHEET_EXTENSIONS:
        return CADSourceKind.SPREADSHEET
    if extension in ARCHIVE_EXTENSIONS:
        return CADSourceKind.ARCHIVE

    if content_type:
        lowered = content_type.lower()
        if "pdf" in lowered:
            return CADSourceKind.PDF
        if lowered.startswith("image/"):
            return CADSourceKind.IMAGE
        if "spreadsheet" in lowered or "excel" in lowered:
            return CADSourceKind.SPREADSHEET
        if "word" in lowered:
            return CADSourceKind.OFFICE_DOCUMENT
        if lowered.startswith("text/"):
            return CADSourceKind.TEXT

    return CADSourceKind.UNKNOWN

