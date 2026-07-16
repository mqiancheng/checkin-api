import json
import re

TOKEN_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


def resolve_path(data, path):
    """点号路径取值，支持嵌套与数组索引，例如 data.sign_count / list[0].id。"""
    if path is None or path == "":
        return data
    if not isinstance(data, (dict, list)):
        return None
    cur = data
    for tok in TOKEN_RE.findall(path):
        if tok.startswith("["):
            idx = int(tok[1:-1])
            if isinstance(cur, list) and 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            if isinstance(cur, dict) and tok in cur:
                cur = cur[tok]
            else:
                return None
    return cur


def coerce(val, vtype):
    if vtype == "num":
        try:
            return float(val)
        except (TypeError, ValueError):
            return val
    if vtype == "bool":
        return str(val).strip().lower() in ("true", "1", "yes")
    if vtype == "str":
        return str(val)
    # auto：能转数字就转数字
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    try:
        return float(val)
    except (TypeError, ValueError):
        return val


def eval_condition(cond, data) -> bool:
    op = cond.get("op", "eq")
    path = cond.get("path", "")
    expected = cond.get("value")
    vtype = cond.get("value_type", "auto")

    if op == "exists":
        return resolve_path(data, path) is not None

    actual = resolve_path(data, path)
    if actual is None:
        return False

    a = coerce(actual, vtype)
    e = coerce(expected, vtype)

    if op == "eq":
        return a == e
    if op == "ne":
        return a != e
    if op == "contains":
        return str(expected) in str(actual)
    if op == "gt":
        return a > e
    if op == "ge":
        return a >= e
    if op == "lt":
        return a < e
    if op == "le":
        return a <= e
    if op == "in":
        try:
            lst = json.loads(expected) if isinstance(expected, str) else expected
        except (TypeError, ValueError):
            lst = [expected]
        return actual in lst
    return False


def evaluate(conditions, logic, data) -> bool:
    """JSON 模式：按顶层 AND/OR 组合多个条件。无条件时视为成功。"""
    if not conditions:
        return True
    results = [eval_condition(c, data) for c in conditions]
    if logic == "OR":
        return any(results)
    return all(results)


def evaluate_text(conditions, logic, text) -> bool:
    """文本模式：条件对原文做 contains 判定。"""
    if not conditions:
        return True
    results = []
    for c in conditions:
        expected = c.get("value", "")
        results.append(expected in text)
    return any(results) if logic == "OR" else all(results)
