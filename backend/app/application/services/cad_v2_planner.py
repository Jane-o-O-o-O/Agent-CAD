"""Requirements → a typed, inspectable plan; exact geometry is deterministic."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.domain.cad_v2.compiler import UNIT_TO_MM, compile_ir
from app.domain.cad_v2.errors import DependencyError, InfrastructureError, ParameterError, RequirementCoverageError, RequirementError, SchemaValidationError
from app.domain.cad_v2.models import CADDocumentIR
from app.domain.cad_v2.requirements import require_coverage
from app.application.services.cad_v2_requirements import deterministic_requirements, extract_requirements
from app.infrastructure.cad_v2.model_gateway import ModelConfig, StructuredModelGateway

NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
UNIT_NAMES = {"mm": "mm", "毫米": "mm", "cm": "cm", "厘米": "cm", "m": "m", "米": "m", "inch": "inch", "inches": "inch", "英寸": "inch"}
UNIT_PATTERN = r"(?:mm|cm|m|inches|inch|毫米|厘米|米|英寸)"


def _explicit_units(prompt: str) -> set[str]:
    found = re.findall(r"[0-9]\s*(" + UNIT_PATTERN + r")(?![A-Za-z])", prompt, re.I)
    found += re.findall(r"(?:单位|units?)\s*[:：为=]?\s*(" + UNIT_PATTERN + r")(?![A-Za-z])", prompt, re.I)
    return {UNIT_NAMES[value.lower()] for value in found}


PLATE_LABELS = {
    "length": (r"长(?:度)?|length", "长度"),
    "width": (r"宽(?:度)?|width", "宽度"),
    "thickness": (r"厚(?:度)?|thickness", "厚度"),
    "hole_diameter": (r"孔(?:的)?直径|孔径|直径|diameter|[ØøφΦ]", "孔径"),
    "pitch_x": (r"(?:孔中心的)?水平距离|水平(?:孔中心|中心|孔)?距|横向(?:中心)?距|孔距[xX]|x(?:pitch|spacing)|horizontal(?:distance|spacing|pitch)", "水平孔中心距"),
    "pitch_y": (r"(?:孔中心的)?垂直距离|垂直(?:孔中心|中心|孔)?距|纵向(?:中心)?距|孔距[yY]|y(?:pitch|spacing)|vertical(?:distance|spacing|pitch)", "垂直孔中心距"),
}


def _conversation(prompt: str, clarification: list[dict[str, str]] | None) -> str:
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
        raise ParameterError("需求必须为 1–16000 字符 / Requirement length must be 1–16000 characters")
    if clarification is None:
        return prompt
    if not isinstance(clarification, list) or len(clarification) > 20 or any(
        not isinstance(turn, dict) or set(turn) != {"question", "answer"}
        or any(not isinstance(turn[key], str) or not turn[key].strip() for key in ("question", "answer"))
        for turn in clarification
    ):
        raise SchemaValidationError("Invalid CAD clarification context")
    if not clarification:
        return prompt
    text = "原始需求：" + prompt
    for turn in clarification:
        text += "\n待澄清问题：" + turn["question"] + "\n用户补充：" + turn["answer"]
    if len(text) > 16000:
        raise ParameterError("澄清后的需求超过 16000 字符 / Clarified requirement exceeds 16000 characters")
    return text


def _response_schema(model) -> dict:
    success = model.model_json_schema()
    definitions = success.pop("$defs", {})
    if "clarification" in success.get("properties", {}):
        success["properties"]["clarification"] = {"type": "null", "default": None}
    question = {
        "type": "object", "additionalProperties": False,
        "properties": {"clarification": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": r"\S"}},
        "required": ["clarification"],
    }
    # Existing model references are absolute JSON pointers into the schema root.
    return {"$defs": definitions, "oneOf": [success, question]}


HOLE_COUNT_NUMBER = r"(?:[0-9]+|[零一二三四五六七八九十两]+)"


def _hole_count_number(value: str) -> int | None:
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value.count("十") == 1:
        before, after = value.split("十")
        if (not before or before in digits) and (not after or after in digits):
            return digits.get(before, 1) * 10 + digits.get(after, 0)
    return None


def _asks_hole_count(question: str) -> bool:
    return bool(re.search(r"孔(?:的)?(?:数量|数目|个数)|(?:几个|多少(?:个)?)孔|孔数|"
                          r"hole\s*count|number\s*of\s*(?:through\s*)?holes|how\s*many\s*(?:through\s*)?holes",
                          question, re.I))


def _corrects_hole_count(fragment: str) -> bool:
    text = re.sub(r"\s", "", fragment)
    correction = r"(?:改成|改为|设为|设置为|更正为|纠正为|最终(?:需要|为|是)?)"
    return bool(re.search(correction + HOLE_COUNT_NUMBER + r"(?:个|处|(?=孔|holes?))|"
                          r"(?:孔(?:的)?(?:数量|数目|个数)|孔数)" + correction + HOLE_COUNT_NUMBER + r"|"
                          r"以" + HOLE_COUNT_NUMBER + r"(?:个|处)?(?:安装孔|通孔|孔|holes?)为准", text, re.I))


def _plate_fields(prompt: str, default_unit: str, question: str = "") -> tuple[dict, set, bool, int | None, bool, bool] | None:
    """Consume every word before admitting the offline grammar, including answers."""
    text = re.sub(r"[\s，,。;；：:、]", "", prompt).rstrip(".")
    fields: dict[str, float] = {}
    conflicts: set[str] = set()
    scalar = r"(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>" + UNIT_PATTERN + r")?"

    def record(key: str, value: str, unit: str | None):
        amount = float(value) * UNIT_TO_MM[UNIT_NAMES.get((unit or "").lower(), default_unit)]
        if key in fields and abs(fields[key] - amount) > 1e-8:
            conflicts.add(key)
        fields[key] = amount

    triple = re.compile(r"([0-9]+(?:\.[0-9]+)?)(" + UNIT_PATTERN + r")?[x×*]([0-9]+(?:\.[0-9]+)?)(" + UNIT_PATTERN + r")?[x×*]([0-9]+(?:\.[0-9]+)?)(" + UNIT_PATTERN + r")?", re.I)
    def dimensions(match):
        values = match.groups()
        shared = values[5]
        for key, value, unit in zip(("length", "width", "thickness"), values[::2], values[1::2]):
            record(key, value, unit or shared)
        return ""
    text = triple.sub(dimensions, text)
    for key, (label, _) in PLATE_LABELS.items():
        pattern = re.compile(r"(?:" + label + r")(?:改成|改为|设为|设置为|为|是|=)?" + scalar, re.I)
        def measure(match, key=key):
            record(key, match.group("value"), match.group("unit"))
            return ""
        text = pattern.sub(measure, text)
    bare = re.fullmatch(scalar, text, re.I)
    asked = [key for key, (_, label) in PLATE_LABELS.items() if label in question]
    if bare and len(asked) == 1 and not _asks_hole_count(question):
        record(asked[0], bare.group("value"), bare.group("unit"))
        text = ""
    has_holes = bool(re.search(r"孔|holes?", text, re.I)) or any(key.startswith("pitch_") or key == "hole_diameter" for key in fields)
    through = bool(re.search(r"通孔|through", text, re.I))
    centered = bool(re.search(r"四角|居中|中心对称|对称布置|centered|centred", text, re.I))
    count = None
    counts: list[int] = []

    def count_holes(match):
        amount = _hole_count_number(match.group("count"))
        if amount is None:
            return match.group(0)
        counts.append(amount)
        return match.groupdict().get("noun") or "孔"

    text = re.sub(r"(?:孔(?:的)?(?:数量|数目|个数)|孔数)(?:改成|改为|设为|设置为|为|是|=)?"
                  r"(?P<count>" + HOLE_COUNT_NUMBER + r")(?:个)?", count_holes, text, flags=re.I)
    text = re.sub(r"(?P<count>" + HOLE_COUNT_NUMBER + r")(?:个|处)?(?:的)?"
                  r"(?P<noun>安装孔|通孔|孔|(?:through)?holes?)", count_holes, text, flags=re.I)
    bare_count = re.fullmatch(r"(?:改成|改为|设为|设置为|更正为|纠正为|最终(?:为|是)?)?"
                             r"(?P<count>" + HOLE_COUNT_NUMBER + r")(?:个|处)?", text)
    other_hole_question = re.search(r"孔为通孔还是盲孔|孔阵列相对板的位置|孔位|坐标|边距|"
                                    r"through\s+or\s+blind|hole\s*(?:position|placement)", question, re.I)
    if bare_count and _asks_hole_count(question) and not asked and not other_hole_question:
        amount = _hole_count_number(bare_count.group("count"))
        if amount is not None:
            counts.append(amount)
            has_holes = True
            text = ""
    if counts:
        if len(set(counts)) != 1:
            conflicts.add("hole_count")
        count = counts[-1]
    elif has_holes and "四角" in text:
        count = 4
    text = re.sub(r"(?:单位|units?)(?:为|是|=)?(?:" + UNIT_PATTERN + r")", "", text, flags=re.I)
    tokens = r"(?:安装板|矩形板|平板|plate|矩形|rectangle|在四角|四角|居中对称|居中|中心对称|对称布置|centered|centred|孔阵列|孔中心的|安装孔|通孔|throughholes?|through|holes?|孔|请|帮我|生成|创建|制作|做|一块|一个|在|开|打|设置|添加|的|并且|以及|并|和|是|为|全部|都是|均为|改成|改为|设为|设置为|更正为|纠正为|最终|以|为准|需要)"
    if not re.fullmatch(tokens + "*", text, re.I):
        return None
    return fields, conflicts, has_holes, count, through, centered


def _plate_requirements(prompt: str, clarification: list[dict[str, str]] | None) -> dict | None:
    if not re.search(r"安装板|平板|矩形板|plate", prompt, re.I):
        return None
    original_units = _explicit_units(prompt)
    default_unit = next(iter(original_units), "mm") if len(original_units) <= 1 else "mm"
    units = _explicit_units(" ".join([prompt] + [turn["answer"] for turn in clarification or []]))
    output_unit = next(iter(units), "mm") if len(units) <= 1 else "mm"
    values: dict[str, float] = {}
    conflicts: set[str] = set()
    holes, count, through, centered = False, None, False, False
    fragments = [(prompt, "")] + [(turn["answer"], turn["question"]) for turn in clarification or []]
    for fragment, question in fragments:
        declared = re.search(r"(?:单位|units?)\s*(?:为|是|=|[:：])?\s*(" + UNIT_PATTERN + r")", fragment, re.I)
        fragment_unit = UNIT_NAMES[declared.group(1).lower()] if declared else default_unit
        parsed = _plate_fields(fragment, fragment_unit, question)
        if parsed is None:
            return None
        fields, duplicates, has_holes, hole_count, is_through, is_centered = parsed
        for key, amount in fields.items():
            resolves = key in conflicts and PLATE_LABELS[key][1] in question or bool(re.search(r"改成|改为|设为|设置为", fragment))
            if key in values and abs(values[key] - amount) > 1e-8 and not resolves:
                conflicts.add(key)
            elif resolves:
                conflicts.discard(key)
            values[key] = amount
        holes |= has_holes
        through |= is_through
        centered |= is_centered
        if hole_count is not None:
            resolves = "hole_count" not in duplicates and (_asks_hole_count(question) or _corrects_hole_count(fragment))
            if count is not None and count != hole_count and not resolves:
                conflicts.add("hole_count")
            elif resolves:
                conflicts.discard("hole_count")
            count = hole_count
        conflicts.update(duplicates)
    if conflicts:
        if conflicts == {"hole_count"}:
            raise RequirementError("孔数量有冲突，请明确需要几个孔", details={"missing": ["hole_count"]})
        labels = {key: label for key, (_, label) in PLATE_LABELS.items()}
        labels["hole_count"] = "孔数量"
        names = "、".join(labels[key] for key in labels if key in conflicts)
        raise RequirementError(f"{names}存在互相矛盾的数值，请明确最终值及单位", details={"missing": sorted(conflicts)})
    if count is not None and not holes:
        return None
    if count not in {None, 4}:
        return None
    missing = [key for key in ("length", "width", "thickness") if key not in values]
    if holes:
        missing += [key for key in ("hole_diameter", "pitch_x", "pitch_y") if key not in values]
        if count is None:
            missing.append("hole_count")
        if not through:
            missing.append("hole_type")
        if not centered:
            missing.append("hole_placement")
    if missing:
        labels = {key: label for key, (_, label) in PLATE_LABELS.items()}
        labels.update({"hole_count": "孔的数量", "hole_type": "孔为通孔还是盲孔（盲孔需深度）",
                       "hole_placement": "孔阵列相对板的位置（可说明居中对称，或给出坐标/边距）"})
        raise RequirementError("请补充安装板的" + "、".join(labels[key] for key in missing) + f"（尺寸请标明单位，当前为 {default_unit}）", details={"missing": missing})
    if any(amount <= 0 for amount in values.values()):
        raise ParameterError("安装板尺寸、孔径及孔中心距必须大于零")
    return {"values": {key: value / UNIT_TO_MM[output_unit] for key, value in values.items()}, "units": output_unit, "holes": holes}


def _parameter(value: float, label: str) -> dict:
    return {"kind": "length", "value": value, "editable": True, "label": label, "min": 0.001}


def _plate(prompt: str, document_id: str, clarification: list[dict[str, str]] | None = None) -> CADDocumentIR | None:
    """A narrow explicit-language route, never silently substitutes another part."""
    requirement = _plate_requirements(prompt, clarification)
    if requirement is None:
        return None
    values, units = requirement["values"], requirement["units"]
    width, depth, height = (values[key] for key in ("length", "width", "thickness"))
    parameters = {"width": _parameter(width, "长度 / Length"),
                  "depth": _parameter(depth, "宽度 / Width"),
                  "height": _parameter(height, "厚度 / Thickness")}
    features: list[dict[str, Any]] = [{"id": "plate", "type": "Box", "params": {"width": "width", "depth": "depth", "height": "height"}}]
    expected: dict[str, Any] = {"bbox": ["width", "depth", "height"], "solids": 1,
                                "volume": "width*depth*height", "holes": []}
    if requirement["holes"]:
        diameter, pitch_x, pitch_y = (values[key] for key in ("hole_diameter", "pitch_x", "pitch_y"))
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
                                        "metadata": {"prompt": prompt, "clarification": clarification or [], "planning_mode": "deterministic-explicit-plate",
                                                     "assumptions": ["four-hole pattern centered on plate"] if len(features) > 1 else []}})


async def plan(prompt: str, document_id: str, *, clarification: list[dict[str, str]] | None = None) -> CADDocumentIR:
    request = _conversation(prompt, clarification)
    deterministic = _plate(prompt, document_id, clarification)
    if deterministic is not None:
        requirements = deterministic_requirements(prompt, clarification=clarification)
        require_coverage(requirements, compile_ir(deterministic), allow_geometry_pending=True)
        deterministic.metadata["requirements"] = requirements.model_dump(mode="json")
        return deterministic
    config = ModelConfig.from_env("planner")
    if not config.api_key:
        raise InfrastructureError("此需求需要结构化模型规划；请配置 CAD_V2_API_KEY 或提交 CAD IR。当前离线解析支持安装板及四孔阵列。")
    requirements = await extract_requirements(prompt, clarification=clarification)
    request = json.dumps({"conversation": request, "requirements": requirements.model_dump(mode="json")}, ensure_ascii=False)
    instruction = (
            "You plan precise mechanical CAD. Return CAD IR 2.0 only, never Python or shell code. "
            "If a necessary engineering dimension is missing or contradictory return {\"clarification\":\"specific question\"}. "
            "Infer only information uniquely determined by the supplied body and functional features. "
            "Ask only missing information that changes geometry, grouped in one specific question. "
            "When the original requirement and clarification answers are sufficient, return the IR immediately. "
            "Resolve all clarification turns together, retain original intent, and do not repeat answered questions. "
            "Retain every explicit dimension, hole, position and feature; never simplify the design. "
            "Use editable named length parameters and expressions in feature params AND expected_geometry. "
            "Expressions allow numeric literals, named parameters, pi, and + - * / ** only; no function calls. "
            "Each arithmetic expression is limited to 512 characters and 64 AST nodes. Split volume formulas "
            "into small named scalar helper parameters (kind scalar) instead of expanding a long expression. "
            "Feature order must be topological; inputs refer to earlier stable feature ids. "
            "Box origin is its minimum corner; Cylinder origin is its bottom center, axis +Z. "
            "Rectangle sketch origin is its minimum corner in its plane; Circle origin is its center. "
            "Sketch planes are XY/XZ/YZ. Hole positions are axis points in world coordinates. "
            "BSpline is a closed periodic 2D profile; Path3D supplies explicit 3D polyline/bspline points. "
            "Sweep inputs are [profile,path]; place its profile at the path start perpendicular to the tangent. "
            "Loft takes 2-12 ordered closed profiles in parallel planes at strictly monotonic stations. "
            "HelicalThread cuts one single-start cylindrical V thread into a body; specify major/minor diameter, "
            "pitch, length, axis_origin/direction, internal and handedness. It does not certify thread standards. "
            "Through holes must actually cut the part; specify expected_geometry.holes with measured intent. "
            "Semantic edge selectors require expected_count, direction/type/center and a physical tolerance; never edge indices. "
            "Set expected_geometry.bbox, solids, hole positions/diameters and analytic volume where known. "
            "Use explicit Hole positions for circular layouts, rather than patterning an already cut body. "
            "Honor the source-backed feature_parameters exactly. For fixed assemblies use world-space native "
            "part origins and identity instance transforms when requirements specify those origins. "
            "Assembly holes should be specified in each part.expected_geometry.holes; top-level hole "
            "assertions must carry the owning feature_id to bind them to that part's native geometry. "
            "Do not invent custom constraints that cannot be checked. All numeric coordinates use document units."
    )
    for attempt in range(2):
        response = await StructuredModelGateway(config).generate(
            role="planner", request=request, schema=_response_schema(CADDocumentIR), instruction=instruction,
        )
        if "clarification" in response.payload:
            question = response.payload["clarification"]
            if not isinstance(question, str) or not question.strip() or len(question) > 2000 or set(response.payload) != {"clarification"}:
                raise SchemaValidationError("模型返回的澄清问题格式无效 / Invalid model clarification")
            raise RequirementError(question.strip(), details={"clarification": question.strip()})
        try:
            ir = CADDocumentIR.model_validate({**response.payload, "document_id": document_id})
        except ValidationError as exc:
            raise SchemaValidationError("模型生成的 CAD IR 不符合 2.0 Schema",
                                        details={"errors": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()[:20]]}) from exc
        try:
            require_coverage(requirements, compile_ir(ir), allow_geometry_pending=True)
            break
        except (ParameterError, DependencyError) as exc:
            if attempt:
                raise
            request = json.dumps({"conversation": _conversation(prompt, clarification),
                                  "requirements": requirements.model_dump(mode="json"),
                                  "previous_ir": ir.model_dump(mode="json"), "compilation_error": exc.to_dict(),
                                  "correction": "Correct the bounded IR compilation error, preserving all source requirements. Split long formulas into named scalar helper parameters."}, ensure_ascii=False)
        except RequirementCoverageError as exc:
            coverage = exc.details["requirement_coverage"]
            if attempt or any(check["status"] == "unverified" and check["verification_stage"] == "plan" for check in coverage["checks"]):
                raise
            request = json.dumps({"conversation": _conversation(prompt, clarification),
                                  "requirements": requirements.model_dump(mode="json"),
                                  "previous_ir": ir.model_dump(mode="json"), "coverage_failures": coverage,
                                  "correction": "Regenerate complete IR satisfying every supplied requirement; do not ask for values already supplied."}, ensure_ascii=False)
    ir.metadata.update({"prompt": prompt, "clarification": clarification or [], "planning_mode": "structured-model", "model": response.model,
                        "usage": response.usage, "model_elapsed_seconds": response.elapsed_seconds,
                        "model_attempts": response.attempts, "coverage_replans": attempt,
                        "requirements": requirements.model_dump(mode="json")})
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


def _edit_clauses(prompt: str) -> list[str]:
    text = re.sub(r"(?:[，,;；]?\s*(?:其他保持不变|其余保持不变|keep everything else unchanged))\s*[。.]?$", "", prompt.strip(), flags=re.I)
    text = text.rstrip("。.")
    return [clause.strip() for clause in re.split(r"\s*(?:[;；]|[，,]\s*(?=(?:并|再|然后|将|把|删除|添加|change|set|move|remove|delete|add))|\s+and\s+(?=change|set|move|remove|delete|add))\s*", text, flags=re.I) if clause.strip()]


def _offline_edit(prompt: str, document: CADDocumentIR):
    """Fully consumed small grammar over a plain plate; unsupported intent goes to the model.

    This route deliberately requires an axial through-hole chain. It never assumes
    that a hole in a transformed or boolean assembly is in the plate's XY frame.
    """
    from copy import deepcopy
    from app.domain.cad_v2.editing import EditPlan
    from app.domain.cad_v2.expressions import evaluate_expression, resolve_parameters

    if document.assembly is not None or document.features[0].type != "Box" or any(feature.type != "Hole" for feature in document.features[1:]):
        return None
    if any(feature.params.direction != (0, 0, 1) or not feature.params.through for feature in document.features[1:]):
        return None
    for index, feature in enumerate(document.features[1:], 1):
        if feature.inputs != [document.features[index - 1].id]:
            return None
    if (document.output_feature or document.features[-1].id) != document.features[-1].id or document.dependencies:
        return None
    features = [feature.model_dump(mode="json") for feature in document.features]
    original = deepcopy(features)
    values = resolve_parameters(document.parameters)
    intents = []
    base = features[0]
    n = r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>" + UNIT_PATTERN + r")?"
    choice = r"(?P<choice>所有|全部|四个|4个|左边两个|左侧两个|右边两个|右侧两个|上面两个|上侧两个|下面两个|下侧两个|左边|左侧|右边|右侧|上面|上侧|下面|下侧|all|four|left two|right two|top two|bottom two|left|right|top|bottom)"
    clauses = _edit_clauses(prompt)
    operations = []
    # Parse every clause first, so a recognized prefix cannot discard a suffix.
    for source in clauses:
        text = re.sub(r"^(?:并且|然后|并|再)", "", source).strip()
        patterns = [
            ("diameter", rf"(?:请)?(?:将|把)?{choice}孔(?:的)?直径(?:改成|改为|设为|设置为)\s*{n}"),
            ("diameter", rf"(?:change|set)\s+(?:the\s+)?{choice}\s+holes?(?:\s+diameters?)?\s+to\s+{n}"),
            ("move", rf"(?:请)?(?:将|把)?{choice}孔向(?P<direction>左|右|上|下)(?:移动|平移)\s*{n}"),
            ("move", rf"move\s+(?:the\s+)?{choice}\s+holes?\s+(?P<direction>left|right|up|down)\s+(?:by\s+)?{n}"),
            ("remove", rf"(?:请)?(?:删除|移除|去掉|去除){choice}孔"),
            ("remove", rf"(?:remove|delete)\s+(?:the\s+)?{choice}\s+holes?"),
            ("add_center", rf"(?:请)?(?:在)?(?:中心|中央)(?:添加|增加|加)(?:一个)?直径\s*{n}\s*(?:的)?通孔"),
            ("add_center", rf"add\s+(?:a\s+)?(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>" + UNIT_PATTERN + r")?\s*(?:diameter\s+)?through\s+hole\s+at\s+(?:the\s+)?center"),
        ]
        match = None
        for action, pattern in patterns:
            match = re.fullmatch(pattern, text, re.I)
            if match:
                operations.append((source, action, match.groupdict()))
                break
        if match is None:
            return None
    if not operations:
        return None

    # Preserve an existing diameter parameter for the common whole-pattern edit.
    # Only do this when compiled non-diameter geometry is unaffected (including
    # indirect parameter expressions); otherwise patch just the hole features.
    if len(operations) == 1 and operations[0][1] == "diameter" and operations[0][2]["choice"].lower() in {"所有", "全部", "四个", "4个", "all", "four"}:
        from app.domain.cad_v2.compiler import apply_edits
        from app.domain.cad_v2.editing import validate_edit_plan
        source, _, match = operations[0]
        names = {feature.params.diameter for feature in document.features[1:]}
        count = sum(len(feature.params.positions) for feature in document.features[1:])
        if names and all(isinstance(name, str) and name in document.parameters for name in names) and (
                match["choice"].lower() not in {"四个", "4个", "four"} or count == 4):
            unit = UNIT_NAMES.get((match.get("unit") or "").lower(), document.units)
            amount = float(match["value"]) * UNIT_TO_MM[unit] / UNIT_TO_MM[document.units]
            parameter_edit = EditPlan.model_validate({"parameters": {name: amount for name in names}, "intents": [
                {"source": source, "action": "modify", "targets": [feature.id for feature in document.features[1:]], "parameters": sorted(names)}],
                "planning_mode": "deterministic-incremental-edit"})
            before = compile_ir(document)
            after = compile_ir(apply_edits(document, **parameter_edit.apply_kwargs()))
            def nondiameter(feature):
                return {key: value for key, value in feature.params.items() if key != "diameter" or feature.type != "Hole"}
            if all(nondiameter(left) == nondiameter(right) for left, right in zip(before.features, after.features)) and (
                    document.expected_geometry.volume is None or isinstance(document.expected_geometry.volume, str)
                    and any(name in document.expected_geometry.volume for name in names)):
                validate_edit_plan(parameter_edit, document, prompt)
                return parameter_edit

    def scalar(value):
        return evaluate_expression(value, values)

    def unique_id(prefix):
        existing = {feature["id"] for feature in features} | {feature["id"] for feature in original}
        candidate, index = prefix[:58], 2
        while candidate in existing:
            candidate = prefix[:55] + "_" + str(index)
            index += 1
        return candidate

    for source, action, match in operations:
        amount = None
        if match.get("value"):
            unit = UNIT_NAMES.get((match.get("unit") or "").lower(), document.units)
            amount = float(match["value"]) * UNIT_TO_MM[unit] / UNIT_TO_MM[document.units]
            if amount <= 0:
                raise ParameterError("Diameter or movement must be positive")
        if action == "add_center":
            feature_id = unique_id("center_hole")
            origin = base["params"]["origin"]
            positions = [[f"({origin[0]})+({base['params']['width']})/2", f"({origin[1]})+({base['params']['depth']})/2", origin[2]]]
            features.append({"id": feature_id, "type": "Hole", "inputs": [features[-1]["id"]],
                             "params": {"diameter": amount, "positions": positions, "through": True}})
            intents.append({"source": source, "action": "add", "targets": [feature_id]})
            continue
        available = [(feature, index, point) for feature in features[1:] for index, point in enumerate(feature["params"]["positions"])]
        selection = match["choice"].lower()
        if not available:
            raise RequirementError("模型没有可选择的孔 / There are no holes to select", details={"clarification": "请指定现有特征"})
        selected = available
        if selection in {"四个", "4个", "four"} and len(available) != 4:
            raise RequirementError("模型的孔数不是四个，请明确目标孔 / The model does not contain exactly four holes")
        sides = {"左": (0, min), "left": (0, min), "右": (0, max), "right": (0, max),
                 "上": (1, max), "top": (1, max), "下": (1, min), "bottom": (1, min)}
        for prefix, (axis, extreme) in sides.items():
            if selection.startswith(prefix):
                edge = extreme(scalar(point[axis]) for _, _, point in available)
                selected = [entry for entry in available if abs(scalar(entry[2][axis]) - edge) < 1e-8]
                if len(selected) == len(available) or ("两个" in selection or "two" in selection) and len(selected) != 2:
                    raise RequirementError("位置描述不能唯一确定所需的孔，请提供特征 ID 或孔坐标 / Specify feature IDs or coordinates for the ambiguous hole selection",
                                           details={"clarification": "请明确目标孔及当前坐标", "matched_holes": len(selected)})
                break
        groups = {}
        for feature, index, _ in selected:
            groups.setdefault(feature["id"], set()).add(index)
        targets = list(groups)
        for feature in list(features[1:]):
            chosen = groups.get(feature["id"])
            if not chosen:
                continue
            positions = feature["params"]["positions"]
            if action == "move":
                direction = match["direction"].lower()
                axis = 0 if direction in {"左", "右", "left", "right"} else 1
                delta = -amount if direction in {"左", "下", "left", "down"} else amount
                for index in chosen:
                    positions[index][axis] = f"({positions[index][axis]})+({delta})"
            elif action == "diameter":
                if len(chosen) == len(positions):
                    feature["params"]["diameter"] = amount
                else:
                    feature["params"]["positions"] = [point for index, point in enumerate(positions) if index not in chosen]
                    feature_id = unique_id(feature["id"] + "_edited")
                    features.append({"id": feature_id, "type": "Hole", "inputs": [features[-1]["id"]],
                                     "params": {**deepcopy(feature["params"]), "diameter": amount,
                                                "positions": [point for index, point in enumerate(positions) if index in chosen]}})
                    targets.append(feature_id)
            elif action == "remove":
                if len(chosen) != len(positions):
                    feature["params"]["positions"] = [point for index, point in enumerate(positions) if index not in chosen]
                else:
                    features.remove(feature)
                    for follower in features:
                        if feature["id"] in follower.get("inputs", []):
                            follower["inputs"] = [feature["inputs"][0] if item == feature["id"] else item for item in follower["inputs"]]
                            if follower["id"] not in targets:
                                targets.append(follower["id"])
        intents.append({"source": source, "action": "modify" if action == "diameter" else action, "targets": targets})
    old = {feature["id"]: feature for feature in original}
    now = {feature["id"]: feature for feature in features}
    # New features subsequently removed in the same request have no net effect.
    for intent in intents:
        intent["targets"] = [target for target in intent["targets"] if target in old or target in now]
    intents = [intent for intent in intents if intent["targets"]]
    patches = [{"id": feature["id"], **{key: feature[key] for key in ("params", "inputs") if feature.get(key) != old[feature["id"]].get(key)}}
               for feature in features if feature["id"] in old and any(feature.get(key) != old[feature["id"]].get(key) for key in ("params", "inputs"))]
    holes = [{"feature_id": feature["id"], **feature["params"]} for feature in features[1:]]
    # Analytic expectation is only valid for disjoint bores fully inside the Box.
    # The geometry validator rejects overlaps or out-of-envelope movements.
    params = base["params"]
    volume = f"({params['width']})*({params['depth']})*({params['height']})"
    for feature in features[1:]:
        volume += f"-{len(feature['params']['positions'])}*pi*(({feature['params']['diameter']})/2)**2*({params['height']})"
    expected = {"holes": holes}
    if document.expected_geometry.volume is not None:
        expected["volume"] = volume
    return EditPlan.model_validate({"feature_patches": patches, "add_features": [feature for feature in features if feature["id"] not in old],
        "delete_features": [feature_id for feature_id in old if feature_id not in now], "output_feature": features[-1]["id"],
        "expected_geometry": expected, "intents": intents, "planning_mode": "deterministic-incremental-edit"})


def _clarified_diameter_edit(prompt: str, clarification: list[dict[str, str]] | None, units: str) -> str | None:
    """Resolve an incomplete diameter command without treating its answer as a new edit."""
    choices = r"所有|全部|四个|4个|左边两个|左侧两个|右边两个|右侧两个|上面两个|上侧两个|下面两个|下侧两个"
    value = r"(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>" + UNIT_PATTERN + r")?"
    text = re.sub(r"[\s，,。;；：:]", "", prompt).rstrip(".")
    match = re.fullmatch(r"(?:请)?(?:将|把)?(?P<choice>" + choices + r")?孔(?:的)?(?:直径|径)(?:(?:改成|改为|设为|设置为)(?:" + value + r")?|改大|改小)?(?:其他保持不变)?", text, re.I)
    if match is None:
        return None
    choice, amount, unit = match.group("choice"), match.group("value"), match.group("unit")
    for turn in clarification or []:
        answer = re.sub(r"[\s，,。;；：:]", "", turn["answer"]).rstrip(".")
        selection = re.fullmatch(r"(?:选|选择|修改)?(" + choices + r")(?:孔)?", answer)
        number = re.fullmatch(r"(?:孔径|直径)?(?:改成|改为|为|是|=)?" + value, answer, re.I)
        if selection:
            choice = selection.group(1)
        elif number:
            amount, unit = number.group("value"), number.group("unit")
        else:
            return None
    missing = []
    if not choice:
        missing.append("目标孔（所有孔、左边两个孔或右边两个孔等）")
    if not amount:
        missing.append("修改后的孔径及单位")
    if missing:
        raise RequirementError("请明确" + "、".join(missing), details={"missing": [
            key for key, absent in (("hole_selection", not choice), ("hole_diameter", not amount)) if absent]})
    return f"将{choice}孔的直径改成{amount}{unit or units}"


async def plan_edit(prompt: str, ir: CADDocumentIR | dict, *, clarification: list[dict[str, str]] | None = None):
    """Plan and validate an atomic natural-language edit against the current IR.

    Returns an EditPlan accepted by apply_edits(**plan.apply_kwargs()). Clarification
    raises RequirementError, so the service leaves the current revision unchanged.
    """
    from app.domain.cad_v2.editing import EditPlan, validate_edit_plan
    request = _conversation(prompt, clarification)
    document = ir if isinstance(ir, CADDocumentIR) else CADDocumentIR.model_validate(ir)
    compile_ir(document)
    effective_prompt = prompt
    deterministic = _offline_edit(prompt, document) if not clarification else None
    if deterministic is None:
        effective_prompt = _clarified_diameter_edit(prompt, clarification, document.units)
        deterministic = _offline_edit(effective_prompt, document) if effective_prompt is not None else None
    if deterministic is not None:
        validate_edit_plan(deterministic, document, effective_prompt)
        return deterministic
    config = ModelConfig.from_env("editor")
    if not config.api_key:
        raise InfrastructureError("此编辑需要结构化模型规划；请配置 CAD_V2_API_KEY。离线支持安装板孔的增删、移动和子集孔径修改。")
    response = await StructuredModelGateway(config).generate(
        role="editor", schema=_response_schema(EditPlan),
        request=json.dumps({"edit_request": request, "current_ir": document.model_dump(mode="json")}, ensure_ascii=False),
        instruction=(
            "Edit the provided current CAD IR by returning ONLY a bounded EditPlan JSON object, never code or a replacement IR. "
            "Interpret ALL requested operations together as an atomic incremental edit. If target selection, direction, dimensions, "
            "or conflicting requirements are ambiguous, return clarification with a specific question and no geometry mutations. "
            "Infer only uniquely determined information and ask only unanswered questions that change geometry. "
            "Once the original request and all answers suffice, return the incremental edit immediately. "
            "The edit_request may contain the original request, a prior clarification question and the user's answer. "
            "Resolve that conversation as one edit against current_ir; treat the answer as clarification of the original intent, "
            "not as a separate request that drops earlier operations. Quote exact spans from this composed edit_request. "
            "Each intent source must be an exact substring of edit_request; action is modify/move/add/remove; targets lists every "
            "affected existing or added feature ID, parameters lists changed parameter names. Preserve stable existing IDs, "
            "types, labels, units, parameter definitions and every unrelated feature and requirement. Parameters may only change "
            "existing editable values or arithmetic expressions. Changing shared parameters requires intent for ALL affected features. "
            "Feature patches contain id and only changed params/inputs/depends_on/label. They cannot replace a whole feature. "
            "add_features uses typed CAD IR features in dependency order; features are appended after existing ones. Never delete "
            "and recreate an existing feature to change its type, or bypass existing geometry by choosing an unrelated output. "
            "For hole subsets keep unselected positions and diameter intact; split a grouped Hole into an existing remainder "
            "and a new Hole applied downstream when different diameters are needed. Use positions in document units, explicit "
            "directions and through/depth values. Preserve parametric expressions wherever possible. "
            "Deleting a feature requires an explicit removal request, rewiring its consumers and updating only associated requirements. "
            "expected_geometry is a partial patch: update analytically affected values and complete hole list, retaining every "
            "unrelated entry; never null existing checks, loosen tolerance or drop constraints. If constraints is supplied preserve "
            "unrelated constraints exactly, including IDs and tolerances. changes_envelope is true only when the user explicitly "
            "changes external dimensions/position. Keep bbox, solids and unchanged checks intact. Updated Hole expectations must "
            "match edited params exactly. Keep existing volume checking with updated analytic expression where possible. "
            "No dimensions may be guessed. Arithmetic uses literals, parameter names, pi and + - * / ** only, never function calls. "
            "Semantic selectors require physical tolerance and expected_count. End output must be fully connected to retained geometry."
            "For existing assemblies use instance_patches with id and partial transform {translation,rotation}, add_instances, "
            "delete_instances, and mate_patches for explicitly revised mate data. Each intent lists affected instance IDs in instances "
            "and mate IDs in mates; targets remains feature IDs only. Retain each part definition, instance ID, mate type and tolerance. "
            "Never swap instance part_id as a shortcut. Instance placement edits preserve all unrelated instance transforms. "
            "Do not weaken or silently retarget fixed/aligned/concentric mates to force a move through validation; ask clarification "
            "if the requested movement conflicts with an existing mate unless the user explicitly requests that mate change. "
            "Assembly collision checks and part geometry expectations remain mandatory."
        ))
    if response.payload.get("clarification") is not None:
        question = response.payload["clarification"]
        if not isinstance(question, str) or not question.strip() or len(question) > 2000 or set(response.payload) != {"clarification"}:
            raise SchemaValidationError("模型返回的澄清问题格式无效 / Invalid model clarification")
        raise RequirementError(question.strip(), details={"clarification": question.strip()})
    try:
        edit = EditPlan.model_validate(response.payload)
    except ValidationError as exc:
        raise SchemaValidationError("模型生成的增量编辑不符合 EditPlan Schema", details={"errors": [
            {"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()[:20]]}) from exc
    edit.planning_mode = "structured-incremental-edit"
    edit.model = response.model
    edit.usage = response.usage
    validate_edit_plan(edit, document, request)
    return edit
