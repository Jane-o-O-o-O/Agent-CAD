from io import StringIO
from typing import Optional

import ezdxf
from langchain.tools import tool

from app.application.services.cad_service import CADService
from app.domain.external.sandbox import Sandbox
from app.domain.models.cad import CADUnit
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit


class CADToolkit(BaseToolkit):
    """CAD-specific tools for bounded ReAct drawing workflows."""

    name: str = "cad"

    def __init__(
        self,
        sandbox: Sandbox,
        cad_service: Optional[CADService] = None,
        user_id: str = "agent",
        session_id: str = "agent",
    ):
        super().__init__()
        self.sandbox = sandbox
        self.cad_service = cad_service or CADService()
        self.user_id = user_id
        self.session_id = session_id

    @tool(parse_docstring=True)
    async def cad_analyze_request(
        self,
        prompt: str,
        units: str = "mm",
    ) -> ToolResult:
        """Analyze a CAD drawing request and return a structured brief plus proposed operations.

        Use this before generating DXF. Decide from the returned brief whether the user request is complete enough.
        If important values are missing, ask the user instead of generating.

        Args:
            prompt: User CAD request plus any extracted attachment summary.
            units: Drawing units, usually "mm" or "inch".
        """
        cad_units = CADUnit.INCH if units == "inch" else CADUnit.MM
        brief, operations, message = await self.cad_service.plan_from_prompt(
            prompt=prompt,
            units=cad_units,
            user_id=self.user_id,
        )
        return ToolResult(
            success=True,
            message=message,
            data={
                "brief": brief.model_dump(mode="json"),
                "operations": [operation.model_dump(mode="json") for operation in operations],
                "operation_count": len(operations),
                "is_complete_enough": len(brief.unknowns) == 0,
                "unknowns": brief.unknowns,
            },
        )

    @tool(parse_docstring=True)
    async def cad_generate_dxf(
        self,
        prompt: str,
        output_path: str = "/home/ubuntu/output.dxf",
        units: str = "mm",
    ) -> ToolResult:
        """Generate a DXF drawing file from a complete CAD request.

        Call this only after cad_analyze_request shows the request is complete enough or the user accepted defaults.

        Args:
            prompt: Complete CAD request, including confirmed assumptions.
            output_path: Absolute sandbox path for the generated DXF file.
            units: Drawing units, usually "mm" or "inch".
        """
        cad_units = CADUnit.INCH if units == "inch" else CADUnit.MM
        result = await self.cad_service.create_document_from_prompt(
            user_id=self.user_id,
            prompt=prompt,
            session_id=self.session_id,
            units=cad_units,
        )
        dxf_content = await self.cad_service.export_dxf(result.document.id, self.user_id)
        write_result = await self.sandbox.file_write(
            file=output_path,
            content=dxf_content,
            trailing_newline=False,
        )
        return ToolResult(
            success=bool(write_result.success),
            message=f"Generated DXF at {output_path}",
            data={
                "document_id": result.document.id,
                "document": result.document.model_dump(mode="json"),
                "output_path": output_path,
                "brief": result.document.brief.model_dump(mode="json") if result.document.brief else None,
                "entity_count": len(result.document.entities),
                "dimension_count": len(result.document.dimensions),
                "added_entity_ids": result.added_entity_ids,
            },
        )

    @tool(parse_docstring=True)
    async def cad_validate_dxf(
        self,
        file: str,
        expected_summary: str = "",
    ) -> ToolResult:
        """Validate that a generated DXF can be parsed and contains drawing entities.

        Use this after cad_generate_dxf. If validation fails, regenerate or report the failure.

        Args:
            file: Absolute sandbox path of the DXF file to validate.
            expected_summary: Short natural-language summary of expected geometry.
        """
        read_result = await self.sandbox.file_read(file=file)
        if not read_result.success:
            return ToolResult(success=False, message=f"Failed to read DXF file: {file}")

        content = (read_result.data or {}).get("content", "")
        try:
            doc = ezdxf.read(StringIO(content))
            entities = list(doc.modelspace())
        except Exception as exc:
            return ToolResult(success=False, message=f"DXF parse failed: {exc}")

        entity_types = sorted({entity.dxftype() for entity in entities})
        has_geometry = any(entity_type in entity_types for entity_type in ["LINE", "CIRCLE", "ARC", "LWPOLYLINE"])
        return ToolResult(
            success=has_geometry,
            message="DXF validation passed" if has_geometry else "DXF has no drawable geometry",
            data={
                "file": file,
                "dxf_version": doc.dxfversion,
                "entity_count": len(entities),
                "entity_types": entity_types,
                "expected_summary": expected_summary,
            },
        )
