"""Playbooks: reusable investigations — parameters plus ordered, reviewed SQL steps.

Two kinds of parameter:
  :name       a value, bound by the driver (never pasted into SQL)
  {{name}}    a table or column name, allowed only if it exists in the database schema
"""
import re

import db
from core import params as sql_params

IDENT = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PARAM_TYPES = ["value", "table", "column"]


def normalise(spec):
    s = dict(spec or {})
    s.setdefault("name", "Untitled playbook")
    s.setdefault("objective", "")
    s["parameters"] = [dict(p) for p in s.get("parameters") or [] if p.get("name")]
    for p in s["parameters"]:
        p.setdefault("label", p["name"].replace("_", " "))
        p["type"] = p.get("type") if p.get("type") in PARAM_TYPES else "value"
        p.setdefault("table_param", "")
        p.setdefault("hint", "")
    s["steps"] = [dict(st) for st in s.get("steps") or [] if str(st.get("sql") or "").strip()]
    for st in s["steps"]:
        st.setdefault("title", "Step")
        st.setdefault("purpose", "")
        st.setdefault("look_for", "")
    return s


def used_parameters(sql):
    """(value parameter names, identifier parameter names) used by one step."""
    idents = list(dict.fromkeys(IDENT.findall(sql or "")))
    values = sql_params.names(IDENT.sub("x", sql or ""))
    return values, idents


def problems(spec):
    """What is wrong with a playbook definition, in words."""
    s = normalise(spec)
    out = []
    declared = {p["name"]: p for p in s["parameters"]}
    if not s["name"].strip():
        out.append("Give the playbook a name.")
    if not s["steps"]:
        out.append("Add at least one step with SQL.")
    for i, step in enumerate(s["steps"], 1):
        values, idents = used_parameters(step["sql"])
        for n in values:
            if n not in declared:
                out.append(f"Step {i} uses :{n}, which is not a declared parameter.")
            elif declared[n]["type"] != "value":
                out.append(f"Step {i} uses :{n} as a value, but it is declared as a {declared[n]['type']} — write {{{{{n}}}}}.")
        for n in idents:
            if n not in declared:
                out.append(f"Step {i} uses {{{{{n}}}}}, which is not a declared parameter.")
            elif declared[n]["type"] == "value":
                out.append(f"Step {i} uses {{{{{n}}}}} as a name, but it is declared as a value — write :{n}.")
        try:
            db.check_read_only(IDENT.sub("placeholder", step["sql"]))
        except db.ReadOnlyError as e:
            out.append(f"Step {i}: {e}")
    return out


def render_sql(sql, values, schema, spec):
    """Returns (sql_for_driver, params, missing, error). Nothing runs here."""
    s = normalise(spec)
    declared = {p["name"]: p for p in s["parameters"]}
    vals, idents = used_parameters(sql)
    missing = [n for n in idents + vals if not str(values.get(n, "")).strip()]
    if missing:
        return None, None, missing, None

    def check_name(name):
        v = str(values[name]).strip()
        kind = declared.get(name, {}).get("type", "table")
        if not re.fullmatch(r"[A-Za-z0-9_$]+", v):
            raise ValueError(f"“{v}” is not a valid {kind} name.")
        if kind == "table" and v not in schema.names:
            raise ValueError(f"Table “{v}” does not exist in this database.")
        if kind == "column":
            owner = declared[name].get("table_param")
            table = str(values.get(owner, "")).strip() if owner else ""
            if table and not schema.has(table, v):
                raise ValueError(f"Column “{v}” does not exist in table “{table}”.")
            if not table and v not in set(schema.columns["col"]):
                raise ValueError(f"Column “{v}” does not exist in this database.")
        return f"`{v}`"

    def swap(m):
        return check_name(m.group(1))

    try:
        # validate tables before columns, so the message names the real problem
        for name in sorted(idents, key=lambda n: declared.get(n, {}).get("type") != "table"):
            check_name(name)
        text = IDENT.sub(swap, sql)
        bound, params = sql_params.bind(text, {k: str(v).strip() for k, v in values.items()})
        db.check_read_only(bound)
    except (ValueError, KeyError, db.ReadOnlyError) as e:
        return None, None, [], str(e)
    return bound, params, [], None


def from_ai_plan(plan, issue, identifier_kind):
    """Turn an AI investigation plan into a playbook with an :identifier parameter."""
    return normalise({
        "name": (issue or "Investigation")[:60],
        "objective": plan.get("understanding") or issue,
        "parameters": [{"name": "identifier", "label": identifier_kind or "identifier", "type": "value"}],
        "steps": [{"title": s["title"], "purpose": s["purpose"], "sql": s["sql"], "look_for": s["look_for"]}
                  for s in plan.get("steps", [])],
    })
