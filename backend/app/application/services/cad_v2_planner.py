"""Requirements → a typed, inspectable plan; exact geometry is deterministic."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.domain.cad_v2.compiler import UNIT_TO_MM, compile_ir
from app.domain.cad_v2.errors import ParameterError, RequirementError, SchemaValidationError
from app.domain.cad_v2.models import CADDocumentIR
from app.infrastructure.cad_v2.model_gateway import ModelConfig, StructuredModelGateway

NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
UNIT_NAMES = {"mm": "mm", "毫米": "mm", "cm": "cm", "厘米": "cm", "m": "m", "米": "m", "inch": "inch", "inches": "inch", "英寸": "inch"}
UNIT_PATTERN = r"(?:mm|cm|m|inches|inch|毫米|厘米|米|英寸)"


def _explicit_units(prompt: str) -> set[str]:
    found = re.findall(r"[0-9]\s*(" + UNIT_PATTERN + r")(?![A-Za-z])", prompt, re.I)
    found += re.findall(r"(?:单位|units?)\s*[:：为=]?\s*(" + UNIT_PATTERN + r")(?![A-Za-z])", prompt, re.I)
    return {UNIT_NAMES[value.lower()] for value in found}


def _supported_plate_sentence(prompt: str, holes: bool) -> bool:
    # Fully consume the supported grammar. A keyword blacklist alone can drop an
    # unrecognized boss, offset, count, or other design intent without noticing.
    text = re.sub(r"[\s，,。;；：:、]", "", prompt).rstrip(".")
    n = r"[0-9]+(?:\.[0-9]+)?"
    v = n + "(?:" + UNIT_PATTERN + ")?"
    dims = rf"(?:长(?:度)?(?:为|是|=)?{v}宽(?:度)?(?:为|是|=)?{v}厚(?:度)?(?:为|是|=)?{v}|{n}[x×*]{n}[x×*]{v})"
    prefix = r"(?:(?:请)?(?:帮我)?(?:生成|创建|制作|做)(?:一块|一个)?|一块)?"
    outline = prefix + dims + r"(?:的)?(?:矩形)?(?:安装板|平板|矩形板|plate)"
    if holes:
        outline += rf"(?:在四角)?(?:开|打|设置|添加)?(?:四|4)个直径{v}(?:的)?通孔(?:孔中心的)?水平距离(?:为|是|=)?{v}垂直距离(?:为|是|=)?{v}"
    return re.fullmatch(outline, text, re.I) is not None


def _number(pattern: str, prompt: str) -> float | None:
    match = re.search(pattern, prompt, flags=re.I)
    return float(match.group(1)) if match else None


def _parameter(value: float, label: str) -> dict:
    return {"kind": "length", "value": value, "editable": True, "label": label, "min": 0.001}


def _plate(prompt: str, document_id: str) -> CADDocumentIR | None:
    """A narrow explicit-language route, never silently substitutes another part."""
    if not re.search(r"安装板|平板|矩形板|plate", prompt, re.I) or re.search(r"支架|L\s*型|bracket|法兰|flange", prompt, re.I):
        return None
    # Other features must go through structured planning, never get dropped.
    if re.search(r"圆角|倒角|槽|盲孔|口袋|fillet|chamfer|slot|pocket|blind|凸台|筋", prompt, re.I):
        return None
    declared_units = _explicit_units(prompt)
    if len(declared_units) > 1:
        raise RequirementError("请将本条安装板需求统一为一种单位，或使用明确转换后的 CAD IR；不会猜测混合单位")
    units = next(iter(declared_units), "mm")
    width = _number(r"(?:长(?:度)?|length)\s*(?:为|是|=|:)?\s*" + NUMBER, prompt)
    depth = _number(r"(?:宽(?:度)?|width)\s*(?:为|是|=|:)?\s*" + NUMBER, prompt)
    height = _number(r"(?:厚(?:度)?|thickness)\s*(?:为|是|=|:)?\s*" + NUMBER, prompt)
    triple = re.search(NUMBER + r"\s*[x×*]\s*" + NUMBER + r"\s*[x×*]\s*" + NUMBER, prompt, re.I)
    if triple:
        width, depth, height = map(float, triple.groups())
    if any(value is None for value in (width, depth, height)):
        raise RequirementError("请提供安装板的长度、宽度和厚度 / Plate length, width and thickness are required",
                               details={"missing": [name for name, val in zip(("length", "width", "thickness"), (width, depth, height)) if val is None]})
    if not _supported_plate_sentence(prompt, bool(re.search(r"孔|hole", prompt, re.I))):
        return None
    parameters = {"width": _parameter(width, "长度 / Length"),
                  "depth": _parameter(depth, "宽度 / Width"),
                  "height": _parameter(height, "厚度 / Thickness")}
    features: list[dict[str, Any]] = [{"id": "plate", "type": "Box", "params": {"width": "width", "depth": "depth", "height": "height"}}]
    expected: dict[str, Any] = {"bbox": ["width", "depth", "height"], "solids": 1,
                                "volume": "width*depth*height", "holes": []}
    if re.search(r"孔|hole", prompt, re.I):
        if not re.search(r"(?:四|4)\s*(?:个|处|孔|holes?)|four\s+(?:through\s+)?holes?", prompt, re.I):
            raise RequirementError("规则解析支持明确的四孔安装板；其他孔阵列请使用结构化 IR 或配置模型")
        if not re.search(r"通孔|through", prompt, re.I):
            raise RequirementError("请明确孔为通孔，或提供盲孔深度")
        diameter = _number(r"(?:直径|diameter|[ØøφΦ])\s*(?:为|是|=|:)?\s*" + NUMBER, prompt)
        pitch_x = _number(r"(?:水平距离|水平(?:中心)?距|横向(?:中心)?距|孔距\s*[xX]|x\s*(?:pitch|spacing)|horizontal\s*(?:distance|spacing|pitch))\s*(?:为|是|=|:)?\s*" + NUMBER, prompt)
        pitch_y = _number(r"(?:垂直距离|垂直(?:中心)?距|纵向(?:中心)?距|孔距\s*[yY]|y\s*(?:pitch|spacing)|vertical\s*(?:distance|spacing|pitch))\s*(?:为|是|=|:)?\s*" + NUMBER, prompt)
        if None in (diameter, pitch_x, pitch_y):
            raise RequirementError("请提供四个通孔的直径、水平孔中心距和垂直孔中心距",
                                   details={"missing": [name for name, val in zip(("hole_diameter", "pitch_x", "pitch_y"), (diameter, pitch_x, pitch_y)) if val is None]})
        if diameter <= 0 or pitch_x <= 0 or pitch_y <= 0 or pitch_x + diameter >= width or pitch_y + diameter >= depth:
            raise ParameterError("孔中心距和直径必须使四个独立通孔完全位于板内")
        parameters.update({"hole_diameter": _parameter(diameter, "孔径 / Hole diameter"),
                           "pitch_x": _parameter(pitch_x, "水平孔距 / X pitch"),
                           "pitch_y": _parameter(pitch_y, "垂直孔距 / Y pitch")})
        positions = [[x, y, 0] for x in ("(width-pitch_x)/2", "(width+pitch_x)/2")
                     for y in ("(depth-pitch_y)/2", "(depth+pitch_y)/2")]
        features.append({"id": "mounting_holes", "type": "Hole", "inputs": ["plate"],
                         "params": {"diameter": "hole_diameter", "positions": positions, "through": True}})
        expected["holes"] = [{"feature_id": "mounting_holes", "diameter": "hole_diameter", "positions": positions, "through": True}]
        expected["volume"] = "width*depth*height-4*3.141592653589793*(hole_diameter/2)**2*height"
    return CADDocumentIR.model_validate({"document_id": document_id, "units": units,
                                        "parameters": parameters, "features": features,
                                        "output_feature": features[-1]["id"], "expected_geometry": expected,
                                        "metadata": {"prompt": prompt, "planning_mode": "deterministic-explicit-plate",
                                                     "assumptions": ["four-hole pattern centered on plate"] if len(features) > 1 else []}})


async def plan(prompt: str, document_id: str) -> CADDocumentIR:
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
        raise RequirementError("需求必须为 1–16000 字符 / Requirement length must be 1–16000 characters")
    deterministic = _plate(prompt, document_id)
    if deterministic is not None:
        compile_ir(deterministic)
        return deterministic
    config = ModelConfig.from_env("planner")
    if not config.api_key:
        raise RequirementError("此需求需要结构化模型规划；请配置 CAD_V2_API_KEY 或提交 CAD IR。当前离线解析支持尺寸、孔径及孔距明确的安装板。")
    response = await StructuredModelGateway(config).generate(
        role="planner", request=prompt,
        schema=CADDocumentIR.model_json_schema(),
        instruction=(
            "You plan precise mechanical CAD. Return CAD IR 2.0 only, never Python or shell code. "
            "If a necessary engineering dimension is missing or contradictory return {\"clarification\":\"specific question\"}. "
            "Retain every explicit dimension, hole, position and feature; never simplify the design. "
            "Use editable named length parameters and expressions in feature params AND expected_geometry. "
            "Expressions allow numeric literals, named parameters, pi, and + - * / ** only; no function calls. "
            "Feature order must be topological; inputs refer to earlier stable feature ids. "
            "Box origin is its minimum corner; Cylinder origin is its bottom center, axis +Z. "
            "Rectangle sketch origin is its minimum corner in its plane; Circle origin is its center. "
            "Sketch planes are XY/XZ/YZ. Hole positions are axis points in world coordinates. "
            "Through holes must actually cut the part; specify expected_geometry.holes with measured intent. "
            "Semantic edge selectors require expected_count, direction/type/center and a physical tolerance; never edge indices. "
            "Set expected_geometry.bbox, solids, hole positions/diameters and analytic volume where known. "
            "Do not invent custom constraints that cannot be checked. All numeric coordinates use document units."
        ),
    )
    if response.payload.get("clarification"):
        raise RequirementError(str(response.payload["clarification"])[:2000])
    try:
        ir = CADDocumentIR.model_validate({**response.payload, "document_id": document_id})
    except ValidationError as exc:
        raise SchemaValidationError("模型生成的 CAD IR 不符合 2.0 Schema",
                                    details={"errors": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()[:20]]}) from exc
    compile_ir(ir)
    ir.metadata.update({"prompt": prompt, "planning_mode": "structured-model", "model": response.model,
                        "usage": response.usage, "model_elapsed_seconds": response.elapsed_seconds,
                        "model_attempts": response.attempts})
    return ir


async def edit_prompt(prompt: str, ir: CADDocumentIR | dict) -> dict[str, float]:
    document = ir if isinstance(ir, CADDocumentIR) else CADDocumentIR.model_validate(ir)
    text = re.sub(r"[\s，,。;；：:]", "", prompt)
    match = re.fullmatch(r"(?:请)?(?:将|把)?(四个|4个|所有|全部)孔(?:的)?直径(?:改成|改为|设为|设置为)" + NUMBER + "(" + UNIT_PATTERN + r")?(?:其他保持不变)?", text, re.I)
    if not match:
        raise RequirementError("当前文字编辑支持明确修改全部孔径；子集或多项编辑请使用参数面板或 Feature Patch")
    hole_count = sum(len(feature.params.positions) for feature in document.features if feature.type == "Hole")
    if match.group(1) in {"四个", "4个"} and hole_count != 4:
        raise RequirementError("当前模型的孔数不是四个，请明确要修改的特征")
    diameter = float(match.group(2))
    unit = UNIT_NAMES.get((match.group(3) or "").lower(), document.units)
    diameter = diameter * UNIT_TO_MM[unit] / UNIT_TO_MM[document.units]
    names = {feature.params.diameter for feature in document.features if feature.type == "Hole"}
    if not names or any(not isinstance(name, str) or name not in document.parameters for name in names):
        raise RequirementError("已有孔未关联独立可编辑孔径参数，请使用 Feature Patch")
    if any(not document.parameters[name].editable for name in names):
        raise ParameterError("孔径参数不可编辑")
    return {name: diameter for name in names}
