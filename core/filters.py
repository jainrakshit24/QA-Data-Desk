"""Click-built filters turned into a parameterised WHERE clause. Values are always bound, never pasted."""
import re

OPS = ["equals", "not equal", "contains", "starts with", "ends with", "greater than",
       "less than", "at least", "at most", "between", "one of", "is empty", "is not empty"]
NO_VALUE = {"is empty", "is not empty"}
TEXT_TYPES = ("char", "text", "enum", "set", "json")


def is_text(col_type):
    return any(t in (col_type or "").lower() for t in TEXT_TYPES)


def ident(name, alias=None):
    if not re.fullmatch(r"[A-Za-z0-9_$]+", name or ""):
        raise ValueError(f"“{name}” is not a valid table or column name.")
    return f"{alias}.`{name}`" if alias else f"`{name}`"


def conditions(filters, types, alias=None):
    """Returns (list of SQL conditions, params)."""
    clauses, params = [], []
    for f in filters or []:
        col, op, val = f.get("col"), f.get("op"), str(f.get("val") if f.get("val") is not None else "").strip()
        if col not in types or op not in OPS:
            continue
        q = ident(col, alias)
        if op == "is empty":
            clauses.append(f"({q} IS NULL OR {q} = '')" if is_text(types[col]) else f"{q} IS NULL")
            continue
        if op == "is not empty":
            clauses.append(f"({q} IS NOT NULL AND {q} <> '')" if is_text(types[col]) else f"{q} IS NOT NULL")
            continue
        if val == "":
            continue
        if op == "equals":
            clauses.append(f"{q} = %s"); params.append(val)
        elif op == "not equal":
            clauses.append(f"({q} <> %s OR {q} IS NULL)"); params.append(val)
        elif op == "contains":
            clauses.append(f"{q} LIKE %s"); params.append(f"%{val}%")
        elif op == "starts with":
            clauses.append(f"{q} LIKE %s"); params.append(f"{val}%")
        elif op == "ends with":
            clauses.append(f"{q} LIKE %s"); params.append(f"%{val}")
        elif op in ("greater than", "less than", "at least", "at most"):
            sign = {"greater than": ">", "less than": "<", "at least": ">=", "at most": "<="}[op]
            clauses.append(f"{q} {sign} %s"); params.append(val)
        elif op == "between":
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if len(parts) == 2:
                clauses.append(f"{q} BETWEEN %s AND %s"); params.extend(parts)
        elif op == "one of":
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if parts:
                clauses.append(f"{q} IN ({', '.join(['%s'] * len(parts))})"); params.extend(parts)
    return clauses, params


def build_where(filters, types, alias=None):
    clauses, params = conditions(filters, types, alias)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def describe(filters):
    parts = []
    for f in filters or []:
        if f.get("op") in NO_VALUE:
            parts.append(f"{f.get('col')} {f.get('op')}")
        elif str(f.get("val") or "").strip():
            parts.append(f"{f.get('col')} {f.get('op')} {f.get('val')}")
    return " and ".join(parts)
