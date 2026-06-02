from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.application.errors.exceptions import NotFoundError
from app.application.services.cad_service import CADService
from app.domain.models.user import User
from app.interfaces.dependencies import get_cad_service, get_current_user
from app.interfaces.schemas.base import APIResponse
from app.interfaces.schemas.cad import (
    ApplyCADOperationRequest,
    ApplyCADOperationsRequest,
    ApplyCADOperationResponse,
    CADPlanFromPromptRequest,
    CADPlanFromPromptResponse,
    CADPlanStep,
    CADDocumentResponse,
    CreateCADDocumentRequest,
    CreateCADDocumentFromPromptRequest,
)


router = APIRouter(prefix="/cad", tags=["cad"])


@router.post("/documents", response_model=APIResponse[CADDocumentResponse])
async def create_cad_document(
    request: CreateCADDocumentRequest,
    current_user: User = Depends(get_current_user),
    cad_service: CADService = Depends(get_cad_service),
) -> APIResponse[CADDocumentResponse]:
    document = await cad_service.create_document(
        user_id=current_user.id,
        title=request.title,
        session_id=request.session_id,
        units=request.units,
        brief=request.brief,
    )
    return APIResponse.success(CADDocumentResponse(document=document))


@router.post("/documents/from-prompt", response_model=APIResponse[ApplyCADOperationResponse])
async def create_cad_document_from_prompt(
    request: CreateCADDocumentFromPromptRequest,
    current_user: User = Depends(get_current_user),
    cad_service: CADService = Depends(get_cad_service),
) -> APIResponse[ApplyCADOperationResponse]:
    result = await cad_service.create_document_from_prompt(
        user_id=current_user.id,
        prompt=request.prompt,
        title=request.title,
        session_id=request.session_id,
        units=request.units,
        attachments=request.attachments,
    )
    return APIResponse.success(
        ApplyCADOperationResponse(
            document=result.document,
            added_entity_ids=result.added_entity_ids,
            message=result.message,
        )
    )


@router.post("/plans/from-prompt", response_model=APIResponse[CADPlanFromPromptResponse])
async def create_cad_plan_from_prompt(
    request: CADPlanFromPromptRequest,
    cad_service: CADService = Depends(get_cad_service),
) -> APIResponse[CADPlanFromPromptResponse]:
    brief, operations, message = await cad_service.plan_from_prompt(
        prompt=request.prompt,
        units=request.units,
        attachments=request.attachments,
    )
    steps = [
        CADPlanStep(
            id=str(index + 1),
            title=_cad_operation_title(operation.operation),
            description=_cad_operation_description(operation.operation, operation.params),
            operation=operation,
        )
        for index, operation in enumerate(operations)
    ]
    return APIResponse.success(
        CADPlanFromPromptResponse(
            title="Mechanical CAD plan",
            message=message,
            brief=brief,
            steps=steps,
        )
    )


@router.get("/documents/{document_id}", response_model=APIResponse[CADDocumentResponse])
async def get_cad_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    cad_service: CADService = Depends(get_cad_service),
) -> APIResponse[CADDocumentResponse]:
    document = await cad_service.get_document(document_id, current_user.id)
    if not document:
        raise NotFoundError("CAD document not found")
    return APIResponse.success(CADDocumentResponse(document=document))


@router.post("/documents/{document_id}/operations", response_model=APIResponse[ApplyCADOperationResponse])
async def apply_cad_operation(
    document_id: str,
    request: ApplyCADOperationRequest,
    current_user: User = Depends(get_current_user),
    cad_service: CADService = Depends(get_cad_service),
) -> APIResponse[ApplyCADOperationResponse]:
    try:
        result = await cad_service.apply_operation(document_id, request.operation, current_user.id)
    except FileNotFoundError:
        raise NotFoundError("CAD document not found")

    return APIResponse.success(
        ApplyCADOperationResponse(
            document=result.document,
            added_entity_ids=result.added_entity_ids,
            message=result.message,
        )
    )


@router.post("/documents/{document_id}/operations/batch", response_model=APIResponse[ApplyCADOperationResponse])
async def apply_cad_operations(
    document_id: str,
    request: ApplyCADOperationsRequest,
    current_user: User = Depends(get_current_user),
    cad_service: CADService = Depends(get_cad_service),
) -> APIResponse[ApplyCADOperationResponse]:
    try:
        result = await cad_service.apply_operations(document_id, request.operations, current_user.id)
    except FileNotFoundError:
        raise NotFoundError("CAD document not found")

    return APIResponse.success(
        ApplyCADOperationResponse(
            document=result.document,
            added_entity_ids=result.added_entity_ids,
            message=result.message,
        )
    )


@router.get("/documents/{document_id}/export/dxf")
async def export_cad_document_dxf(
    document_id: str,
    current_user: User = Depends(get_current_user),
    cad_service: CADService = Depends(get_cad_service),
) -> Response:
    try:
        dxf = await cad_service.export_dxf(document_id, current_user.id)
    except FileNotFoundError:
        raise NotFoundError("CAD document not found")

    return Response(
        content=dxf,
        media_type="application/dxf",
        headers={"Content-Disposition": f"attachment; filename={document_id}.dxf"},
    )


def _cad_operation_title(operation: str) -> str:
    return {
        "create_plate": "Create base outline",
        "add_centerline": "Add centerline",
        "add_dimension": "Add dimension",
        "add_hole": "Add hole",
        "add_slot": "Add slot",
        "add_note": "Add note",
    }.get(operation, operation.replace("_", " ").title())


def _cad_operation_description(operation: str, params: dict) -> str:
    if operation == "create_plate":
        return f"Draw the base plate {params.get('width')} x {params.get('height')} with R{params.get('corner_radius', 0)} corners."
    if operation == "add_hole":
        return f"Place a hole at {params.get('center')} with diameter {params.get('diameter')}."
    if operation == "add_slot":
        return f"Place a slot at {params.get('center')} with length {params.get('length')} and width {params.get('width')}."
    if operation == "add_centerline":
        return f"Draw centerline from {params.get('start')} to {params.get('end')}."
    if operation == "add_dimension":
        return f"Add dimension label {params.get('text')}."
    if operation == "add_note":
        return f"Add note: {params.get('text')}."
    return f"Apply CAD operation {operation}."
