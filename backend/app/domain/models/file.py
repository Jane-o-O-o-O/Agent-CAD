from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel


class FileInfo(BaseModel):
    file_id: str
    filename: str
    file_path: Optional[str] = None
    content_type: Optional[str] = None
    size: int
    upload_date: datetime
    metadata: Optional[Dict[str, Any]] = None
