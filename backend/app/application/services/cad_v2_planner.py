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


async def plan_edit(prompt: str, ir: CADDocumentIR | dict):
    """Plan and validate an atomic natural-language edit against the current IR.

    Returns an EditPlan accepted by apply_edits(**plan.apply_kwargs()). Clarification
    raises RequirementError, so the service leaves the current revision unchanged.
    """
    from app.domain.cad_v2.editing import EditPlan, validate_edit_plan
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
        raise RequirementError("编辑需求必须为 1–16000 字符 / Edit length must be 1–16000 characters")
    document = ir if isinstance(ir, CADDocumentIR) else CADDocumentIR.model_validate(ir)
    compile_ir(document)
    deterministic = _offline_edit(prompt, document)
    if deterministic is not None:
        validate_edit_plan(deterministic, document, prompt)
        return deterministic
    config = ModelConfig.from_env("editor")
    if not config.api_key:
        raise RequirementError("此编辑需要结构化模型规划；请配置 CAD_V2_API_KEY，或明确要修改的特征、尺寸及位置。离线支持安装板孔的多项增删、移动和子集孔径修改。",
                               details={"clarification": "请明确目标特征、操作和必要尺寸，或配置结构化编辑模型"})
    response = await StructuredModelGateway(config).generate(
        role="editor", schema=EditPlan.model_json_schema(),
        request=json.dumps({"edit_request": prompt, "current_ir": document.model_dump(mode="json")}, ensure_ascii=False),
        instruction=(
            "Edit the provided current CAD IR by returning ONLY a bounded EditPlan JSON object, never code or a replacement IR. "
            "Interpret ALL requested operations together as an atomic incremental edit. If target selection, direction, dimensions, "
            "or conflicting requirements are ambiguous, return clarification with a specific question and no geometry mutations. "
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
    try:
        edit = EditPlan.model_validate(response.payload)
    except ValidationError as exc:
        raise SchemaValidationError("模型生成的增量编辑不符合 EditPlan Schema", details={"errors": [
            {"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()[:20]]}) from exc
    edit.planning_mode = "structured-incremental-edit"
    edit.model = response.model
    edit.usage = response.usage
    validate_edit_plan(edit, document, prompt)
    return edit
