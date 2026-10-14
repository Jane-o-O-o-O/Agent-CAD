from app.application.services.cad_v2_repair import next_repair
from app.domain.cad_v2.models import CADDocumentIR


def example():
    return CADDocumentIR.model_validate({"features": [
        {"id": "base", "type": "Box", "params": {"width": 20, "depth": 20, "height": 5}},
        {"id": "round", "type": "Fillet", "inputs": ["base"], "params": {"radius": 1, "selector": {"center": [0, 0, 2.50005], "tolerance": 1e-6, "expected_count": 1}}},
    ], "expected_geometry": {"bbox": [20, 20, 5]}})


def error(**kwargs):
    return {"code": "InvalidTopologyError", "feature_id": "round", "recoverable": True,
            "details": {"recommended_tolerance": 1e-4}, **kwargs}


def test_repair_preserves_design_and_has_a_bounded_explicit_patch():
    original = example()
    repaired = next_repair(original, error(), [])
    assert repaired["ir"].features[1].params.radius == 1
    assert repaired["ir"].expected_geometry == original.expected_geometry
    assert original.features[1].params.selector.tolerance == 1e-6
    assert repaired["patch"]["path"] == "params.selector.tolerance"
    assert next_repair(original, error(), [repaired]) is None
    assert next_repair(original, error(), [{}, {}, {}]) is None


def test_infrastructure_kernel_and_oversized_repairs_are_refused():
    for failure in [error(code="InfrastructureError"), error(code="GeometryKernelError"),
                    error(recoverable=False), error(details={"recommended_tolerance": 1}),
                    error(details={"recommended_tolerance": float("nan")})]:
        assert next_repair(example(), failure, []) is None
