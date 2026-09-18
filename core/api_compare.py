"""API ↔ database ↔ expected-value comparison, and rules that validate an API response on its own."""
import datetime as dt
import json
import re
from dataclasses import dataclass, field

MISSING = object()   # a value that is not present at all (different from null)


# ------------------------------------------------------------------ JSON
class JSONProblem(ValueError):
    pass


def parse_json(text):
    """Parse pasted JSON with a message a QA can act on."""
    text = (text or "").strip()
    if not text:
        raise JSONProblem("Paste the API response JSON first.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        line = text.splitlines()[e.lineno - 1] if e.lineno - 1 < len(text.splitlines()) else ""
        hint = ""
        if "'" in text and '"' not in text:
            hint = " JSON needs double quotes, not single quotes."
        elif re.search(r",\s*[}\]]", text):
            hint = " Remove the comma before a closing } or ]."
        raise JSONProblem(f"This is not valid JSON — {e.msg} at line {e.lineno}, column {e.colno}: "
                          f"{line.strip()[:80]!r}.{hint}") from None


def flatten(data, prefix="", max_fields=500):
    """{'data': {'items': [{'id': 1}]}} -> {'data.items[0].id': 1, 'data.items': [...] (count kept)}."""
    out = {}

    def walk(v, path):
        if len(out) >= max_fields:
            return
        if isinstance(v, dict):
            if not v and path:
                out[path] = {}
            for k, child in v.items():
                walk(child, f"{path}.{k}" if path else str(k))
        elif isinstance(v, list):
            out[path] = v
            for i, child in enumerate(v[:50]):
                walk(child, f"{path}[{i}]")
        else:
            out[path] = v

    walk(data, prefix)
    return out


def get_path(data, path):
    """Read a dotted path like data.items[0].id; returns MISSING when absent."""
    if path in ("", "$"):
        return data
    cur = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return MISSING
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return MISSING
            cur = cur[part]
    return cur


# ------------------------------------------------------------------ normalisation
@dataclass
class Options:
    case_insensitive: bool = True
    trim: bool = True
    numbers: bool = True       # "5" == 5 == 5.0
    booleans: bool = True      # true == 1 == "true" == "yes"
    dates: bool = True         # "2026-09-17T10:00:00Z" == datetime(2026, 9, 17, 10, 0)
    empty_is_null: bool = False


_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$")


def normalise(v, opt):
    """A comparable form of a value. None means null; MISSING stays MISSING."""
    if v is MISSING:
        return MISSING
    if v is None:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):   # numpy scalars
        try:
            v = v.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(v, float) and v != v:                            # NaN from pandas
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, bool):
        return ("bool", v) if opt.booleans else str(v).lower()
    if isinstance(v, (dt.datetime, dt.date)):
        if opt.dates:
            return ("time", _as_utc_naive(v))
        return str(v)
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        if opt.numbers:
            f = float(v)
            return ("num", int(f) if f.is_integer() else round(f, 9))
        return str(v)
    s = str(v)
    if opt.trim:
        s = s.strip()
    if opt.empty_is_null and s == "":
        return None
    if opt.dates and _DATE.match(s):
        try:
            import pandas as pd
            return ("time", _as_utc_naive(pd.to_datetime(s).to_pydatetime()))
        except (ValueError, TypeError):
            pass
    if opt.numbers and re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        f = float(s)
        return ("num", int(f) if f.is_integer() else round(f, 9))
    if opt.booleans and s.lower() in _TRUE | _FALSE:
        return ("bool", s.lower() in _TRUE)
    return s.lower() if opt.case_insensitive else s


def _as_utc_naive(d):
    if isinstance(d, dt.datetime):
        if d.tzinfo is not None:
            d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return d.replace(microsecond=0) if d.microsecond == 0 else d
    return dt.datetime(d.year, d.month, d.day)


def same(a, b, opt):
    na, nb = normalise(a, opt), normalise(b, opt)
    if na is MISSING or nb is MISSING:
        return na is nb
    # numbers vs booleans: 1 == true
    if opt.booleans and isinstance(na, tuple) and isinstance(nb, tuple) and {na[0], nb[0]} == {"num", "bool"}:
        num = na if na[0] == "num" else nb
        boo = nb if num is na else na
        return num[1] in (0, 1) and bool(num[1]) == boo[1]
    return na == nb


# ------------------------------------------------------------------ comparison
def display(v):
    if v is MISSING:
        return "— not present —"
    if v is None:
        return "NULL"
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)[:200]
    return str(v)


def compare(mapping, api, db_row, opt=None):
    """Compare mapped fields.

    mapping: list of dicts {api_field, db_column, expected (optional, "" means none)}
    api: parsed JSON; db_row: dict of column -> value, or None when no DB row was found.
    Returns list of result dicts with: field, db_column, expected, api, db, result, layer.
    """
    opt = opt or Options()
    results = []
    for m in mapping:
        api_field = (m.get("api_field") or "").strip()
        db_col = (m.get("db_column") or "").strip()
        has_expected = m.get("expected") not in (None, "")
        api_v = get_path(api, api_field) if api_field else MISSING
        if db_row is None or not db_col:
            db_v = MISSING
        else:
            db_v = db_row.get(db_col, MISSING)
        exp_v = m.get("expected") if has_expected else MISSING

        if api_v is MISSING and db_v is MISSING:
            result, layer = "Missing in API and DB", "API, DB"
        elif api_v is MISSING:
            result, layer = "Missing in API", "API"
        elif db_v is MISSING:
            result, layer = "Missing in DB", "DB"
        elif has_expected:
            api_ok, db_ok = same(exp_v, api_v, opt), same(exp_v, db_v, opt)
            if api_ok and db_ok:
                result, layer = "Match", ""
            elif not api_ok and not db_ok:
                result = "Mismatch"
                layer = "API and DB both differ from expected" if not same(api_v, db_v, opt) \
                    else "API and DB agree, but differ from expected"
            elif not api_ok:
                result, layer = "Mismatch", "API differs from expected (DB is correct)"
            else:
                result, layer = "Mismatch", "DB differs from expected (API is correct)"
        else:
            result = "Match" if same(api_v, db_v, opt) else "Mismatch"
            layer = "" if result == "Match" else "API ≠ DB"
        results.append({
            "field": api_field or db_col, "db_column": db_col,
            "expected": display(exp_v) if has_expected else "",
            "api": display(api_v), "db": display(db_v),
            "result": result, "where": layer,
        })
    return results


def suggest_mapping(api_fields, db_columns):
    """Pair API fields with DB columns by name: exact, snake/camel, then last path segment."""
    def key(s):
        return re.sub(r"[^a-z0-9]", "", re.sub(r"\[\d+\]", "", str(s)).split(".")[-1].lower())

    by_key = {}
    for c in db_columns:
        by_key.setdefault(key(c), c)
    out = []
    for f in api_fields:
        k = key(f)
        col = by_key.get(k)
        if not col and k.endswith("id"):
            col = by_key.get("id") if k == "id" else None
        if col:
            out.append({"api_field": f, "db_column": col, "expected": ""})
    return out


# ------------------------------------------------------------------ response rules
OPERATORS = {
    "equals": "value equals",
    "not equals": "value is not",
    "exists": "field is present",
    "not exists": "field is absent",
    "not null": "present and not null",
    "is null": "is null",
    "not empty": "not null, not empty string/list/object",
    "contains": "text contains / list contains",
    "matches regex": "text matches regular expression",
    "greater than": "number greater than",
    "less than": "number less than",
    "count at least": "list has at least N items",
    "count equals": "list has exactly N items",
    "type is": "string / number / boolean / list / object / null",
}


@dataclass
class Rule:
    path: str
    operator: str
    expected: str = ""
    note: str = field(default="")


def _type_name(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def check_rule(data, rule, opt=None):
    """Returns (status, detail). status: 'pass' | 'fail' | 'warn' (could not be evaluated)."""
    opt = opt or Options()
    v = get_path(data, rule.path)
    op, exp = rule.operator, rule.expected
    shown = display(v)
    if op not in OPERATORS:
        return "warn", f"Unknown operator “{op}”"
    if op == "exists":
        return ("pass", shown) if v is not MISSING else ("fail", "field is not present")
    if op == "not exists":
        return ("pass", "field is not present") if v is MISSING else ("fail", f"present: {shown}")
    if v is MISSING:
        return "fail", "field is not present"
    if op == "is null":
        return ("pass", "null") if v is None else ("fail", shown)
    if op == "not null":
        return ("pass", shown) if v is not None else ("fail", "null")
    if op == "not empty":
        empty = v is None or v == "" or v == [] or v == {}
        return ("fail", "empty") if empty else ("pass", shown)
    if op == "equals":
        return ("pass", shown) if same(exp, v, opt) else ("fail", f"{shown} (expected {exp})")
    if op == "not equals":
        return ("fail", shown) if same(exp, v, opt) else ("pass", shown)
    if op == "contains":
        if isinstance(v, list):
            ok = any(same(exp, item, opt) for item in v)
        elif isinstance(v, str):
            ok = (exp.lower() in v.lower()) if opt.case_insensitive else (exp in v)
        else:
            return "warn", f"cannot use contains on {_type_name(v)}"
        return ("pass", shown) if ok else ("fail", f"{shown} does not contain {exp}")
    if op == "matches regex":
        if not isinstance(v, str):
            return "warn", f"value is {_type_name(v)}, not text"
        try:
            ok = re.search(exp, v) is not None
        except re.error as e:
            return "warn", f"invalid regular expression: {e}"
        return ("pass", shown) if ok else ("fail", f"{shown} does not match")
    if op in ("greater than", "less than"):
        try:
            a, b = float(v), float(exp)
        except (TypeError, ValueError):
            return "warn", f"{shown} or {exp!r} is not a number"
        ok = a > b if op == "greater than" else a < b
        return ("pass", shown) if ok else ("fail", f"{shown} (expected {op} {exp})")
    if op in ("count at least", "count equals"):
        if not isinstance(v, (list, dict, str)):
            return "warn", f"value is {_type_name(v)}, it has no count"
        try:
            n = int(exp)
        except ValueError:
            return "warn", f"expected count {exp!r} is not a whole number"
        ok = len(v) >= n if op == "count at least" else len(v) == n
        return ("pass", f"{len(v)} items") if ok else ("fail", f"{len(v)} items (expected {op} {n})")
    if op == "type is":
        actual = _type_name(v)
        return ("pass", actual) if actual == exp.strip().lower() else ("fail", f"{actual} (expected {exp})")
    return "warn", "not evaluated"
