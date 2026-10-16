"""Extract design obligations before asking a separate model to generate CAD IR."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.domain.cad_v2.compiler import UNIT_TO_MM
from app.domain.cad_v2.errors import RequirementError, SchemaValidationError
from app.domain.cad_v2.requirements import GeometryRequirement, RequirementExtraction, RequirementSet
from app.infrastructure.cad_v2.model_gateway import ModelConfig, StructuredModelGateway


NUMBER = r"[0-9]+(?:\.[0-9]+)?"
UNITS = {"mm": "mm", "毫米": "mm", "cm": "cm", "厘米": "cm", "m": "m", "米": "m", "inch": "inch", "inches": "inch", "英寸": "inch"}
UNIT = r"(?:mm|cm|m|inches|inch|毫米|厘米|米|英寸)"
MEASURE = r"(?P<number>" + NUMBER + r")\s*(?P<unit>" + UNIT + r")?"
COUNTS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
          "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
FEATURE_WORDS = {
    "Hole": r"通孔|盲孔|安装孔|螺栓孔|钻孔|\bholes?\b",
    "Fillet": r"圆角|\bfillets?\b", "Chamfer": r"倒角|\bchamfers?\b",
    "Rib": r"加强筋|加强肋|\b(?:reinforcing\s+)?ribs?\b|\bstiffeners?\b",
    "Slot": r"长圆孔|槽孔|长槽|\bslots?\b",
    "Sweep": r"扫掠|\bsweep(?:s|ing)?\b", "Loft": r"放样|\bloft(?:s|ing)?\b",
    "HelicalThread": r"螺纹|\b(?:helical\s+)?threads?\b",
    "BSpline": r"样条|\bb[- ]?splines?\b",
    "Shell": r"抽壳|\bshell(?:ing)?\b", "Surface": r"自由曲面|曲面|\bfreeform\b|\bsurfaces?\b",
    "Gear": r"齿轮|\bgears?\b",
    "Cylinder": r"圆柱|\bcylinders?\b", "Sphere": r"球体|\bspheres?\b",
    "Extrude": r"拉伸|\bextrud(?:e|ed|ing)\b", "Revolve": r"旋转成型|\brevolv(?:e|ed|ing)\b",
}
NEGATION = r"不要|无需|不需要|不增加|不添加|没有|取消|删除|去掉|\bwithout\b|\bno\b|\bdo\s+not\b"


def _negated(text: str, start: int) -> bool:
    prefix = re.split(r"[。；;\n]", text[:start])[-1]
    negatives = list(re.finditer(NEGATION, prefix, re.I))
    if not negatives:
        return False
    tail = prefix[negatives[-1].end():]
    # A coordinated list shares its negation until a new affirmative command.
    return not re.search(r"(?:[，,]|但是|但|而|\bbut\b)\s*[^，,]*?(?:添加|增加|设置|加工|需要|保留|\badd\b|\binclude\b|\bwith\b)", tail, re.I)


def _global_hole_counts(text: str):
    pattern = (r"(?<![A-Za-z0-9])(?P<count>[0-9]+|[零〇一二两三四五六七八九十]|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
               r"\s*(?:个|处)?\s*(?:(?:直径|孔径|diameter)\s*" + NUMBER + r"\s*(?:" + UNIT + r")?\s*(?:的)?\s*)?(?:通孔|盲孔|孔|holes?\b)")
    for match in re.finditer(pattern, text, re.I):
        prefix = text[:match.start()]
        if re.search(r"第\s*$", prefix) or _negated(text, match.start()):
            continue
        clause = re.split(r"[,，;；。\n]", prefix)[-1]
        local = re.search(r"底板|立板|侧板|顶板|每(?:个|块|侧|面)|零件[一二三四五六七八九十0-9]+|\b(?:each|per)\b|\b(?:bottom|top|left|right)\s+plate\b", clause, re.I)
        if local and not re.search(r"总计|总共|合计|总数|一共|\btotal\b", clause, re.I):
            continue
        token = match.group("count").lower()
        yield match, int(token) if token.isdigit() else COUNTS[token]


def _sources(prompt: str, clarification: list[dict[str, str]] | None) -> list[str]:
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
        raise SchemaValidationError("CAD requirement must contain 1-16000 characters")
    if clarification is not None and (not isinstance(clarification, list) or len(clarification) > 20):
        raise SchemaValidationError("Invalid CAD clarification history")
    answers = []
    for turn in clarification or []:
        if (not isinstance(turn, dict) or set(turn) != {"question", "answer"}
                or any(not isinstance(turn[key], str) or not turn[key].strip() for key in ("question", "answer"))):
            raise SchemaValidationError("Invalid CAD clarification turn")
        answers.append(turn["answer"])
    if sum(map(len, [prompt, *answers])) > 16000:
        raise SchemaValidationError("CAD requirement and answers exceed the context limit")
    return [prompt, *answers]


def _unit(text: str, fallback: str = "mm") -> str:
    declared = re.search(r"(?:单位|units?)\s*(?:为|是|=|[:：])?\s*(" + UNIT + r")(?![A-Za-z])", text, re.I)
    if declared:
        return UNITS[declared.group(1).lower()]
    found = {UNITS[match.lower()] for match in re.findall(NUMBER + r"\s*(" + UNIT + r")(?![A-Za-z])", text, re.I)}
    return next(iter(found)) if len(found) == 1 else fallback


def _measurement(match, fallback):
    return float(match.group("number")) * UNIT_TO_MM[UNITS.get((match.group("unit") or "").lower(), fallback)]


def _explicit_requirements(prompt: str, clarification: list[dict[str, str]] | None) -> list[GeometryRequirement]:
    sources = _sources(prompt, clarification)
    facts: dict[tuple, dict] = {}
    conflicts = set()
    original_unit = _unit(prompt)

    def record(key: tuple, index: int, quote: str, **check):
        value = {"source_index": index, "source_quote": quote, **check}
        prior = facts.get(key)
        comparable = {name: value.get(name) for name in ("value", "count", "through", "feature_type")}
        if prior and index == prior["source_index"] and comparable != {name: prior.get(name) for name in comparable}:
            conflicts.add(key)
        elif prior and index > prior["source_index"]:
            conflicts.discard(key)
        facts[key] = value

    for index, text in enumerate(sources):
        fallback = _unit(text, original_unit)
        for feature_type, pattern in FEATURE_WORDS.items():
            matches = list(re.finditer(pattern, text, re.I))
            if not matches:
                continue
            if feature_type == "BSpline" and all(
                re.search(r"路径|\bpath\b", re.split(r"[。；;\n]", text[:match.end()])[-1], re.I)
                for match in matches
            ):
                feature_type = "Path3D"
            affirmative = [match for match in matches if not _negated(text, match.start())]
            key = ("feature", feature_type)
            if not affirmative:
                facts.pop(key, None)
                for old_key in list(facts):
                    if facts[old_key].get("feature_type") == feature_type:
                        facts.pop(old_key)
                        conflicts.discard(old_key)
                clause = re.split(r"[。；;\n]", text[:matches[-1].end()])[-1]
                record(("feature_count", feature_type), index, clause,
                       kind="feature_count", feature_type=feature_type, count=0)
                continue
            facts.pop(("feature_count", feature_type), None)
            record(key, index, affirmative[-1].group(), kind="feature", feature_type=feature_type)

        native_measurements = {
            ("HelicalThread", "major_diameter"): r"螺纹(?:大径|外径)|大径|\bmajor\s*(?:_|\s)diameter",
            ("HelicalThread", "minor_diameter"): r"螺纹(?:小径|底径)|小径|\bminor\s*(?:_|\s)diameter",
            ("HelicalThread", "pitch"): r"螺距|\bthread\s+pitch",
            ("HelicalThread", "length"): r"螺纹(?:的)?长度|\bthread\s+length",
            ("Fillet", "radius"): r"圆角(?:的)?半径|圆角|\bfillet\s+(?:radius\s*)?",
            ("Chamfer", "length"): r"倒角(?:的)?(?:长度|尺寸)?|\bchamfer\s+(?:length\s*)?",
            ("Cylinder", "radius"): r"圆柱(?:凸起)?(?:的)?半径|\bcylinder\s+radius",
            ("Cylinder", "height"): r"圆柱(?:凸起)?(?:的)?(?:高度|高)|\bcylinder\s+height",
            ("Sphere", "radius"): r"球体(?:的)?半径|\bsphere\s+radius",
            ("Extrude", "amount"): r"拉伸(?:的)?(?:高度|长度|距离)|\bextrud(?:e|ed|ing)\s+(?:amount|height|length|distance)",
            ("Revolve", "angle"): r"旋转成型(?:的)?角度|\brevolv(?:e|ed|ing)\s+angle",
        }
        for (feature_type, parameter), label in native_measurements.items():
            if not any(item.get("feature_type") == feature_type for item in facts.values()):
                continue
            for match in re.finditer(r"(?:" + label + r")\s*(?:改成|改为|设为|为|是|=|to)?\s*" + MEASURE, text, re.I):
                if _negated(text, match.start()):
                    continue
                record(("feature_dimension", feature_type, parameter), index, match.group(),
                       kind="feature_dimension", feature_type=feature_type, parameter=parameter,
                       value=float(match.group("number")) if parameter == "angle" else _measurement(match, fallback))
        for feature_type, label in {"Cylinder": r"圆柱(?:凸起)?(?:的)?直径|\bcylinder\s+diameter",
                                    "Sphere": r"球体(?:的)?直径|\bsphere\s+diameter"}.items():
            for match in re.finditer(r"(?:" + label + r")\s*(?:为|是|=)?\s*" + MEASURE, text, re.I):
                record(("feature_dimension", feature_type, "radius"), index, match.group(), kind="feature_dimension",
                       feature_type=feature_type, parameter="radius", value=_measurement(match, fallback)/2)

        # Plain length/width/thickness refer to the first body clause only.
        # Later clauses can describe ribs or tools and must be independently
        # bound by the requirement extractor rather than guessed as body size.
        boundary = min([match.start() for feature_type, pattern in FEATURE_WORDS.items()
                        if feature_type != "Hole" for match in re.finditer(pattern, text, re.I)] or [len(text)])
        part_scope = re.search(r"零件[一二三四五六七八九十0-9]+|水平底板|竖直立板|\bpart\s+[0-9]+\b", text, re.I)
        if part_scope:
            boundary = min(boundary, part_scope.start())
        body_text = text[:boundary]
        dimensions = {"x": r"长(?:度)?|\blength", "y": r"宽(?:度)?|\bwidth", "z": r"厚(?:度)?|\bthickness"}
        for axis, label in dimensions.items():
            for match in re.finditer(r"(?:" + label + r")\s*(?:改成|改为|设为|设置为|为|是|=|to)?\s*" + MEASURE, body_text, re.I):
                record(("body_dimension", axis), index, match.group(), kind="body_dimension", axis=axis, value=_measurement(match, fallback))
        triples = re.finditer(r"(" + NUMBER + r")\s*(" + UNIT + r")?\s*[x×*]\s*(" + NUMBER + r")\s*(" + UNIT + r")?\s*[x×*]\s*(" + NUMBER + r")\s*(" + UNIT + r")?", body_text, re.I)
        for match in triples:
            values = match.groups()
            shared_unit = values[5]
            for axis, number, unit in zip("xyz", values[::2], values[1::2]):
                amount = float(number) * UNIT_TO_MM[UNITS.get((unit or shared_unit or "").lower(), fallback)]
                record(("body_dimension", axis), index, match.group(), kind="body_dimension", axis=axis, value=amount)

        hole_context = bool(re.search(r"孔|\bholes?\b", " ".join(sources[:index+1]), re.I))
        if hole_context:
            for match, count in _global_hole_counts(text):
                record(("hole_count",), index, match.group(), kind="hole_count", count=count)
            for match in re.finditer(r"(?:孔(?:的)?数量|孔数|hole\s*count|number\s+of\s+holes)\s*(?:改为|为|是|=|to)?\s*(?P<count>[0-9]+|[零〇一二两三四五六七八九十])", text, re.I):
                token = match.group("count")
                record(("hole_count",), index, match.group(), kind="hole_count", count=int(token) if token.isdigit() else COUNTS[token])
            for match in re.finditer(r"(?:孔(?:的)?直径|孔径|(?:通孔|盲孔|孔|holes?)\s*(?:直径|diameter))\s*(?:改为|为|是|=|to)?\s*" + MEASURE, text, re.I):
                record(("hole_diameter",), index, match.group(), kind="hole_diameter", value=_measurement(match, fallback))
            for clause in re.finditer(r"[^,，;；。\n]+", text):
                if not re.search(r"孔|\bholes?\b", clause.group(), re.I):
                    continue
                for match in re.finditer(r"(?:直径|diameter|[ØøφΦ])\s*(?:改为|为|是|=|to)?\s*" + MEASURE, clause.group(), re.I):
                    record(("hole_diameter",), index, match.group(), kind="hole_diameter", value=_measurement(match, fallback))
            for match in re.finditer(r"通孔|\bthrough(?:\s+holes?)?\b|盲孔|\bblind(?:\s+holes?)?\b", text, re.I):
                record(("hole_through",), index, match.group(), kind="hole_through", through=bool(re.search(r"通孔|through", match.group(), re.I)))
            for axis, label in {"x": r"(?:孔中心的)?水平距离|水平(?:孔中心|中心|孔)?距|横向(?:中心)?距|孔距[xX]|horizontal\s*(?:distance|spacing|pitch)",
                                "y": r"(?:孔中心的)?垂直距离|垂直(?:孔中心|中心|孔)?距|纵向(?:中心)?距|孔距[yY]|vertical\s*(?:distance|spacing|pitch)"}.items():
                for match in re.finditer(r"(?:" + label + r")\s*(?:改为|为|是|=|to)?\s*" + MEASURE, text, re.I):
                    record(("hole_pitch", axis), index, match.group(), kind="hole_pitch", axis=axis, value=_measurement(match, fallback))

        # A bare numeric answer is meaningful only when exactly one dimension
        # is asked. The question supplies the binding, never its numeric value.
        if index:
            question = (clarification or [])[index-1]["question"]
            scalar = re.fullmatch(r"\s*" + MEASURE + r"\s*", text, re.I)
            asked = [axis for axis, label in dimensions.items() if re.search(label, question, re.I)]
            asks_count = bool(re.search(r"孔(?:的)?数量|孔数|几个孔|多少.*孔|最终孔数|\b(?:hole\s*count|number\s+of\s+holes|how\s+many\s+holes)", question, re.I))
            asks_other = bool(re.search(r"孔径|直径|孔距|中心距|位置|坐标|布置|孔的类型|\b(?:diameter|pitch|position|placement)\b", question, re.I))
            bare_count = re.fullmatch(r"\s*([0-9]+|[零〇一二两三四五六七八九十])\s*(?:个)?\s*", text)
            if bare_count and asks_count and not asked and not asks_other:
                token = bare_count.group(1)
                record(("hole_count",), index, text, kind="hole_count", count=int(token) if token.isdigit() else COUNTS[token])
            if scalar and len(asked) == 1 and not asks_count and not asks_other:
                record(("body_dimension", asked[0]), index, text, kind="body_dimension", axis=asked[0], value=_measurement(scalar, fallback))
            if scalar and hole_context and not asked and not asks_count:
                asks_diameter = bool(re.search(r"孔径|孔(?:的)?直径|\bhole\s+diameter|\bdiameter\s+of\s+(?:the\s+)?holes?", question, re.I))
                pitch_axes = [axis for axis, label in {
                    "x": r"(?:水平|横向|x)(?:方向)?(?:孔)?(?:中心)?距|(?:孔中心的)?水平距离|孔距[xX]|\bhorizontal\s+(?:distance|spacing|pitch)|\bx\s*(?:pitch|spacing)",
                    "y": r"(?:垂直|纵向|y)(?:方向)?(?:孔)?(?:中心)?距|(?:孔中心的)?垂直距离|孔距[yY]|\bvertical\s+(?:distance|spacing|pitch)|\by\s*(?:pitch|spacing)",
                }.items() if re.search(label, question, re.I)]
                unresolved = bool(re.search(r"位置|坐标|布置|孔(?:的)?类型|深度|高度|半径|角度|\b(?:position|placement|depth|height|radius|angle|hole\s+type)\b", question, re.I))
                ambiguous_pitch = bool(re.search(r"孔距|中心距|\bpitch\b|\bspacing\b", question, re.I)) and not pitch_axes
                if int(asks_diameter) + len(pitch_axes) == 1 and not unresolved and not ambiguous_pitch:
                    amount = _measurement(scalar, fallback)
                    if asks_diameter:
                        record(("hole_diameter",), index, text, kind="hole_diameter", value=amount)
                    else:
                        record(("hole_pitch", pitch_axes[0]), index, text, kind="hole_pitch", axis=pitch_axes[0], value=amount)
    if facts.get(("hole_count",), {}).get("count") == 0:
        obsolete = {key for key in facts if key == ("feature", "Hole") or key[0].startswith("hole_") and key != ("hole_count",)}
        for key in obsolete:
            facts.pop(key, None)
        conflicts.difference_update(obsolete)
    if conflicts:
        raise RequirementError("设计参数存在冲突，请明确最终数值 / Conflicting design parameters require their final values",
                               details={"missing": ["/".join(key) for key in sorted(conflicts)]})
    return [GeometryRequirement(id=f"explicit_{index+1}", **fact) for index, fact in enumerate(facts.values())]


def deterministic_requirements(prompt: str, *, clarification: list[dict[str, str]] | None = None) -> RequirementSet:
    sources = _sources(prompt, clarification)
    requirements = _explicit_requirements(prompt, clarification)
    if not requirements:
        raise RequirementError("请补充可确定几何结果的主体尺寸和特征 / Supply measurable body dimensions and features")
    return RequirementSet(raw_request=prompt, answers=sources[1:], requirements=requirements)


def requirement_response_schema() -> dict[str, Any]:
    success = RequirementExtraction.model_json_schema()
    definitions = success.pop("$defs", {})
    question = {"type": "object", "additionalProperties": False,
                "properties": {"clarification": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": r"\S"}},
                "required": ["clarification"]}
    return {"$defs": definitions, "oneOf": [success, question]}


async def extract_requirements(
    prompt: str, *, clarification: list[dict[str, str]] | None = None,
    gateway: StructuredModelGateway | None = None,
) -> RequirementSet:
    sources = _sources(prompt, clarification)
    mandatory = _explicit_requirements(prompt, clarification)
    gateway = gateway or StructuredModelGateway(ModelConfig.from_env("requirements"))
    request = {"raw_request": prompt, "clarification": clarification or [],
               "mandatory_explicit_checks": [item.model_dump(mode="json") for item in mandatory]}
    instruction = (
            "Independently extract every geometric requirement from the user request BEFORE CAD generation. "
            "You do not see generated IR and cannot claim a requirement is satisfied. "
            "Return requirements, each with an exact verbatim source_quote from raw_request (source_index 0) "
            "or the corresponding user answer (source_index 1 onward); questions are not user specifications. "
            "Latest answers resolve the parameter explicitly being clarified; preserve all other original requirements. "
            "Check kinds: feature (typed operation), feature_count, feature_dimension (direct numeric parameter), "
            "body_dimension (overall envelope axis x/y/z), hole_count, hole_diameter, hole_through, hole_pitch, "
            "feature_position (explicit origin), unsupported (supplied intent has no deterministic representation). "
            "Lengths and positions must use millimeters; angles degrees and counts integers. "
            "Do not infer body axes or parameter associations when ambiguous: return clarification with only "
            "the missing information that changes geometry. Do not invent dimensions. Exclude cosmetic or "
            "functional descriptions only when they impose no geometry requirement. Include every explicitly "
            "requested feature even if unsupported: Rib, Slot, Shell, Gear and Surface cannot be certified "
            "from labels on generic booleans. Supported operation types include Box, Cylinder, Sphere, Rectangle, "
            "Circle, Polyline, Extrude, Revolve, Hole, Pocket, Fillet, Chamfer, Union, Cut, Intersection, Transform, "
            "LinearPattern, CircularPattern, BSpline, Path3D, Sweep, Loft and HelicalThread. "
            "For a sweep include verifiable profile and path requirements; for a loft include profile count/positions; "
            "for thread include major_diameter, minor_diameter, pitch, length when supplied. "
            "scope single requires a uniquely bound typed feature, scope all asserts every feature of that type. "
            "Prefer feature_parameters with a params object for each fully specified feature: compare a "
            "unique matching typed operation using native numeric, boolean, string and ordered array parameters. "
            "This binds multiple same-type features without relying on generated IDs or labels. "
            "Native params: Box width=X, depth=Y, height=Z, origin=minimum corner; Cylinder radius,height,origin "
            "and implicit axis_direction=[0,0,1]; Circle radius,origin,plane; Hole diameter,positions,direction,through; "
            "Path3D points,interpolation='bspline' or 'polyline',tangents (normalized endpoint vectors); "
            "Sweep is_frenet; Loft ruled; HelicalThread major_diameter,minor_diameter,pitch,length,profile_angle, "
            "axis_origin,axis_direction,internal,left_hand. A single-start thread is native to HelicalThread. "
            "Use feature_parameters for explicit positions, directions, path points, tangents, profile planes, "
            "loft section origins and radii, and boolean flags instead of unsupported. "
            "Use feature_inputs for ordered relationships: feature_type='Loft', inputs=[{feature_type:'Circle', "
            "params:{radius:...,origin:[...],plane:'XY'}}, ...] preserves EVERY specified profile in order; "
            "Sweep inputs are profile then Path3D. This checks the actual operation inputs rather than "
            "merely the presence of disconnected profiles. Ordered loft section retention is supported, "
            "so express it as feature_inputs, not unsupported. "
            "Do not require BSpline for a 3D interpolation path: use Path3D with interpolation='bspline'. "
            "Represent no extra features as allowed_features with feature_types listing only the operations "
            "needed for the specified construction, accompanied by exact counts/parameter checks. "
            "Use solid_count for the required number of closed solids, verified after kernel execution. "
            "Use assembly_count for N distinct parts, each instantiated once; it requires collision validation. "
            "Fixed assembly positions can be native part origins with identity instance transforms; preserve "
            "the distinct geometric specifications using feature_parameters. Assembly count and solid count "
            "also cover unfused identities and absence of penetration via mandatory geometry validation. "
            "Use the established plate convention length=X,width=Y,thickness=Z unless the user specifies "
            "otherwise. The mandatory_explicit_checks state already grounded bindings. Ask for genuinely "
            "missing values, retaining every answered value and convention. "
            "Use only the fields allowed for each kind in the schema. In particular hole_* checks do not "
            "accept feature_type, feature_position does not accept parameter, and unsupported only accepts "
            "an optional feature_type. Represent forbidden operations as feature_count with count 0; "
            "never turn 'no chamfer or fillet' into positive features. Ordinals such as 'first hole' "
            "are not counts; local group counts are not global totals. "
            "Unmeasurable supplied geometry must be unsupported, never omitted. Return only the bounded JSON object."
    )
    for attempt in range(2):
        response = await gateway.generate(role="requirements", request=json.dumps(request, ensure_ascii=False),
                                          schema=requirement_response_schema(), instruction=instruction)
        payload = response.payload
        if "clarification" in payload:
            if set(payload) != {"clarification"} or not isinstance(payload["clarification"], str) or not 1 <= len(payload["clarification"].strip()) <= 2000:
                raise SchemaValidationError("Independent requirement extraction returned an invalid question")
            raise RequirementError(payload["clarification"].strip())
        try:
            extracted = RequirementExtraction.model_validate(payload)
            RequirementSet(raw_request=prompt, answers=sources[1:], requirements=extracted.requirements,
                           extraction_mode="independent-model", model=response.model)
        except ValidationError as exc:
            if attempt:
                raise SchemaValidationError("Independent requirement extraction returned ungrounded or invalid checks") from exc
            request["invalid_response"] = payload
            request["validation_errors"] = exc.errors(include_url=False, include_context=False, include_input=False)
            instruction += (" Correct the invalid response using the validation errors and original sources. "
                            "Preserve every user obligation; never drop a check to evade validation. "
                            "Source quotes must be copied verbatim, including punctuation and spacing.")
            continue
        break
    try:
        def same_obligation(left, right):
            return (left.kind, left.axis, left.feature_type, left.parameter) == (right.kind, right.axis, right.feature_type, right.parameter)
        # A final answer replaces just the obligation it resolves. An extractor
        # must not resurrect a stale original value that deterministic parsing
        # already grounded in a later answer.
        current = [item for item in extracted.requirements
                   if not any(same_obligation(item, guard) and guard.source_index > item.source_index for guard in mandatory)]
        zero_holes = next((guard for guard in mandatory if guard.kind == "hole_count" and guard.count == 0), None)
        if zero_holes is not None:
            current = [item for item in current if not (
                item.source_index <= zero_holes.source_index
                and (item.feature_type == "Hole" or item.kind.startswith("hole_") and item.kind != "hole_count"
                     or item.kind == "hole_count" and item.count != 0))]
        for index, answer in enumerate(sources[1:], 1):
            removed = {feature_type for feature_type, pattern in FEATURE_WORDS.items()
                       for match in re.finditer(pattern, answer, re.I)
                       if _negated(answer, match.start())}
            current = [item for item in current if not (item.feature_type in removed and item.source_index < index)]
        extracted_requirements = [item.model_copy(update={"id": f"model_{index+1}"}) for index, item in enumerate(current)]
        result = RequirementSet(raw_request=prompt, answers=sources[1:], requirements=[*mandatory, *extracted_requirements],
                                extraction_mode="independent-model", model=response.model)
    except ValidationError as exc:
        raise SchemaValidationError("Independent requirement extraction returned ungrounded or invalid checks") from exc
    return result
