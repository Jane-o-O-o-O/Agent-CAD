"""Public CAD failures independent of HTTP, model providers and geometry kernels."""

from typing import Any


class CADError(Exception):
    """A structured failure safe to persist and return to a CAD client."""

    def __init__(
        self, message: str, *, feature_id: str | None = None,
        details: dict[str, Any] | None = None, recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.feature_id = feature_id
        self.details = details or {}
        self.recoverable = recoverable

    @property
    def code(self) -> str:
        return type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "message": self.message,
            "feature_id": self.feature_id, "details": self.details,
            "recoverable": self.recoverable,
        }


class RequirementError(CADError):
    pass


class SchemaValidationError(CADError):
    pass


class ParameterError(CADError):
    pass


class DependencyError(CADError):
    pass


class GeometryKernelError(CADError):
    pass


class BooleanOperationError(GeometryKernelError):
    pass


class InvalidTopologyError(GeometryKernelError):
    pass


class ConstraintViolationError(CADError):
    pass


class ExportError(CADError):
    pass


class ExecutionTimeoutError(CADError):
    pass


class InfrastructureError(CADError):
    pass
