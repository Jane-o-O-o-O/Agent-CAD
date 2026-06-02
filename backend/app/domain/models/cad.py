from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
import uuid

from pydantic import BaseModel, Field


class CADUnit(str, Enum):
    MM = "mm"
    INCH = "inch"


class CADLayer(BaseModel):
    name: str
    color: Optional[int] = None
    line_type: Optional[str] = None
    visible: bool = True


class CADPoint(BaseModel):
    x: float
    y: float


class CADEntityBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    layer: str = "M-OBJECT"


class CADLine(CADEntityBase):
    type: Literal["line"] = "line"
    start: CADPoint
    end: CADPoint


class CADCircle(CADEntityBase):
    type: Literal["circle"] = "circle"
    center: CADPoint
    radius: float


class CADArc(CADEntityBase):
    type: Literal["arc"] = "arc"
    center: CADPoint
    radius: float
    start_angle: float
    end_angle: float


class CADPolyline(CADEntityBase):
    type: Literal["polyline"] = "polyline"
    points: List[CADPoint]
    closed: bool = False


class CADSlot(CADEntityBase):
    type: Literal["slot"] = "slot"
    center: CADPoint
    width: float
    length: float
    rotation: float = 0


class CADNote(CADEntityBase):
    type: Literal["note"] = "note"
    position: CADPoint
    text: str
    height: float = 3.5


CADEntity = Union[CADLine, CADCircle, CADArc, CADPolyline, CADSlot, CADNote]


class CADDimension(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: Literal["linear", "diameter", "radius", "note"] = "linear"
    layer: str = "M-DIM"
    start: Optional[CADPoint] = None
    end: Optional[CADPoint] = None
    position: CADPoint
    text: Optional[str] = None


class CADConstraint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    target_ids: List[str] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)


class MechanicalDesignBrief(BaseModel):
    part_type: Optional[str] = None
    units: CADUnit = CADUnit.MM
    features: List[Dict[str, Any]] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    manufacturing_notes: List[str] = Field(default_factory=list)
    source_references: List[Dict[str, Any]] = Field(default_factory=list)


class MechanicalCADDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    title: str = "Untitled mechanical drawing"
    units: CADUnit = CADUnit.MM
    layers: List[CADLayer] = Field(default_factory=list)
    entities: List[CADEntity] = Field(default_factory=list)
    dimensions: List[CADDimension] = Field(default_factory=list)
    constraints: List[CADConstraint] = Field(default_factory=list)
    brief: Optional[MechanicalDesignBrief] = None
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CADOperation(BaseModel):
    operation: str
    params: Dict[str, Any] = Field(default_factory=dict)


class CADOperationResult(BaseModel):
    success: bool
    document: MechanicalCADDocument
    added_entity_ids: List[str] = Field(default_factory=list)
    message: str = ""
