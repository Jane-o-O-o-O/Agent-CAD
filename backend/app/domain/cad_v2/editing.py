"""Constrained incremental edit plans and preservation checks.

The planner can describe bounded patches, never a replacement document or code.
Every mutation is attached to a quoted request span and checked before compilation.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

from pydantic import Field, model_validator

from .compiler import apply_edits, compile_ir
from .errors import RequirementError
from .expressions import expression_dependencies
from .models import (AssemblyInstance, CADDocumentIR, Constraint, Direction3, Feature,
                     Identifier, Scalar, StrictModel, Vector3)


class EditIntent(StrictModel):
    source: str = Field(min_length=1, max_length=2000)
    action: Literal["modify", "move", "add", "remove"]
    targets: list[Identifier] = Field(default_factory=list, max_length=100)
    parameters: list[str] = Field(default_factory=list, max_length=100)
    instances: list[Identifier] = Field(default_factory=list, max_length=100)
    mates: list[Identifier] = Field(default_factory=list, max_length=100)
    changes_envelope: bool = False


class ParameterPatch(StrictModel):
    value: float | None = None
    expression: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def one_value(self):
        if (self.value is None) == (self.expression is None):
            raise ValueError("exactly one parameter value or expression is required")
        return self


class FeaturePatch(StrictModel):
    id: Identifier
    params: dict[str, Any] | None = Field(default=None, max_length=30)
    inputs: list[Identifier] | None = Field(default=None, max_length=500)
    depends_on: list[Identifier] | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=200)


class TransformPatch(StrictModel):
    translation: Vector3 | None = None
    rotation: Vector3 | None = None


class InstancePatch(StrictModel):
    id: Identifier
    transform: TransformPatch | None = None
    name: str | None = Field(default=None, max_length=200)


class MatePatch(StrictModel):
    id: Identifier
    origin: Vector3 | None = None
    reference_origin: Vector3 | None = None
    axis: Direction3 | None = None
    reference_axis: Direction3 | None = None
    offset: Scalar | None = None
    transform: TransformPatch | None = None


class EditPlan(StrictModel):
    parameters: dict[str, Scalar | ParameterPatch] = Field(default_factory=dict, max_length=500)
    feature_patches: list[FeaturePatch] = Field(default_factory=list, max_length=500)
    add_features: list[Feature] = Field(default_factory=list, max_length=100)
    delete_features: list[Identifier] = Field(default_factory=list, max_length=100)
    output_feature: Identifier | None = None
    expected_geometry: dict[str, Any] | None = Field(default=None, max_length=8)
    constraints: list[Constraint] | None = Field(default=None, max_length=1000)
    instance_patches: list[InstancePatch] = Field(default_factory=list, max_length=100)
    add_instances: list[AssemblyInstance] = Field(default_factory=list, max_length=100)
    delete_instances: list[Identifier] = Field(default_factory=list, max_length=100)
    mate_patches: list[MatePatch] = Field(default_factory=list, max_length=300)
    intents: list[EditIntent] = Field(default_factory=list, max_length=100)
    clarification: str | None = Field(default=None, max_length=2000)
    planning_mode: str = "structured-edit"
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)

    def apply_kwargs(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        return {
            "parameters": data["parameters"],
            "feature_patches": {patch["id"]: {k: v for k, v in patch.items() if k != "id"}
                                for patch in data["feature_patches"]},
            "add_features": data["add_features"], "delete_features": data["delete_features"],
            "output_feature": self.output_feature,
            "expected_geometry": self.expected_geometry,
            "constraints": data.get("constraints"),
            "instance_patches": {patch["id"]: {k: v for k, v in patch.items() if k != "id"}
                                 for patch in data["instance_patches"]},
            "add_instances": data["add_instances"], "delete_instances": data["delete_instances"],
            "mate_patches": {patch["id"]: {k: v for k, v in patch.items() if k != "id"}
                             for patch in data["mate_patches"]},
        }

    def metadata(self) -> dict[str, Any]:
        return {"planning_mode": self.planning_mode, "intents": [item.model_dump() for item in self.intents],
                "model": self.model, "usage": self.usage}


def _references(value: Any) -> set[str]:
    if isinstance(value, str):
        try:
            return expression_dependencies(value)
        except Exception:
            return set()
    if isinstance(value, dict):
        return set().union(*(_references(item) for item in value.values())) if value else set()
    if isinstance(value, (tuple, list)):
        return set().union(*(_references(item) for item in value)) if value else set()
    return set()


def _reject(message: str, **details) -> None:
    raise RequirementError(message, details={"clarification": message, **details})


def _preserve_unselected_holes(plan: EditPlan, document: CADDocumentIR, candidate: CADDocumentIR) -> None:
    """Check individual bores, not just a grouped Hole feature's broad identity."""
    before = compile_ir(document)
    after = compile_ir(candidate)
    holes = {feature.id: feature for feature in before.features if feature.type == "Hole"}
    permitted: set[tuple[str, int]] = set()
    for intent in plan.intents:
        selected_features = [holes[target] for target in intent.targets if target in holes]
        entries = [(feature, index, point) for feature in selected_features
                   for index, point in enumerate(feature.params["positions"])]
        if not entries:
            continue
        side = re.search(r"(左边|左侧|右边|右侧|上面|上侧|下面|下侧|left|right|top|bottom)\s*(?:(两个|两|2|two)\s*)?(?:个)?\s*(?:孔|holes?)", intent.source, re.I)
        if side:
            direction = side.group(1).lower()
            axis = 0 if direction in {"左边", "左侧", "右边", "右侧", "left", "right"} else 1
            extreme = min if direction in {"左边", "左侧", "下面", "下侧", "left", "bottom"} else max
            edge = extreme(point[axis] for _, _, point in entries)
            selected = [entry for entry in entries if abs(entry[2][axis] - edge) < 1e-8]
            if len(selected) == len(entries) or side.group(2) and len(selected) != 2:
                _reject("The requested hole subset is ambiguous; specify feature IDs and coordinates")
            entries = selected
        elif re.search(r"(?:两个|两|2|two)\s*(?:个)?\s*(?:孔|holes?)", intent.source, re.I) and len(entries) != 2:
            _reject("The request names two holes but does not uniquely identify which two")
        permitted.update((feature.id, index) for feature, index, _ in entries)

    def signature(feature, point):
        params = feature.params
        return (round(params["diameter"], 8), tuple(round(value, 8) for value in point),
                params["through"], params.get("depth"), tuple(params["direction"]))

    preserved = Counter(signature(feature, point) for feature in holes.values()
                        for index, point in enumerate(feature.params["positions"])
                        if (feature.id, index) not in permitted)
    actual = Counter(signature(feature, point) for feature in after.features if feature.type == "Hole"
                     for point in feature.params["positions"])
    if preserved - actual:
        _reject("The edit changes holes outside the requested subset")


def validate_edit_plan(plan: EditPlan, document: CADDocumentIR, prompt: str) -> CADDocumentIR:
    """Reject undeclared changes and weakened requirements before running the kernel."""
    if plan.clarification:
        _reject(plan.clarification, intents=[item.model_dump() for item in plan.intents])
    if not plan.intents:
        _reject("请明确要修改的特征与尺寸 / Specify the features and dimensions to edit")
    old = {feature.id: feature for feature in document.features}
    added = {feature.id: feature for feature in plan.add_features}
    patched = {patch.id for patch in plan.feature_patches}
    removed = set(plan.delete_features)
    if len(patched) != len(plan.feature_patches) or len(added) != len(plan.add_features) or len(removed) != len(plan.delete_features):
        _reject("Edit contains repeated feature IDs")
    if set(added) & set(old) or removed & patched:
        _reject("An edit cannot replace or reuse an existing feature ID")
    if (patched | removed) - set(old):
        _reject("Edit references an unknown existing feature")
    if not any((plan.parameters, patched, added, removed, plan.instance_patches,
                plan.add_instances, plan.delete_instances)):
        _reject("Edit contains no geometry changes")
    targets, parameter_names = set(), set()
    removable, addable, scoped_instances, scoped_mates = set(), set(), set(), set()
    removable_instances, addable_instances = set(), set()
    for intent in plan.intents:
        if intent.source not in prompt:
            _reject("Each edit intent must quote an exact span of the user request")
        if not any((intent.targets, intent.parameters, intent.instances, intent.mates)):
            _reject("Edit intent has no explicit targets")
        if set(intent.targets) - (set(old) | set(added)):
            _reject("Edit intent references an unknown feature")
        targets.update(intent.targets)
        parameter_names.update(intent.parameters)
        scoped_instances.update(intent.instances)
        scoped_mates.update(intent.mates)
        if intent.action == "add":
            addable.update(intent.targets)
            addable_instances.update(intent.instances)
        if intent.action == "remove":
            if not re.search(r"删除|移除|去掉|去除|取消|remove|delete|eliminate", intent.source, re.I):
                _reject("Deleting a feature requires an explicit removal request")
            removable.update(intent.targets)
            removable_instances.update(intent.instances)
    if (patched | removed | set(added)) - targets or set(plan.parameters) - parameter_names:
        _reject("Edit changes geometry outside its declared intent")
    # Subset diameter edits may split a Hole into two features while retaining its
    # old ID. All other deletions must be explicitly requested.
    if removed - removable:
        _reject("The request does not explicitly authorize deleting these features", features=sorted(removed - removable))
    for feature_id in set(added) - addable:
        feature = added[feature_id]
        # A changed diameter may split a grouped bore while keeping its original
        # ID and all original centers. It does not authorize arbitrary additions.
        parents = [old[target] for intent in plan.intents if feature_id in intent.targets and intent.action == "modify"
                   for target in intent.targets if target in old and old[target].type == "Hole"]
        also_moved = any(feature_id in intent.targets and intent.action == "move" for intent in plan.intents)
        if feature.type != "Hole" or not any(
                all(point in parent.params.positions for point in feature.params.positions)
                or also_moved and len(feature.params.positions) < len(parent.params.positions)
                for parent in parents):
            _reject("Adding new geometry requires an explicit add intent", feature=feature_id)
    for patch in plan.feature_patches:
        actions = {intent.action for intent in plan.intents if patch.id in intent.targets}
        previous_params = old[patch.id].params.model_dump(mode="json")
        changed_fields = {key for key, value in (patch.params or {}).items() if value != previous_params.get(key)}
        if actions == {"move"} and changed_fields - {"positions", "origin", "center", "axis_origin", "translation", "rotation"}:
            _reject("A movement intent cannot change feature dimensions", feature=patch.id)
    assembly = document.assembly
    if any((plan.instance_patches, plan.add_instances, plan.delete_instances, plan.mate_patches)) and assembly is None:
        _reject("Instance edits require an existing assembly")
    if assembly is not None:
        instances = {instance.id: instance for instance in assembly.instances}
        mates = {mate.id: mate for mate in assembly.mates}
        new_instances = {instance.id for instance in plan.add_instances}
        instance_patch_ids = {patch.id for patch in plan.instance_patches}
        mate_patch_ids = {patch.id for patch in plan.mate_patches}
        deleted_instances = set(plan.delete_instances)
        if (len(instance_patch_ids) != len(plan.instance_patches) or len(mate_patch_ids) != len(plan.mate_patches)
                or len(new_instances) != len(plan.add_instances) or len(deleted_instances) != len(plan.delete_instances)):
            _reject("Assembly edit contains repeated IDs")
        if new_instances & instances.keys() or deleted_instances & instance_patch_ids:
            _reject("An edit cannot replace or reuse an existing instance ID")
        if (scoped_instances | instance_patch_ids | deleted_instances) - (instances.keys() | new_instances) or scoped_mates - mates.keys():
            _reject("Edit intent references an unknown assembly instance or mate")
        if (instance_patch_ids | deleted_instances | new_instances) - scoped_instances or mate_patch_ids - scoped_mates:
            _reject("Assembly edit changes an instance or mate outside its declared intent")
        if deleted_instances - removable_instances or new_instances - addable_instances:
            _reject("Adding or deleting an assembly instance requires the corresponding explicit intent")
        for mate_id in mate_patch_ids:
            if not any(mate_id in intent.mates and (
                    mate_id in intent.source or re.search(r"配合|约束|固定|对齐|同轴|mate|align|concentric|fixed", intent.source, re.I))
                    for intent in plan.intents):
                _reject("Changing an assembly mate requires an explicit mate change request", mate=mate_id)
    if set(plan.parameters) - set(document.parameters):
        _reject("Edit references an unknown parameter")
    changed_parameters = set(plan.parameters)
    # A shared parameter must not silently change a feature outside the targets.
    dependent_parameters = set(changed_parameters)
    for _ in range(len(document.parameters)):
        more = {name for name, parameter in document.parameters.items()
                if parameter.expression and _references(parameter.expression) & dependent_parameters}
        if more <= dependent_parameters:
            break
        dependent_parameters |= more
    direct = {feature.id for feature in document.features
              if _references(feature.params.model_dump()) & dependent_parameters}
    if direct - targets:
        _reject("This parameter is shared with other features; clarify whether they should also change", features=sorted(direct - targets))
    if assembly is not None:
        affected_instances = {instance.id for instance in assembly.instances
                              if _references(instance.transform.model_dump()) & dependent_parameters}
        affected_mates = {mate.id for mate in assembly.mates if _references(mate.model_dump()) & dependent_parameters}
        if affected_instances - scoped_instances or affected_mates - scoped_mates:
            _reject("This parameter is shared with assembly placements or mates outside the requested edit")
    affected = patched | removed | direct | set(added)
    # Expectations are still strict: values can be revised, never removed or made
    # less precise as a side effect of planning.
    expectation = plan.expected_geometry or {}
    before_expectation = document.expected_geometry.model_dump(mode="json")
    for key, value in expectation.items():
        if key not in before_expectation:
            _reject("Unknown geometry expectation field")
        if value is None and before_expectation.get(key) is not None:
            _reject("An edit cannot discard an existing geometry expectation", field=key)
    if expectation.get("tolerance", document.expected_geometry.tolerance) > document.expected_geometry.tolerance:
        _reject("An edit cannot relax geometry verification tolerance")
    if "bbox" in expectation and expectation["bbox"] != before_expectation["bbox"] and not any(item.changes_envelope for item in plan.intents):
        _reject("Bounding dimensions may change only for a declared envelope edit")
    if "solids" in expectation and expectation["solids"] != before_expectation["solids"] and not any(item.action in {"add", "remove"} for item in plan.intents):
        _reject("Changing the solid count requires an explicit add/remove intent")
    if plan.constraints is not None:
        replacement = {constraint.id: constraint for constraint in plan.constraints}
        if len(replacement) != len(plan.constraints):
            _reject("Constraint IDs must remain unique")
        for constraint in document.constraints:
            new = replacement.get(constraint.id)
            if new is None:
                if constraint.feature_id not in removed:
                    _reject("An edit cannot discard an existing constraint", constraint=constraint.id)
                continue
            if new == constraint:
                continue
            if new.type != constraint.type or new.feature_id != constraint.feature_id or new.tolerance > constraint.tolerance:
                _reject("An edit cannot replace or weaken an existing constraint", constraint=constraint.id)
            if constraint.feature_id not in affected and not (_references(constraint.expected) & dependent_parameters):
                _reject("An edit cannot change an unrelated constraint", constraint=constraint.id)
    if "holes" in expectation:
        for hole in before_expectation["holes"]:
            if hole.get("feature_id") not in affected and not (_references(hole) & dependent_parameters):
                if hole not in expectation["holes"]:
                    _reject("An edit cannot discard an unrelated hole expectation")
    candidate = apply_edits(document, **plan.apply_kwargs())
    after = {feature.id: feature for feature in candidate.features}
    # A plan cannot hide pre-existing geometry by choosing an unrelated output.
    output = candidate.output_feature or candidate.features[-1].id
    reachable = set()
    def visit(feature_id):
        if feature_id in reachable:
            return
        reachable.add(feature_id)
        feature = after[feature_id]
        for dependency in feature.inputs + feature.depends_on:
            visit(dependency)
    visit(output)
    old_output = document.output_feature or document.features[-1].id
    old_reachable = set()
    def visit_old(feature_id):
        if feature_id in old_reachable:
            return
        old_reachable.add(feature_id)
        for dependency in old[feature_id].inputs + old[feature_id].depends_on:
            visit_old(dependency)
    visit_old(old_output)
    if old_reachable - removed - reachable:
        _reject("The edit would detach existing geometry from the output", features=sorted(old_reachable - removed - reachable))
    # Keep complete hole verification; every changed/new hole must retain its
    # explicit measured expectation when the document used one before.
    if document.expected_geometry.holes:
        expected = {hole.feature_id: hole for hole in candidate.expected_geometry.holes if hole.feature_id}
        for feature in candidate.features:
            if feature.type == "Hole" and feature.id in affected:
                hole = expected.get(feature.id)
                if hole is None:
                    _reject("Changed holes require updated measured expectations", feature=feature.id)
                if hole.model_dump(exclude={"feature_id"}) != feature.params.model_dump():
                    _reject("Hole expectation must agree with the edited hole geometry", feature=feature.id)
    _preserve_unselected_holes(plan, document, candidate)
    compile_ir(candidate)
    return candidate
