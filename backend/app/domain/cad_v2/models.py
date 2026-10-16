"""CAD IR 2.0: typed design intent; geometry is never stored as executable code."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

def _numeric_or_expression(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("boolean values are not numeric parameters")
    return value


Scalar = Annotated[float | str, BeforeValidator(_numeric_or_expression)]
Vector3 = tuple[Scalar, Scalar, Scalar]
Direction3 = tuple[float, float, float]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class Parameter(StrictModel):
    kind: Literal["length", "angle", "integer", "scalar"] = "length"
    value: Annotated[float, BeforeValidator(_numeric_or_expression)] | None = None
    expression: str | None = Field(default=None, max_length=512)
    editable: bool = True
    label: str | None = Field(default=None, max_length=200)
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> Parameter:
        if (self.value is None) == (self.expression is None):
            raise ValueError("exactly one of value and expression is required")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("parameter min cannot exceed max")
        return self


class SemanticSelector(StrictModel):
    kind: Literal["edge", "face"] = "edge"
    geometry_type: str | None = Field(default=None, max_length=64)
    parallel: Direction3 | None = None
    near: Vector3 | None = None
    center: Vector3 | None = None
    min: Vector3 | None = None
    max: Vector3 | None = None
    tolerance: float = Field(default=1e-4, gt=0, le=10)
    expected_count: int = Field(default=1, ge=1, le=1000)


class BoxParams(StrictModel):
    width: Scalar
    depth: Scalar
    height: Scalar
    origin: Vector3 = (0, 0, 0)


class CylinderParams(StrictModel):
    radius: Scalar
    height: Scalar
    origin: Vector3 = (0, 0, 0)


class SphereParams(StrictModel):
    radius: Scalar
    center: Vector3 = (0, 0, 0)


class RectangleParams(StrictModel):
    width: Scalar
    height: Scalar
    origin: Vector3 = (0, 0, 0)
    plane: Literal["XY", "XZ", "YZ"] = "XY"


class CircleParams(StrictModel):
    radius: Scalar
    origin: Vector3 = (0, 0, 0)
    plane: Literal["XY", "XZ", "YZ"] = "XY"


class PolylineParams(StrictModel):
    points: list[tuple[Scalar, Scalar]] = Field(min_length=3, max_length=1000)
    origin: Vector3 = (0, 0, 0)
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    closed: Literal[True] = True


class BSplineParams(StrictModel):
    points: list[tuple[Scalar, Scalar]] = Field(min_length=3, max_length=100)
    origin: Vector3 = (0, 0, 0)
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    closed: Literal[True] = True


class Path3DParams(StrictModel):
    points: list[Vector3] = Field(min_length=2, max_length=100)
    interpolation: Literal["polyline", "bspline"] = "polyline"
    tangents: tuple[Direction3, Direction3] | None = None

    @model_validator(mode="after")
    def spline_tangents(self) -> Path3DParams:
        if self.tangents is not None and self.interpolation != "bspline":
            raise ValueError("endpoint tangents are supported only for BSpline paths")
        return self


class SweepParams(StrictModel):
    is_frenet: bool = False


class LoftParams(StrictModel):
    ruled: bool = False


class HelicalThreadParams(StrictModel):
    major_diameter: Scalar
    minor_diameter: Scalar
    pitch: Scalar
    length: Scalar
    profile_angle: Scalar = 60
    axis_origin: Vector3 = (0, 0, 0)
    axis_direction: Direction3 = (0, 0, 1)
    internal: bool = False
    left_hand: bool = False


class ExtrudeParams(StrictModel):
    amount: Scalar


class RevolveParams(StrictModel):
    angle: Scalar = 360
    axis_origin: Vector3 = (0, 0, 0)
    axis_direction: Direction3 = (0, 0, 1)


class HoleParams(StrictModel):
    diameter: Scalar
    positions: list[Vector3] = Field(min_length=1, max_length=500)
    depth: Scalar | None = None
    direction: Direction3 = (0, 0, 1)
    through: bool = True

    @model_validator(mode="after")
    def blind_depth(self) -> HoleParams:
        if not self.through and self.depth is None:
            raise ValueError("blind holes require depth")
        return self


class PocketParams(StrictModel):
    depth: Scalar
    direction: Direction3 = (0, 0, 1)


class FilletParams(StrictModel):
    radius: Scalar
    selector: SemanticSelector


class ChamferParams(StrictModel):
    length: Scalar
    selector: SemanticSelector


class BooleanParams(StrictModel):
    pass


class LinearPatternParams(StrictModel):
    count: Scalar
    spacing: Vector3
    fuse: bool = False


class CircularPatternParams(StrictModel):
    count: Scalar
    angle: Scalar = 360
    axis_origin: Vector3 = (0, 0, 0)
    axis_direction: Direction3 = (0, 0, 1)
    fuse: bool = False


class TransformParams(StrictModel):
    translation: Vector3 = (0, 0, 0)
    rotation: Vector3 = (0, 0, 0)


class FeatureBase(StrictModel):
    id: Identifier
    inputs: list[Identifier] = Field(default_factory=list, max_length=500)
    depends_on: list[Identifier] = Field(default_factory=list, max_length=500)
    editable: bool = True
    label: str | None = Field(default=None, max_length=200)
    validation_conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class BoxFeature(FeatureBase):
    type: Literal["Box"]
    params: BoxParams


class CylinderFeature(FeatureBase):
    type: Literal["Cylinder"]
    params: CylinderParams


class SphereFeature(FeatureBase):
    type: Literal["Sphere"]
    params: SphereParams


class RectangleFeature(FeatureBase):
    type: Literal["Rectangle"]
    params: RectangleParams


class CircleFeature(FeatureBase):
    type: Literal["Circle"]
    params: CircleParams


class PolylineFeature(FeatureBase):
    type: Literal["Polyline"]
    params: PolylineParams


class BSplineFeature(FeatureBase):
    type: Literal["BSpline"]
    params: BSplineParams


class Path3DFeature(FeatureBase):
    type: Literal["Path3D"]
    params: Path3DParams


class SweepFeature(FeatureBase):
    type: Literal["Sweep"]
    params: SweepParams = Field(default_factory=SweepParams)


class LoftFeature(FeatureBase):
    type: Literal["Loft"]
    params: LoftParams = Field(default_factory=LoftParams)


class HelicalThreadFeature(FeatureBase):
    type: Literal["HelicalThread"]
    params: HelicalThreadParams


class ExtrudeFeature(FeatureBase):
    type: Literal["Extrude"]
    params: ExtrudeParams


class RevolveFeature(FeatureBase):
    type: Literal["Revolve"]
    params: RevolveParams


class HoleFeature(FeatureBase):
    type: Literal["Hole"]
    params: HoleParams


class PocketFeature(FeatureBase):
    type: Literal["Pocket"]
    params: PocketParams


class FilletFeature(FeatureBase):
    type: Literal["Fillet"]
    params: FilletParams


class ChamferFeature(FeatureBase):
    type: Literal["Chamfer"]
    params: ChamferParams


class UnionFeature(FeatureBase):
    type: Literal["Union"]
    params: BooleanParams = Field(default_factory=BooleanParams)


class CutFeature(FeatureBase):
    type: Literal["Cut"]
    params: BooleanParams = Field(default_factory=BooleanParams)


class IntersectionFeature(FeatureBase):
    type: Literal["Intersection"]
    params: BooleanParams = Field(default_factory=BooleanParams)


class LinearPatternFeature(FeatureBase):
    type: Literal["LinearPattern"]
    params: LinearPatternParams


class CircularPatternFeature(FeatureBase):
    type: Literal["CircularPattern"]
    params: CircularPatternParams


class TransformFeature(FeatureBase):
    type: Literal["Transform"]
    params: TransformParams


Feature = Annotated[Union[
    BoxFeature, CylinderFeature, SphereFeature, RectangleFeature, CircleFeature,
    PolylineFeature, BSplineFeature, Path3DFeature, SweepFeature, LoftFeature,
    HelicalThreadFeature, ExtrudeFeature, RevolveFeature, HoleFeature, PocketFeature,
    FilletFeature, ChamferFeature, UnionFeature, CutFeature, IntersectionFeature,
    LinearPatternFeature, CircularPatternFeature, TransformFeature,
], Field(discriminator="type")]


class ExpectedHole(HoleParams):
    feature_id: Identifier | None = None


class ExpectedGeometry(StrictModel):
    bbox: Vector3 | None = None
    solids: int = Field(default=1, ge=1, le=500)
    volume: Scalar | None = None
    volume_min: Scalar | None = None
    volume_max: Scalar | None = None
    holes: list[ExpectedHole] = Field(default_factory=list, max_length=500)
    tolerance: float = Field(default=1e-4, gt=0, le=10)


class Constraint(StrictModel):
    id: Identifier
    type: Literal["dimension", "volume", "solid_count", "hole", "symmetry", "distance", "custom"]
    feature_id: Identifier | None = None
    expected: Scalar | Vector3 | ExpectedHole | dict[str, Any] | None = None
    axis: Literal["x", "y", "z"] | None = None
    tolerance: float = Field(default=1e-4, gt=0, le=10)
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_hole_payload(self) -> Constraint:
        if self.type == "hole":
            if not isinstance(self.expected, (dict, ExpectedHole)):
                raise ValueError("hole constraints require a typed hole expectation")
            self.expected = ExpectedHole.model_validate(self.expected)
        return self


class AssemblyPart(StrictModel):
    """Reusable, single-solid definition sourced from the feature graph."""
    id: Identifier
    name: str = Field(min_length=1, max_length=200)
    feature_id: Identifier
    color: tuple[float, float, float, float] = (0.35, 0.55, 0.8, 1.0)
    material: str | None = Field(default=None, max_length=200)
    expected_geometry: ExpectedGeometry = Field(default_factory=ExpectedGeometry)

    @field_validator("color")
    @classmethod
    def bounded_color(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not 0 <= channel <= 1 for channel in value):
            raise ValueError("RGBA color channels must lie between zero and one")
        return value


class AssemblyInstance(StrictModel):
    id: Identifier
    part_id: Identifier
    name: str | None = Field(default=None, max_length=200)
    transform: TransformParams = Field(default_factory=TransformParams)
    editable: bool = True


class AssemblyMate(StrictModel):
    """Measured requirements on explicit instance poses, not a free-form solver.

    Fixed compares the complete relative transform. Aligned compares directed
    local axes and the signed plane offset. Concentric compares axis lines;
    rotation about, and translation along, that line remain unconstrained.
    An absent reference instance denotes the world frame.
    """
    id: Identifier
    type: Literal["fixed", "aligned", "concentric"]
    instance_id: Identifier
    reference_instance_id: Identifier | None = None
    origin: Vector3 = (0, 0, 0)
    reference_origin: Vector3 = (0, 0, 0)
    axis: Direction3 = (0, 0, 1)
    reference_axis: Direction3 = (0, 0, 1)
    offset: Scalar = 0
    transform: TransformParams = Field(default_factory=TransformParams)
    tolerance: float = Field(default=1e-4, gt=0, le=1)
    angular_tolerance: float = Field(default=1e-4, gt=0, le=1)


class AssemblyDefinition(StrictModel):
    id: Identifier = "assembly"
    name: str = Field(default="Assembly", min_length=1, max_length=200)
    parts: list[AssemblyPart] = Field(min_length=1, max_length=100)
    instances: list[AssemblyInstance] = Field(min_length=1, max_length=100)
    mates: list[AssemblyMate] = Field(default_factory=list, max_length=300)
    collision_check: bool = True
    collision_tolerance: float = Field(default=1e-7, ge=0, le=1)


class CADDocumentIR(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    document_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    units: Literal["mm", "cm", "m", "inch"] = "mm"
    coordinate_system: Literal["right-handed-Z-up"] = "right-handed-Z-up"
    parameters: dict[str, Parameter] = Field(default_factory=dict, max_length=500)
    features: list[Feature] = Field(min_length=1, max_length=500)
    output_feature: Identifier | None = None
    dependencies: dict[str, list[str]] = Field(default_factory=dict, max_length=500)
    constraints: list[Constraint] = Field(default_factory=list, max_length=1000)
    expected_geometry: ExpectedGeometry = Field(default_factory=ExpectedGeometry)
    assembly: AssemblyDefinition | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def assembly_default_solid_count(self) -> CADDocumentIR:
        # Resolve this before model_dump: the executor serializes typed documents
        # before compiling in a child process, which otherwise loses which fields
        # came from defaults.
        if self.assembly is not None and "solids" not in self.expected_geometry.model_fields_set:
            self.expected_geometry = self.expected_geometry.model_copy(update={"solids": len(self.assembly.instances)})
        return self

    @field_validator("parameters")
    @classmethod
    def validate_parameter_names(cls, value: dict[str, Parameter]) -> dict[str, Parameter]:
        for name in value:
            if name == "pi":
                raise ValueError("pi is a reserved arithmetic constant")
            if not name.isidentifier() or not name.isascii() or name.startswith("_"):
                raise ValueError("parameter names must be public ASCII identifiers")
        return value
