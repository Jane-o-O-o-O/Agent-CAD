"""Bounded arithmetic interpreter; no eval, calls, indexing or attributes."""
from __future__ import annotations

import ast
import math
from typing import Mapping

from .errors import DependencyError, ParameterError
from .models import Parameter

MAX_VALUE = 1e12
MAX_NODES = 64
_ALLOWED_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)


def _finite(value: float) -> float:
    if not math.isfinite(value) or abs(value) > MAX_VALUE:
        raise ParameterError("parameter value exceeds the finite numeric budget")
    return value


def parse_expression(expression: str) -> ast.Expression:
    if len(expression) > 512:
        raise ParameterError("expression exceeds 512 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ParameterError("invalid arithmetic expression") from exc
    if len(list(ast.walk(tree))) > MAX_NODES:
        raise ParameterError("expression exceeds complexity budget")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Expression, ast.Constant, ast.Name, ast.Load,
                                 ast.BinOp, ast.UnaryOp, ast.UAdd, ast.USub, *_ALLOWED_BINARY)):
            raise ParameterError("only numbers, parameter names and arithmetic are allowed")
        if isinstance(node, ast.Constant) and (type(node.value) not in (int, float)):
            raise ParameterError("only numeric literals are allowed")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or not node.id.isascii()):
            raise ParameterError("invalid parameter reference")
    return tree


def expression_dependencies(expression: str) -> set[str]:
    return {node.id for node in ast.walk(parse_expression(expression)) if isinstance(node, ast.Name)}


def evaluate_expression(value: float | str, parameters: Mapping[str, float]) -> float:
    if isinstance(value, bool):
        raise ParameterError("boolean values are not numeric parameters")
    if isinstance(value, (int, float)):
        return _finite(float(value))
    tree = parse_expression(value)

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Constant):
            return _finite(float(node.value))
        if isinstance(node, ast.Name):
            if node.id not in parameters:
                raise DependencyError("unknown parameter", details={"parameter": node.id})
            return _finite(parameters[node.id])
        if isinstance(node, ast.UnaryOp):
            number = visit(node.operand)
            return number if isinstance(node.op, ast.UAdd) else -number
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            try:
                if isinstance(node.op, ast.Add):
                    result = left + right
                elif isinstance(node.op, ast.Sub):
                    result = left - right
                elif isinstance(node.op, ast.Mult):
                    result = left * right
                elif isinstance(node.op, ast.Div):
                    result = left / right
                else:
                    if abs(right) > 8 or (left < 0 and not right.is_integer()):
                        raise ParameterError("exponent outside supported real arithmetic budget")
                    result = left ** right
            except (ZeroDivisionError, OverflowError, ValueError) as exc:
                raise ParameterError("invalid arithmetic operation") from exc
            return _finite(float(result))
        raise ParameterError("unsupported expression")

    return visit(tree.body)


def resolve_parameters(parameters: Mapping[str, Parameter]) -> dict[str, float]:
    resolved: dict[str, float] = {}
    active: set[str] = set()

    def resolve(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name in active:
            raise DependencyError("parameter dependency cycle", details={"parameter": name})
        if name not in parameters:
            raise DependencyError("unknown parameter", details={"parameter": name})
        if len(active) >= 64:
            raise DependencyError("parameter dependency depth exceeds 64")
        active.add(name)
        parameter = parameters[name]
        if parameter.expression is not None:
            for dependency in expression_dependencies(parameter.expression):
                resolve(dependency)
            value = evaluate_expression(parameter.expression, resolved)
        else:
            value = evaluate_expression(parameter.value, resolved)
        if parameter.kind == "integer" and not value.is_integer():
            raise ParameterError("integer parameter is not an integer", details={"parameter": name})
        if parameter.min is not None and value < parameter.min:
            raise ParameterError("parameter below minimum", details={"parameter": name, "minimum": parameter.min})
        if parameter.max is not None and value > parameter.max:
            raise ParameterError("parameter above maximum", details={"parameter": name, "maximum": parameter.max})
        active.remove(name)
        resolved[name] = value
        return value

    for name in parameters:
        resolve(name)
    return resolved
