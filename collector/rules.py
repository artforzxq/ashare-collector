"""把筛选条件写成数据，而不是写死在代码里。

条件长这样（config.yaml 里的 screen.criteria）：

    - key: 低位横盘
      title: 跌下来之后横着缩量，等打底
      sort: vol_shrink_ratio
      when:
        - {field: consolidation_days, op: ">=", value: 20}
        - {field: vol_shrink_ratio, op: "<=", value: 0.7}
        - {field: close, op: "<=", compare: ma60, factor: 1.0}

一条 criterion 里的所有条件是与（AND）。支持的运算：
  == != > >= < <= between in not_in contains startswith exists
右侧可以是固定值（value）、另一个字段（compare，可带系数 factor），或者区间（value: [a, b]）。

刻意不用 eval：规则来自配置文件，用 eval 等于把执行权交出去。
字段取不到（None）时，除 exists 外的比较一律判否——宁可漏筛，不要把空值当通过。
"""

from __future__ import annotations

from typing import Any, Sequence

OPERATORS = ("==", "!=", ">", ">=", "<", "<=", "between", "in", "not_in",
             "contains", "startswith", "exists")


def get_value(row: dict, path: str) -> Any:
    """支持 a.b.c 这种路径取值；取不到返回 None。"""
    current: Any = row
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check(row: dict, condition: dict) -> bool:
    """单个条件。"""
    if not isinstance(condition, dict):
        return False
    left = get_value(row, condition.get("field", ""))
    op = str(condition.get("op", "==")).lower()

    if op == "exists":
        want = condition.get("value", True)
        return (left is not None) == bool(want)
    if left is None:
        return False

    if "compare" in condition:                       # 与另一个字段比
        right = get_value(row, condition["compare"])
        if right is None:
            return False
        factor = _number(condition.get("factor", 1.0))
        left_num, right_num = _number(left), _number(right)
        if left_num is None or right_num is None or factor is None:
            return False
        left, right = left_num, right_num * factor

    if op == "between":
        bounds = condition.get("value") or []
        if len(bounds) != 2:
            return False
        low, high = _number(bounds[0]), _number(bounds[1])
        left_num = _number(left)
        if low is None or high is None or left_num is None:
            return False
        return low <= left_num <= high

    if op in ("in", "not_in"):
        options = condition.get("value") or []
        hit = left in options
        return hit if op == "in" else not hit

    if op == "contains":
        return str(condition.get("value", "")) in str(left)

    if op == "startswith":
        return str(left).startswith(str(condition.get("value", "")))

    if op not in ("==", "!=", ">", ">=", "<", "<="):
        raise ValueError(f"不支持的比较符：{op}（可选 {', '.join(OPERATORS)}）")

    if "compare" not in condition:
        right = condition.get("value")
    if op == "==":
        return left == right
    if op == "!=":
        return left != right

    left_num, right_num = _number(left), _number(right)
    if left_num is None or right_num is None:
        return False
    if op == ">":
        return left_num > right_num
    if op == ">=":
        return left_num >= right_num
    if op == "<":
        return left_num < right_num
    return left_num <= right_num


def matches(row: dict, conditions: Sequence[dict]) -> bool:
    """一组条件全部满足才算命中。空条件不算命中（免得配置写错就筛出全市场）。"""
    if not conditions:
        return False
    return all(check(row, condition) for condition in conditions)


def describe(condition: dict) -> str:
    """把条件翻成人话，用于报告和页面说明。"""
    field = condition.get("field", "?")
    op = str(condition.get("op", "==")).lower()
    if op == "exists":
        return f"{field} 有值" if condition.get("value", True) else f"{field} 无值"
    if "compare" in condition:
        factor = condition.get("factor", 1.0)
        right = condition["compare"] if float(factor) == 1 else f"{condition['compare']}×{factor}"
    elif op == "between":
        bounds = condition.get("value") or [None, None]
        right = f"{bounds[0]}~{bounds[1]}"
    else:
        right = str(condition.get("value"))
    symbols = {"==": "=", "!=": "≠", ">": ">", ">=": "≥", "<": "<", "<=": "≤",
               "between": "在", "in": "属于", "not_in": "不属于",
               "contains": "包含", "startswith": "以…开头"}
    return f"{field} {symbols.get(op, op)} {right}"


def describe_all(conditions: Sequence[dict]) -> str:
    return "，".join(describe(condition) for condition in conditions or [])
