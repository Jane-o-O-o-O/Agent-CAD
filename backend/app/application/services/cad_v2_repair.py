"""Conservative local repairs: selection precision only, never design dimensions."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app.domain.cad_v2.models import CADDocumentIR


def next_repair(ir: CADDocumentIR | dict, error: dict[str, Any], history: list[dict], max_attempts: int = 3) -> dict | None:
    if len(history) >= max(0, min(max_attempts, 5)):
        return None
    if error.get("code") != "InvalidTopologyError" or not error.get("recoverable"):
        return None
    details = error.get("details") or {}
    try:
        recommendation = float(details["recommended_tolerance"])
    except (ValueError, TypeError, KeyError):
        return None
    if not math.isfinite(recommendation) or not 0 < recommendation <= 0.001:
        return None
    source = ir.model_dump(mode="json") if isinstance(ir, CADDocumentIR) else json.loads(json.dumps(ir))
    feature = next((item for item in source["features"] if item["id"] == error.get("feature_id")), None)
    if feature is None or feature["type"] not in {"Fillet", "Chamfer"}:
        return None
    selector = feature["params"].get("selector")
    if not selector or selector.get("expected_count") is None:
        return None
    old = selector.get("tolerance", 1e-6)
    # The worker reports millimeters. Current selection repair is deliberately
    # limited to mm documents to avoid changing tolerance semantics under units.
    if source.get("units", "mm") != "mm" or not isinstance(old, (int, float)) or recommendation <= old:
        return None
    patch = {"feature_id": feature["id"], "path": "params.selector.tolerance", "before": old, "after": recommendation}
    fingerprint = hashlib.sha256(json.dumps(patch, sort_keys=True).encode()).hexdigest()
    if any(item.get("fingerprint") == fingerprint or item.get("patch") == patch for item in history):
        return None
    selector["tolerance"] = recommendation
    return {"ir": CADDocumentIR.model_validate(source), "patch": patch,
            "fingerprint": fingerprint,
            "reason": "Increase only semantic selection precision within 0.001 mm; unique match and original geometry constraints remain mandatory"}
