"""Ports for trusted CAD adapters. Importing this module never loads a kernel."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from .compiler import CompiledIR


@dataclass
class CADBuildResult:
    shape: Any
    trace: list[dict[str, Any]] = field(default_factory=list)
    geometry: dict[str, Any] = field(default_factory=dict)
    compiled: Any = None


@runtime_checkable
class CADBackend(Protocol):
    def build(self, compiled: "CompiledIR") -> CADBuildResult: ...

    def export(self, result: CADBuildResult, output_dir: Path) -> list[dict[str, Any]]: ...

    def validate(self, result: CADBuildResult) -> dict[str, Any]: ...

    def properties(self, result: CADBuildResult) -> dict[str, Any]: ...

    def query_topology(self, result: CADBuildResult, selector: dict[str, Any]) -> list[dict[str, Any]]: ...

    def tessellate(self, result: CADBuildResult, tolerance: float = 0.08) -> dict[str, Any]: ...

    def cleanup(self) -> None: ...
