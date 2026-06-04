from io import StringIO
from math import cos, hypot, radians, sin
from typing import Any, Dict, List, Optional, Tuple

import ezdxf

from app.application.services.cad_service import CADService
from app.domain.external.sandbox import Sandbox
from app.domain.models.cad import CADUnit
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, tool


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
        validation = self._validate_dxf_content(dxf_content, output_path)
        if not validation.success:
            return validation

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
                "validation": validation.data,
            },
        )

    @tool(parse_docstring=True)
    async def cad_generate_dxf_from_spec(
        self,
        entities: List[Dict[str, Any]],
        output_path: str = "/home/ubuntu/output.dxf",
        units: str = "mm",
        layers: Optional[List[Dict[str, Any]]] = None,
        dimensions: Optional[List[Dict[str, Any]]] = None,
        title: str = "CAD drawing",
        dxf_version: str = "R2010",
    ) -> ToolResult:
        """Generate the final DXF file from an explicit structured drawing specification.

        Use this as the last drawing tool after requirements are known. Prefer this over
        prompt-only generation when you can describe the exact geometry.

        Supported entity types:
        - line: {"type":"line","start":[0,0],"end":[100,0],"layer":"M-OBJECT"}
        - circle/hole: {"type":"hole","center":[20,20],"diameter":8}
        - arc: {"type":"arc","center":[0,0],"radius":10,"start_angle":0,"end_angle":90}
        - polyline: {"type":"polyline","points":[[0,0],[10,0],[10,5]],"closed":true}
        - rectangle: {"type":"rectangle","origin":[0,0],"width":100,"height":60,"corner_radius":5}
        - slot: {"type":"slot","center":[50,30],"length":30,"width":10,"rotation":0}
        - text/note: {"type":"text","position":[0,-10],"text":"MATERIAL: AL6061","height":3.5}
        - center_mark: {"type":"center_mark","center":[20,20],"size":8}

        Supported dimension types:
        - linear: {"type":"linear","start":[0,0],"end":[100,0],"offset":10,"text":"100"}
        - diameter/radius: {"type":"diameter","center":[20,20],"radius":4,"text":"DIA 8"}

        Args:
            entities: Explicit drawing entities to create.
            output_path: Absolute sandbox path for the generated DXF file.
            units: Drawing units, usually "mm" or "inch".
            layers: Optional layer definitions with name, color, and linetype.
            dimensions: Optional visible dimension annotations.
            title: Drawing title for metadata/result reporting.
            dxf_version: DXF version, usually "R2010"; ezdxf also supports R2018.
        """
        if not entities:
            return ToolResult(success=False, message="No CAD entities were provided.")

        try:
            dxf_content = self._build_ezdxf_from_spec(
                entities=entities,
                layers=layers or [],
                dimensions=dimensions or [],
                units=units,
                dxf_version=dxf_version,
            )
        except Exception as exc:
            return ToolResult(success=False, message=f"Failed to build DXF from spec: {exc}")

        validation = self._validate_dxf_content(dxf_content, output_path)
        if not validation.success:
            return ToolResult(
                success=False,
                message=validation.message,
                data={"output_path": output_path, "validation": validation.data},
            )

        write_result = await self.sandbox.file_write(
            file=output_path,
            content=dxf_content,
            trailing_newline=False,
        )
        if not write_result.success:
            return ToolResult(
                success=False,
                message=write_result.message or f"Failed to write DXF to {output_path}",
                data={"output_path": output_path, "validation": validation.data},
            )

        return ToolResult(
            success=True,
            message=f"Generated final DXF at {output_path}",
            data={
                "output_path": output_path,
                "title": title,
                "units": units,
                "dxf_version": validation.data.get("dxf_version") if validation.data else dxf_version,
                "input_entity_count": len(entities),
                "input_dimension_count": len(dimensions or []),
                "validation": validation.data,
            },
        )

    @tool(parse_docstring=True)
    async def cad_validate_dxf(
        self,
        file: str,
        expected_summary: str = "",
    ) -> ToolResult:
        """Validate that a generated DXF can be parsed and contains drawing entities.

        Use this after cad_generate_dxf or cad_generate_dxf_from_spec. If validation fails,
        regenerate or report the failure.

        Args:
            file: Absolute sandbox path of the DXF file to validate.
            expected_summary: Short natural-language summary of expected geometry.
        """
        read_result = await self.sandbox.file_read(file=file)
        if not read_result.success:
            return ToolResult(success=False, message=f"Failed to read DXF file: {file}")

        content = (read_result.data or {}).get("content", "")
        return self._validate_dxf_content(content, file, expected_summary)

    def _build_ezdxf_from_spec(
        self,
        entities: List[Dict[str, Any]],
        layers: List[Dict[str, Any]],
        dimensions: List[Dict[str, Any]],
        units: str,
        dxf_version: str,
    ) -> str:
        dxf_doc = ezdxf.new(self._normalize_dxf_version(dxf_version), setup=True)
        dxf_doc.header["$INSUNITS"] = 4 if units == "mm" else 1
        dxf_doc.header["$MEASUREMENT"] = 1 if units == "mm" else 0
        modelspace = dxf_doc.modelspace()

        self._ensure_spec_layers(dxf_doc, layers)
        for entity in entities:
            self._add_spec_entity(modelspace, entity)
        for dimension in dimensions:
            self._add_visible_dimension(modelspace, dimension)

        output = StringIO()
        dxf_doc.write(output)
        return output.getvalue()

    def _ensure_spec_layers(self, dxf_doc, layers: List[Dict[str, Any]]) -> None:
        defaults = [
            {"name": "M-OBJECT", "color": 7, "linetype": "CONTINUOUS"},
            {"name": "M-HOLE", "color": 3, "linetype": "CONTINUOUS"},
            {"name": "M-CENTER", "color": 1, "linetype": "CENTER"},
            {"name": "M-DIM", "color": 2, "linetype": "CONTINUOUS"},
            {"name": "M-NOTE", "color": 4, "linetype": "CONTINUOUS"},
        ]
        for layer in [*defaults, *layers]:
            name = str(layer.get("name", "")).strip()
            if not name or name in dxf_doc.layers:
                continue
            dxf_doc.layers.add(
                name,
                color=int(layer.get("color") or 7),
                linetype=str(layer.get("linetype") or layer.get("line_type") or "CONTINUOUS"),
            )

    def _add_spec_entity(self, modelspace, entity: Dict[str, Any]) -> None:
        entity_type = str(entity.get("type", "")).lower().strip()
        if entity_type == "line":
            self._add_spec_line(modelspace, entity)
        elif entity_type in {"circle", "hole"}:
            default_layer = "M-HOLE" if entity_type == "hole" else "M-OBJECT"
            self._add_spec_circle(modelspace, entity, default_layer)
        elif entity_type == "arc":
            self._add_spec_arc(modelspace, entity)
        elif entity_type == "polyline":
            self._add_spec_polyline(modelspace, entity)
        elif entity_type == "rectangle":
            self._add_spec_rectangle(modelspace, entity)
        elif entity_type == "slot":
            self._add_spec_slot(modelspace, entity)
        elif entity_type in {"text", "note"}:
            self._add_spec_text(modelspace, entity)
        elif entity_type == "center_mark":
            self._add_center_mark(modelspace, entity)
        elif entity_type in {"dimension", "linear_dimension", "diameter_dimension", "radius_dimension"}:
            self._add_visible_dimension(modelspace, entity)
        else:
            raise ValueError(f"Unsupported CAD entity type: {entity_type or 'missing'}")

    def _add_spec_line(self, modelspace, entity: Dict[str, Any]) -> None:
        modelspace.add_line(
            self._point_tuple(entity["start"]),
            self._point_tuple(entity["end"]),
            dxfattribs={"layer": self._layer(entity, "M-OBJECT")},
        )

    def _add_spec_circle(self, modelspace, entity: Dict[str, Any], default_layer: str) -> None:
        radius = self._radius(entity)
        modelspace.add_circle(
            self._point_tuple(entity["center"]),
            radius=radius,
            dxfattribs={"layer": self._layer(entity, default_layer)},
        )
        if entity.get("center_mark"):
            self._add_center_mark(
                modelspace,
                {"center": entity["center"], "size": max(radius * 0.8, 4.0), "layer": "M-CENTER"},
            )

    def _add_spec_arc(self, modelspace, entity: Dict[str, Any]) -> None:
        modelspace.add_arc(
            self._point_tuple(entity["center"]),
            radius=self._radius(entity),
            start_angle=float(entity["start_angle"]),
            end_angle=float(entity["end_angle"]),
            dxfattribs={"layer": self._layer(entity, "M-OBJECT")},
        )

    def _add_spec_polyline(self, modelspace, entity: Dict[str, Any]) -> None:
        points = [self._point_tuple(point) for point in entity["points"]]
        if len(points) < 2:
            raise ValueError("Polyline requires at least two points.")
        modelspace.add_lwpolyline(
            points,
            close=bool(entity.get("closed", False)),
            dxfattribs={"layer": self._layer(entity, "M-OBJECT")},
        )

    def _add_spec_rectangle(self, modelspace, entity: Dict[str, Any]) -> None:
        width = float(entity["width"])
        height = float(entity["height"])
        radius = float(entity.get("corner_radius", 0) or 0)
        layer = self._layer(entity, "M-OBJECT")
        if "origin" in entity:
            x, y = self._point_tuple(entity["origin"])
        elif "center" in entity:
            cx, cy = self._point_tuple(entity["center"])
            x, y = cx - width / 2, cy - height / 2
        else:
            x, y = 0.0, 0.0

        if radius <= 0:
            modelspace.add_lwpolyline(
                [(x, y), (x + width, y), (x + width, y + height), (x, y + height)],
                close=True,
                dxfattribs={"layer": layer},
            )
            return

        radius = min(radius, width / 2, height / 2)
        self._add_raw_line(modelspace, (x + radius, y), (x + width - radius, y), layer)
        self._add_raw_arc(modelspace, (x + width - radius, y + radius), radius, 270, 0, layer)
        self._add_raw_line(modelspace, (x + width, y + radius), (x + width, y + height - radius), layer)
        self._add_raw_arc(modelspace, (x + width - radius, y + height - radius), radius, 0, 90, layer)
        self._add_raw_line(modelspace, (x + width - radius, y + height), (x + radius, y + height), layer)
        self._add_raw_arc(modelspace, (x + radius, y + height - radius), radius, 90, 180, layer)
        self._add_raw_line(modelspace, (x, y + height - radius), (x, y + radius), layer)
        self._add_raw_arc(modelspace, (x + radius, y + radius), radius, 180, 270, layer)

    def _add_spec_slot(self, modelspace, entity: Dict[str, Any]) -> None:
        center = self._point_tuple(entity["center"])
        length = float(entity["length"])
        width = float(entity["width"])
        if length <= 0 or width <= 0:
            raise ValueError("Slot length and width must be positive.")
        length = max(length, width)
        layer = self._layer(entity, "M-HOLE")
        radius = width / 2
        rotation = float(entity.get("rotation", 0) or 0)
        angle = radians(rotation)
        ux = (cos(angle), sin(angle))
        normal = (-sin(angle), cos(angle))
        half_straight = (length - width) / 2
        c1 = (center[0] - ux[0] * half_straight, center[1] - ux[1] * half_straight)
        c2 = (center[0] + ux[0] * half_straight, center[1] + ux[1] * half_straight)
        p1 = (c1[0] + normal[0] * radius, c1[1] + normal[1] * radius)
        p2 = (c2[0] + normal[0] * radius, c2[1] + normal[1] * radius)
        p3 = (c2[0] - normal[0] * radius, c2[1] - normal[1] * radius)
        p4 = (c1[0] - normal[0] * radius, c1[1] - normal[1] * radius)

        self._add_raw_line(modelspace, p1, p2, layer)
        self._add_raw_arc(modelspace, c2, radius, rotation - 90, rotation + 90, layer)
        self._add_raw_line(modelspace, p4, p3, layer)
        self._add_raw_arc(modelspace, c1, radius, rotation + 90, rotation + 270, layer)

        if entity.get("centerline", True):
            self._add_raw_line(modelspace, c1, c2, "M-CENTER")

    def _add_spec_text(self, modelspace, entity: Dict[str, Any]) -> None:
        text_entity = modelspace.add_text(
            str(entity["text"]),
            dxfattribs={
                "layer": self._layer(entity, "M-NOTE"),
                "height": float(entity.get("height", 3.5) or 3.5),
                "rotation": float(entity.get("rotation", 0) or 0),
            },
        )
        text_entity.set_placement(self._point_tuple(entity["position"]))

    def _add_center_mark(self, modelspace, entity: Dict[str, Any]) -> None:
        cx, cy = self._point_tuple(entity["center"])
        size = float(entity.get("size", 6) or 6)
        layer = self._layer(entity, "M-CENTER")
        half = size / 2
        self._add_raw_line(modelspace, (cx - half, cy), (cx + half, cy), layer)
        self._add_raw_line(modelspace, (cx, cy - half), (cx, cy + half), layer)

    def _add_visible_dimension(self, modelspace, dimension: Dict[str, Any]) -> None:
        dimension_type = str(dimension.get("type", "linear")).lower().strip()
        if dimension_type in {"dimension", "linear_dimension"}:
            dimension_type = "linear"
        if dimension_type == "linear":
            self._add_visible_linear_dimension(modelspace, dimension)
        elif dimension_type in {"diameter", "radius", "diameter_dimension", "radius_dimension"}:
            self._add_visible_radial_dimension(modelspace, dimension, dimension_type)
        else:
            raise ValueError(f"Unsupported dimension type: {dimension_type}")

    def _add_visible_linear_dimension(self, modelspace, dimension: Dict[str, Any]) -> None:
        start = self._point_tuple(dimension["start"])
        end = self._point_tuple(dimension["end"])
        layer = self._layer(dimension, "M-DIM")
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        distance = hypot(dx, dy)
        if distance <= 0:
            raise ValueError("Linear dimension start and end points must differ.")
        normal = (-dy / distance, dx / distance)
        offset = float(dimension.get("offset", 8) or 8)
        dim_start = self._point_tuple(dimension["position"]) if "position" in dimension else (
            start[0] + normal[0] * offset,
            start[1] + normal[1] * offset,
        )
        dim_end = (dim_start[0] + dx, dim_start[1] + dy)
        text = str(dimension.get("text") or self._format_number(distance))
        text_height = float(dimension.get("height", 3.5) or 3.5)
        text_position = (
            (dim_start[0] + dim_end[0]) / 2 + normal[0] * text_height,
            (dim_start[1] + dim_end[1]) / 2 + normal[1] * text_height,
        )

        self._add_raw_line(modelspace, start, dim_start, layer)
        self._add_raw_line(modelspace, end, dim_end, layer)
        self._add_raw_line(modelspace, dim_start, dim_end, layer)
        self._add_dimension_tick(modelspace, dim_start, normal, layer)
        self._add_dimension_tick(modelspace, dim_end, normal, layer)
        modelspace.add_text(text, dxfattribs={"layer": layer, "height": text_height}).set_placement(text_position)

    def _add_visible_radial_dimension(self, modelspace, dimension: Dict[str, Any], dimension_type: str) -> None:
        center = self._point_tuple(dimension["center"])
        radius = self._radius(dimension)
        layer = self._layer(dimension, "M-DIM")
        angle = radians(float(dimension.get("angle", 30) or 30))
        end = (center[0] + cos(angle) * radius, center[1] + sin(angle) * radius)
        leader_end = (
            center[0] + cos(angle) * radius * 1.6,
            center[1] + sin(angle) * radius * 1.6,
        )
        prefix = "DIA" if dimension_type.startswith("diameter") else "R"
        measured = radius * 2 if prefix == "DIA" else radius
        text = str(dimension.get("text") or f"{prefix} {self._format_number(measured)}")
        self._add_raw_line(modelspace, center, end, layer)
        self._add_raw_line(modelspace, end, leader_end, layer)
        modelspace.add_text(
            text,
            dxfattribs={"layer": layer, "height": float(dimension.get("height", 3.5) or 3.5)},
        ).set_placement(leader_end)

    def _add_dimension_tick(self, modelspace, point: Tuple[float, float], normal: Tuple[float, float], layer: str) -> None:
        size = 2.5
        tangent = (normal[1], -normal[0])
        self._add_raw_line(
            modelspace,
            (point[0] - tangent[0] * size, point[1] - tangent[1] * size),
            (point[0] + tangent[0] * size, point[1] + tangent[1] * size),
            layer,
        )

    def _add_raw_line(self, modelspace, start: Tuple[float, float], end: Tuple[float, float], layer: str) -> None:
        modelspace.add_line(start, end, dxfattribs={"layer": layer})

    def _add_raw_arc(
        self,
        modelspace,
        center: Tuple[float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        layer: str,
    ) -> None:
        modelspace.add_arc(
            center,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            dxfattribs={"layer": layer},
        )

    def _point_tuple(self, value: Any) -> Tuple[float, float]:
        if isinstance(value, dict):
            return (float(value["x"]), float(value["y"]))
        return (float(value[0]), float(value[1]))

    def _radius(self, value: Dict[str, Any]) -> float:
        if value.get("radius") is not None:
            radius = float(value["radius"])
        elif value.get("diameter") is not None:
            radius = float(value["diameter"]) / 2
        else:
            raise ValueError("Circle, arc, or radial dimension requires radius or diameter.")
        if radius <= 0:
            raise ValueError("Radius must be positive.")
        return radius

    def _layer(self, value: Dict[str, Any], default: str) -> str:
        return str(value.get("layer") or default)

    def _normalize_dxf_version(self, dxf_version: str) -> str:
        version = str(dxf_version or "R2010").upper()
        if version not in {"R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"}:
            return "R2010"
        return version

    def _format_number(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def _validate_dxf_content(
        self,
        content: str,
        file: str,
        expected_summary: str = "",
    ) -> ToolResult:
        try:
            doc = ezdxf.read(StringIO(content))
            entities = list(doc.modelspace())
        except Exception as exc:
            return ToolResult(success=False, message=f"DXF parse failed: {exc}")

        entity_types = sorted({entity.dxftype() for entity in entities})
        entity_count_by_type: Dict[str, int] = {}
        layers = set()
        for entity in entities:
            entity_count_by_type[entity.dxftype()] = entity_count_by_type.get(entity.dxftype(), 0) + 1
            if hasattr(entity.dxf, "layer"):
                layers.add(entity.dxf.layer)
        has_geometry = any(
            entity_type in entity_types
            for entity_type in ["LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT"]
        )
        return ToolResult(
            success=has_geometry,
            message="DXF validation passed" if has_geometry else "DXF has no drawable geometry",
            data={
                "file": file,
                "dxf_version": doc.dxfversion,
                "entity_count": len(entities),
                "entity_types": entity_types,
                "entity_count_by_type": entity_count_by_type,
                "layers": sorted(layers),
                "expected_summary": expected_summary,
            },
        )
