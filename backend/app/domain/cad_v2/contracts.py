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
