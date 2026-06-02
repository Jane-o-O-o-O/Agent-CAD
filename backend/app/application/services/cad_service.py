from datetime import datetime
from io import StringIO
from math import cos, radians, sin
import re
from typing import Dict, List, Optional

from app.domain.models.cad import (
    CADArc,
    CADCircle,
    CADDimension,
    CADEntity,
    CADLayer,
    CADLine,
    CADNote,
    CADOperation,
    CADOperationResult,
    CADPoint,
    CADPolyline,
    CADSlot,
    CADUnit,
    MechanicalCADDocument,
    MechanicalDesignBrief,
)


DEFAULT_LAYERS = [
    CADLayer(name="M-OBJECT", color=7, line_type="CONTINUOUS"),
    CADLayer(name="M-HOLE", color=3, line_type="CONTINUOUS"),
    CADLayer(name="M-CENTER", color=1, line_type="CENTER"),
    CADLayer(name="M-DIM", color=2, line_type="CONTINUOUS"),
    CADLayer(name="M-NOTE", color=4, line_type="CONTINUOUS"),
]


class CADService:
    def __init__(self) -> None:
        self._documents: Dict[str, MechanicalCADDocument] = {}

    async def create_document(
        self,
        user_id: str,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
        units: CADUnit = CADUnit.MM,
        brief: Optional[MechanicalDesignBrief] = None,
    ) -> MechanicalCADDocument:
        document = MechanicalCADDocument(
            user_id=user_id,
            session_id=session_id,
            title=title or "Untitled mechanical drawing",
            units=units,
            layers=list(DEFAULT_LAYERS),
            brief=brief,
        )
        self._documents[document.id] = document
        return document

    async def get_document(self, document_id: str, user_id: Optional[str] = None) -> Optional[MechanicalCADDocument]:
        document = self._documents.get(document_id)
        if not document:
            return None
        if user_id and document.user_id and document.user_id != user_id:
            return None
        return document

    async def apply_operation(
        self,
        document_id: str,
        operation: CADOperation,
        user_id: Optional[str] = None,
    ) -> CADOperationResult:
        document = await self.get_document(document_id, user_id)
        if not document:
            raise FileNotFoundError("CAD document not found")

        added_entities: List[CADEntity] = []
        op = operation.operation
        params = operation.params

        if op == "create_plate":
            added_entities = self._create_plate(params)
        elif op == "add_circle":
            added_entities = [self._add_circle(params)]
        elif op == "add_hole":
            added_entities = [self._add_hole(params)]
        elif op == "add_slot":
            added_entities = [self._add_slot(params)]
        elif op == "add_centerline":
            added_entities = [self._add_centerline(params)]
        elif op == "add_dimension":
            dimension = self._add_dimension(params)
            document.dimensions.append(dimension)
        elif op == "add_note":
            added_entities = [self._add_note(params)]
        elif op == "delete_entity":
            entity_id = params.get("id")
            document.entities = [entity for entity in document.entities if entity.id != entity_id]
        else:
            raise ValueError(f"Unsupported CAD operation: {op}")

        document.entities.extend(added_entities)
        document.version += 1
        document.updated_at = datetime.utcnow()

        return CADOperationResult(
            success=True,
            document=document,
            added_entity_ids=[entity.id for entity in added_entities],
            message=f"Applied CAD operation: {op}",
        )

    async def apply_operations(
        self,
        document_id: str,
        operations: List[CADOperation],
        user_id: Optional[str] = None,
    ) -> CADOperationResult:
        document = await self.get_document(document_id, user_id)
        if not document:
            raise FileNotFoundError("CAD document not found")

        added_entity_ids: List[str] = []
        for operation in operations:
            result = await self.apply_operation(document_id, operation, user_id)
            added_entity_ids.extend(result.added_entity_ids)

        document = await self.get_document(document_id, user_id)
        return CADOperationResult(
            success=True,
            document=document,
            added_entity_ids=added_entity_ids,
            message=f"Applied {len(operations)} CAD operations",
        )

    async def create_document_from_prompt(
        self,
        user_id: str,
        prompt: str,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
        units: CADUnit = CADUnit.MM,
        attachments: Optional[List[Dict]] = None,
    ) -> CADOperationResult:
        brief, operations, message = self._intake_prompt(prompt, units, attachments or [])
        document = await self.create_document(
            user_id=user_id,
            title=title or self._title_from_brief(brief),
            session_id=session_id,
            units=units,
            brief=brief,
        )
        result = await self.apply_operations(document.id, operations, user_id)
        result.message = message
        return result

    async def plan_from_prompt(
        self,
        prompt: str,
        units: CADUnit = CADUnit.MM,
        attachments: Optional[List[Dict]] = None,
    ) -> tuple[MechanicalDesignBrief, List[CADOperation], str]:
        return self._intake_prompt(prompt, units, attachments or [])

    async def export_dxf(self, document_id: str, user_id: Optional[str] = None) -> str:
        document = await self.get_document(document_id, user_id)
        if not document:
            raise FileNotFoundError("CAD document not found")
        return self._build_minimal_dxf(document)

    def _create_plate(self, params: Dict) -> List[CADEntity]:
        width = float(params["width"])
        height = float(params["height"])
        corner_radius = float(params.get("corner_radius", 0))
        layer = params.get("layer", "M-OBJECT")
        if corner_radius <= 0:
            points = [
                CADPoint(x=0, y=0),
                CADPoint(x=width, y=0),
                CADPoint(x=width, y=height),
                CADPoint(x=0, y=height),
            ]
            return [CADPolyline(layer=layer, points=points, closed=True)]

        radius = min(corner_radius, width / 2, height / 2)
        return [
            CADLine(start=CADPoint(x=radius, y=0), end=CADPoint(x=width - radius, y=0), layer=layer),
            CADArc(center=CADPoint(x=width - radius, y=radius), radius=radius, start_angle=270, end_angle=0, layer=layer),
            CADLine(start=CADPoint(x=width, y=radius), end=CADPoint(x=width, y=height - radius), layer=layer),
            CADArc(center=CADPoint(x=width - radius, y=height - radius), radius=radius, start_angle=0, end_angle=90, layer=layer),
            CADLine(start=CADPoint(x=width - radius, y=height), end=CADPoint(x=radius, y=height), layer=layer),
            CADArc(center=CADPoint(x=radius, y=height - radius), radius=radius, start_angle=90, end_angle=180, layer=layer),
            CADLine(start=CADPoint(x=0, y=height - radius), end=CADPoint(x=0, y=radius), layer=layer),
            CADArc(center=CADPoint(x=radius, y=radius), radius=radius, start_angle=180, end_angle=270, layer=layer),
        ]

    def _add_circle(self, params: Dict) -> CADCircle:
        center = self._point(params["center"])
        radius = float(params.get("radius") or float(params["diameter"]) / 2)
        return CADCircle(center=center, radius=radius, layer=params.get("layer", "M-OBJECT"))

    def _add_hole(self, params: Dict) -> CADCircle:
        center = self._point(params["center"])
        diameter = float(params["diameter"])
        return CADCircle(center=center, radius=diameter / 2, layer=params.get("layer", "M-HOLE"))

    def _add_slot(self, params: Dict) -> CADSlot:
        return CADSlot(
            center=self._point(params["center"]),
            width=float(params["width"]),
            length=float(params["length"]),
            rotation=float(params.get("rotation", 0)),
            layer=params.get("layer", "M-HOLE"),
        )

    def _add_centerline(self, params: Dict) -> CADLine:
        return CADLine(
            start=self._point(params["start"]),
            end=self._point(params["end"]),
            layer=params.get("layer", "M-CENTER"),
        )

    def _add_dimension(self, params: Dict) -> CADDimension:
        return CADDimension(
            type=params.get("type", "linear"),
            start=self._optional_point(params.get("start")),
            end=self._optional_point(params.get("end")),
            position=self._point(params["position"]),
            text=params.get("text"),
            layer=params.get("layer", "M-DIM"),
        )

    def _add_note(self, params: Dict) -> CADNote:
        return CADNote(
            position=self._point(params["position"]),
            text=params["text"],
            height=float(params.get("height", 3.5)),
            layer=params.get("layer", "M-NOTE"),
        )

    def _intake_prompt(self, prompt: str, units: CADUnit, attachments: List[Dict]) -> tuple[MechanicalDesignBrief, List[CADOperation], str]:
        normalized = prompt.lower().replace("×", "x").replace("*", "x")
        width, height, thickness = self._extract_size(normalized)
        corner_radius = self._extract_prefixed_number(normalized, ["r", "圆角"])
        edge_offset = self._extract_edge_offset(normalized)
        hole_count = self._extract_count(normalized, ["孔", "hole"])
        hole_diameter = self._extract_hole_diameter(normalized)
        slot = self._extract_slot(normalized, width, height)

        features: List[Dict] = [
            {
                "type": "base_plate",
                "width": width,
                "height": height,
                "thickness": thickness,
                "corner_radius": corner_radius,
            }
        ]
        operations = [
            CADOperation(
                operation="create_plate",
                params={
                    "width": width,
                    "height": height,
                    "corner_radius": corner_radius,
                },
            ),
            CADOperation(
                operation="add_centerline",
                params={
                    "start": [width / 2, -height * 0.08],
                    "end": [width / 2, height * 1.08],
                },
            ),
            CADOperation(
                operation="add_centerline",
                params={
                    "start": [-width * 0.08, height / 2],
                    "end": [width * 1.08, height / 2],
                },
            ),
            CADOperation(
                operation="add_dimension",
                params={
                    "start": [0, 0],
                    "end": [width, 0],
                    "position": [width / 2, -height * 0.16],
                    "text": f"{self._fmt(width)} {units.value}",
                },
            ),
            CADOperation(
                operation="add_dimension",
                params={
                    "start": [width, 0],
                    "end": [width, height],
                    "position": [width * 1.08, height / 2],
                    "text": f"{self._fmt(height)} {units.value}",
                },
            ),
        ]

        if hole_count > 0:
            hole_centers = self._hole_centers(width, height, hole_count, edge_offset)
            features.append(
                {
                    "type": "hole_pattern",
                    "count": len(hole_centers),
                    "diameter": hole_diameter,
                    "edge_offset": edge_offset,
                    "symmetric": len(hole_centers) in (2, 4),
                }
            )
            for center in hole_centers:
                operations.append(
                    CADOperation(
                        operation="add_hole",
                        params={"center": center, "diameter": hole_diameter},
                    )
                )

        if slot:
            features.append({"type": "slot", **slot})
            operations.append(CADOperation(operation="add_slot", params=slot))

        if thickness:
            operations.append(
                CADOperation(
                    operation="add_note",
                    params={
                        "position": [0, height + max(height * 0.12, 10)],
                        "text": f"THK {self._fmt(thickness)} {units.value}",
                    },
                )
            )

        brief = MechanicalDesignBrief(
            part_type="mounting_plate",
            units=units,
            features=features,
            constraints=["Generated from deterministic mechanical 2D intake MVP"],
            unknowns=[],
            manufacturing_notes=[],
            source_references=[
                {"type": "text", "content": prompt},
                *[
                    {
                        "type": "attachment",
                        "file_id": attachment.get("file_id"),
                        "filename": attachment.get("filename"),
                        "content_type": attachment.get("content_type"),
                    }
                    for attachment in attachments
                ],
            ],
        )
        message = f"Generated a {self._fmt(width)}x{self._fmt(height)} {units.value} mechanical 2D drawing"
        return brief, operations, message

    def _extract_size(self, text: str) -> tuple[float, float, Optional[float]]:
        match = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)(?:\s*x\s*(\d+(?:\.\d+)?))?", text)
        if not match:
            return 120.0, 80.0, None
        width = float(match.group(1))
        height = float(match.group(2))
        thickness = float(match.group(3)) if match.group(3) else None
        return width, height, thickness

    def _extract_prefixed_number(self, text: str, prefixes: List[str]) -> float:
        for prefix in prefixes:
            pattern = rf"{re.escape(prefix)}\s*(\d+(?:\.\d+)?)"
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return 0.0

    def _extract_edge_offset(self, text: str) -> float:
        patterns = [
            r"(?:边缘|边距|edge|offset|from edge|孔距边缘)\s*(\d+(?:\.\d+)?)",
            r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:from edge|edge offset|边距|距边)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return 12.0

    def _extract_count(self, text: str, nouns: List[str]) -> int:
        number_words = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
        for noun in nouns:
            match = re.search(rf"(\d+)\s*(?:个|x|-)?\s*{re.escape(noun)}", text)
            if match:
                return int(match.group(1))
            for word, value in number_words.items():
                if f"{word}个{noun}" in text or f"{word}{noun}" in text:
                    return value
        if any(noun in text for noun in nouns):
            return 4
        return 0

    def _extract_hole_diameter(self, text: str) -> float:
        metric = re.search(r"m\s*(\d+(?:\.\d+)?)", text)
        if metric:
            size = float(metric.group(1))
            return size + 0.5
        diameter = re.search(r"(?:φ|直径|diameter|dia)\s*(\d+(?:\.\d+)?)", text)
        if diameter:
            return float(diameter.group(1))
        return 6.5

    def _extract_slot(self, text: str, width: float, height: float) -> Optional[Dict]:
        if "slot" not in text and "长圆孔" not in text and "槽" not in text:
            return None
        match = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?).{0,8}(?:slot|长圆孔|槽)", text)
        if match:
            a = float(match.group(1))
            b = float(match.group(2))
            length = max(a, b)
            slot_width = min(a, b)
        else:
            length = min(width, height) * 0.38
            slot_width = min(width, height) * 0.12
        return {
            "center": [width / 2, height / 2],
            "length": length,
            "width": slot_width,
            "rotation": 0,
        }

    def _hole_centers(self, width: float, height: float, count: int, edge_offset: float) -> List[List[float]]:
        if count <= 0:
            return []
        if count == 1:
            return [[width / 2, height / 2]]
        if count == 2:
            return [[edge_offset, height / 2], [width - edge_offset, height / 2]]
        return [
            [edge_offset, edge_offset],
            [width - edge_offset, edge_offset],
            [width - edge_offset, height - edge_offset],
            [edge_offset, height - edge_offset],
        ][:count]

    def _title_from_brief(self, brief: MechanicalDesignBrief) -> str:
        if brief.part_type == "mounting_plate":
            return "Mounting plate"
        return "Mechanical drawing"

    def _fmt(self, value: Optional[float]) -> str:
        if value is None:
            return ""
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def _point(self, value) -> CADPoint:
        if isinstance(value, dict):
            return CADPoint(x=float(value["x"]), y=float(value["y"]))
        return CADPoint(x=float(value[0]), y=float(value[1]))

    def _optional_point(self, value) -> Optional[CADPoint]:
        if value is None:
            return None
        return self._point(value)

    def _build_minimal_dxf(self, document: MechanicalCADDocument) -> str:
        output = StringIO()

        def code(value) -> None:
            output.write(f"{value}\n")

        code(0)
        code("SECTION")
        code(2)
        code("HEADER")
        code(9)
        code("$INSUNITS")
        code(70)
        code(4 if document.units == CADUnit.MM else 1)
        code(0)
        code("ENDSEC")
        code(0)
        code("SECTION")
        code(2)
        code("ENTITIES")

        for entity in document.entities:
            self._write_entity(code, entity)

        for dimension in document.dimensions:
            if dimension.text:
                code(0)
                code("TEXT")
                code(8)
                code(dimension.layer)
                code(10)
                code(dimension.position.x)
                code(20)
                code(dimension.position.y)
                code(40)
                code(3.5)
                code(1)
                code(dimension.text)

        code(0)
        code("ENDSEC")
        code(0)
        code("EOF")
        return output.getvalue()

    def _write_entity(self, code, entity: CADEntity) -> None:
        if isinstance(entity, CADLine):
            code(0)
            code("LINE")
            code(8)
            code(entity.layer)
            code(10)
            code(entity.start.x)
            code(20)
            code(entity.start.y)
            code(11)
            code(entity.end.x)
            code(21)
            code(entity.end.y)
        elif isinstance(entity, CADCircle):
            code(0)
            code("CIRCLE")
            code(8)
            code(entity.layer)
            code(10)
            code(entity.center.x)
            code(20)
            code(entity.center.y)
            code(40)
            code(entity.radius)
        elif isinstance(entity, CADArc):
            code(0)
            code("ARC")
            code(8)
            code(entity.layer)
            code(10)
            code(entity.center.x)
            code(20)
            code(entity.center.y)
            code(40)
            code(entity.radius)
            code(50)
            code(entity.start_angle)
            code(51)
            code(entity.end_angle)
        elif isinstance(entity, CADPolyline):
            code(0)
            code("LWPOLYLINE")
            code(8)
            code(entity.layer)
            code(90)
            code(len(entity.points))
            code(70)
            code(1 if entity.closed else 0)
            for point in entity.points:
                code(10)
                code(point.x)
                code(20)
                code(point.y)
        elif isinstance(entity, CADSlot):
            for line in self._slot_as_lines(entity):
                self._write_entity(code, line)
        elif isinstance(entity, CADNote):
            code(0)
            code("TEXT")
            code(8)
            code(entity.layer)
            code(10)
            code(entity.position.x)
            code(20)
            code(entity.position.y)
            code(40)
            code(entity.height)
            code(1)
            code(entity.text)

    def _slot_as_lines(self, slot: CADSlot) -> List[CADLine]:
        angle = radians(slot.rotation)
        dx = cos(angle) * slot.length / 2
        dy = sin(angle) * slot.length / 2
        nx = -sin(angle) * slot.width / 2
        ny = cos(angle) * slot.width / 2
        c = slot.center
        points = [
            CADPoint(x=c.x - dx + nx, y=c.y - dy + ny),
            CADPoint(x=c.x + dx + nx, y=c.y + dy + ny),
            CADPoint(x=c.x + dx - nx, y=c.y + dy - ny),
            CADPoint(x=c.x - dx - nx, y=c.y - dy - ny),
        ]
        return [
            CADLine(start=points[0], end=points[1], layer=slot.layer),
            CADLine(start=points[1], end=points[2], layer=slot.layer),
            CADLine(start=points[2], end=points[3], layer=slot.layer),
            CADLine(start=points[3], end=points[0], layer=slot.layer),
        ]
