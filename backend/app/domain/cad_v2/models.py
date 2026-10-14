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
    PolylineFeature, ExtrudeFeature, RevolveFeature, HoleFeature, PocketFeature,
    FilletFeature, ChamferFeature, UnionFeature, CutFeature, IntersectionFeature,
    LinearPatternFeature, CircularPatternFeature, TransformFeature,
], Field(discriminator="type")]


class ExpectedHole(StrictModel):
    diameter: Scalar
    positions: list[Vector3] = Field(min_length=1, max_length=500)
    direction: Direction3 = (0, 0, 1)
    through: bool = True
    depth: Scalar | None = None
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
    expected: Scalar | Vector3 | dict[str, Any] | None = None
    axis: Literal["x", "y", "z"] | None = None
    tolerance: float = Field(default=1e-4, gt=0, le=10)
    description: str | None = Field(default=None, max_length=1000)


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
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def validate_parameter_names(cls, value: dict[str, Parameter]) -> dict[str, Parameter]:
        for name in value:
            if not name.isidentifier() or not name.isascii() or name.startswith("_"):
                raise ValueError("parameter names must be public ASCII identifiers")
        return value
