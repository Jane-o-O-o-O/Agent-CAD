from typing import Optional

from pydantic import BaseModel

from app.domain.models.cad import CADOperation, CADUnit, MechanicalCADDocument, MechanicalDesignBrief


class CreateCADDocumentRequest(BaseModel):
    title: Optional[str] = None
    session_id: Optional[str] = None
    units: CADUnit = CADUnit.MM
    brief: Optional[MechanicalDesignBrief] = None


class CADDocumentResponse(BaseModel):
    document: MechanicalCADDocument


class ApplyCADOperationRequest(BaseModel):
    operation: CADOperation


class ApplyCADOperationsRequest(BaseModel):
    operations: list[CADOperation]


class ApplyCADOperationResponse(BaseModel):
    document: MechanicalCADDocument
    added_entity_ids: list[str] = []
    message: str = ""


class CreateCADDocumentFromPromptRequest(BaseModel):
    prompt: str
    title: Optional[str] = None
    session_id: Optional[str] = None
    units: CADUnit = CADUnit.MM
    attachments: list[dict] = []


class CADPlanFromPromptRequest(BaseModel):
    prompt: str
    units: CADUnit = CADUnit.MM
    attachments: list[dict] = []


class CADPlanStep(BaseModel):
    id: str
    title: str
    description: str
    operation: CADOperation


class CADPlanFromPromptResponse(BaseModel):
    title: str
    message: str
    brief: MechanicalDesignBrief
    steps: list[CADPlanStep]
