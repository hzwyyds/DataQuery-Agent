from __future__ import annotations

import ast
import math
from typing import Any

import pandas as pd


class FormulaError(ValueError):
    pass


def _aggregate(function: str, value: Any) -> Any:
    if not isinstance(value, pd.Series):
        return value
    if function == "count":
        return int(value.count())
    if function == "mean":
        return float(value.mean())
    if function == "std":
        return float(value.std(ddof=1))
    if function == "sum":
        return float(value.sum())
    if function == "min":
        return float(value.min())
    if function == "max":
        return float(value.max())
    if function == "median":
        return float(value.median())
    raise FormulaError("公式函数不在允许范围内")


_FUNCTIONS = {"abs", "count", "corr", "max", "mean", "median", "min", "sqrt", "std", "sum"}
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)


def evaluate_formula(expression: str, frame: pd.DataFrame, variables: list[str]) -> float:
    """Evaluate an audited math DSL; it never executes Python source."""
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise FormulaError("公式无法解析，请使用字段、函数和四则运算") from exc
    allowed_names = set(variables) | set(frame.columns) | _FUNCTIONS

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in allowed_names:
            if node.id in _FUNCTIONS:
                return node.id
            if node.id not in frame.columns:
                raise FormulaError(f"公式字段不存在: {node.id}")
            return pd.to_numeric(frame[node.id], errors="coerce").dropna()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, _BINARY):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            return left**right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = node.func.id
            if function not in _FUNCTIONS or not 1 <= len(node.args) <= 2:
                raise FormulaError("公式函数不在允许范围内")
            args = [visit(argument) for argument in node.args]
            if function == "corr":
                if len(args) != 2 or not all(isinstance(value, pd.Series) for value in args):
                    raise FormulaError("corr 需要两个数值字段")
                return float(args[0].corr(args[1]))
            if function == "abs":
                return args[0].abs() if isinstance(args[0], pd.Series) else abs(args[0])
            if function == "sqrt":
                return math.sqrt(float(args[0]))
            return _aggregate(function, args[0])
        raise FormulaError("公式包含不允许的语法；仅支持字段、白名单函数和数学运算")

    value = visit(tree)
    if isinstance(value, pd.Series):
        value = value.mean()
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FormulaError("公式结果不是数值") from exc
    if not math.isfinite(result):
        raise FormulaError("公式结果不是有限数值")
    return result
