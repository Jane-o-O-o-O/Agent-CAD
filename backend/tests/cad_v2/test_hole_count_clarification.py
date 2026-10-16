"""Hole-count answers resolve conflicts without replacing the original design."""
import asyncio

import pytest

from app.application.services.cad_v2_planner import _plate_requirements, plan
from app.domain.cad_v2.compiler import compile_ir
from app.domain.cad_v2.errors import RequirementError


PLATE = "长80mm宽50mm厚8mm安装板，居中对称四个直径6mm的通孔，水平孔距60mm，垂直孔距30mm"
CONFLICT = PLATE + "，五个孔"


def question(prompt, turns=None):
    with pytest.raises(RequirementError) as error:
        _plate_requirements(prompt, turns)
    return error.value


@pytest.mark.parametrize("answer", ["四个孔", "4个孔", "四", "4", "4个", "孔数量为4"])
def test_original_conflict_can_be_resolved_without_repeating_question(answer):
    error = question(CONFLICT)
    assert error.details["missing"] == ["hole_count"]
    turns = [{"question": error.message, "answer": answer}]
    compiled = compile_ir(asyncio.run(plan(CONFLICT, "count-resolved", clarification=turns)))
    assert compiled.features[0].params == {"width": 80.0, "depth": 50.0, "height": 8.0, "origin": [0.0, 0.0, 0.0]}
    holes = compiled.features[1].params
    assert holes["diameter"] == 6
    assert holes["through"] is True
    assert holes["positions"] == [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]]


def test_conflict_between_rounds_can_be_resolved_by_later_answer():
    turns = [{"question": "是否还有设计补充？", "answer": "五个孔"}]
    error = question(PLATE, turns)
    turns.append({"question": error.message, "answer": "4"})
    requirement = _plate_requirements(PLATE, turns)
    assert requirement["values"]["hole_diameter"] == 6
    assert requirement["holes"] is True


@pytest.mark.parametrize("answer", ["改为四个孔", "孔数量改为4", "以四个孔为准", "最终四个孔"])
def test_explicit_count_correction_does_not_need_a_targeted_question(answer):
    requirement = _plate_requirements(CONFLICT, [{"question": "是否还有设计补充？", "answer": answer}])
    assert requirement["values"]["thickness"] == 8


def test_answer_with_another_conflict_remains_a_question():
    error = question(CONFLICT)
    repeated = question(CONFLICT, [{"question": error.message, "answer": "四个孔，五个孔"}])
    assert repeated.details["missing"] == ["hole_count"]


@pytest.mark.parametrize("answer", ["五个孔", "5", "零个孔", "0", "十个孔"])
def test_unsupported_final_count_is_preserved_for_structured_planning(answer):
    error = question(CONFLICT)
    assert _plate_requirements(CONFLICT, [{"question": error.message, "answer": answer}]) is None


def test_dimension_correction_does_not_implicitly_correct_count():
    error = question(PLATE, [{"question": "厚度是否需要调整？", "answer": "厚度改为9mm，五个孔"}])
    assert error.details["missing"] == ["hole_count"]


@pytest.mark.parametrize("asked", ["请补充孔的数量和厚度", "请补充孔的数量和孔阵列相对板的位置"])
def test_bare_number_does_not_guess_which_missing_field_it_answers(asked):
    assert _plate_requirements(CONFLICT, [{"question": asked, "answer": "4"}]) is None


def test_count_and_dimension_conflicts_can_be_resolved_in_multiple_rounds():
    prompt = CONFLICT + "，厚度9mm"
    first = question(prompt)
    assert first.details["missing"] == ["hole_count", "thickness"]
    turns = [{"question": first.message, "answer": "四个孔"}]
    second = question(prompt, turns)
    assert second.details["missing"] == ["thickness"]
    turns.append({"question": second.message, "answer": "8mm"})
    requirement = _plate_requirements(prompt, turns)
    assert requirement["values"]["thickness"] == 8
