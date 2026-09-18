"""Data consistency rules: “every <source row matching X> must (not) have a <target row> with the same key”.

Only generates read-only SQL. Counting and sampling run through the normal query layer.
"""
from core import filters


def _types(schema, table):
    cols = schema.table_columns(table)
    return dict(zip(cols["col"], cols["type"]))


def validate(spec, schema):
    """List of problems with a rule, empty when it can run."""
    problems = []
    for side in ("source", "target"):
        part = spec.get(side) or {}
        t, k = part.get("table"), part.get("key")
        if not t or t not in schema.names:
            problems.append(f"Choose a {side} table.")
        elif not k or not schema.has(t, k):
            problems.append(f"Choose the {side} column that links the two tables.")
    if spec.get("expect") not in ("exists", "not exists"):
        problems.append("Choose whether the matching row must exist or must not exist.")
    return problems


def _base(spec, schema):
    """FROM clause for the source (optionally only its newest N rows), plus the WHERE parts and params."""
    src = spec["source"]
    types = _types(schema, src["table"])
    conds, params = filters.conditions(src.get("filters"), types, alias="s")
    sample = int(spec.get("sample") or 0)
    if spec.get("skip_empty_keys", True):
        conds.append(f"{filters.ident(src['key'], 's')} IS NOT NULL")
    if sample:
        pk = spec.get("source_pk") or src["key"]
        inner_conds, inner_params = filters.conditions(src.get("filters"), types)
        inner_where = (" WHERE " + " AND ".join(inner_conds)) if inner_conds else ""
        frm = (f"FROM (SELECT * FROM {filters.ident(src['table'])}{inner_where} "
               f"ORDER BY {filters.ident(pk)} DESC LIMIT {sample}) AS s")
        outer = [f"{filters.ident(src['key'], 's')} IS NOT NULL"] if spec.get("skip_empty_keys", True) else []
        return frm, outer, inner_params
    return f"FROM {filters.ident(src['table'])} AS s", conds, params


def _exists(spec, schema):
    tgt = spec["target"]
    conds, params = filters.conditions(tgt.get("filters"), _types(schema, tgt["table"]), alias="t")
    link = f"{filters.ident(tgt['key'], 't')} = {filters.ident(spec['source']['key'], 's')}"
    where = " AND ".join([link] + conds)
    return f"EXISTS (SELECT 1 FROM {filters.ident(tgt['table'])} AS t WHERE {where})", params


def statements(spec, schema, sample_rows=200, show_columns=15):
    """Returns dict of (sql, params): checked, failed, failing_rows."""
    frm, conds, base_params = _base(spec, schema)
    exists, exists_params = _exists(spec, schema)
    failing = ("NOT " if spec["expect"] == "exists" else "") + exists
    where_checked = (" WHERE " + " AND ".join(conds)) if conds else ""
    where_failed = " WHERE " + " AND ".join(conds + [failing])
    cols = [c for c in schema.cols_by_table.get(spec["source"]["table"], [])][:show_columns]
    import privacy
    cols = [c for c in cols if not privacy.is_secret_column(c)]
    select_cols = ", ".join(filters.ident(c, "s") for c in cols) or "s.*"
    return {
        "checked": (f"SELECT COUNT(*) AS checked {frm}{where_checked}", list(base_params)),
        "failed": (f"SELECT COUNT(*) AS failed {frm}{where_failed}", list(base_params) + list(exists_params)),
        "failing_rows": (f"SELECT {select_cols} {frm}{where_failed} LIMIT {int(sample_rows)}",
                         list(base_params) + list(exists_params)),
    }


def sentence(spec):
    src, tgt = spec.get("source") or {}, spec.get("target") or {}
    cond = filters.describe(src.get("filters"))
    tcond = filters.describe(tgt.get("filters"))
    must = "must have" if spec.get("expect") == "exists" else "must not have"
    s = f"Every `{src.get('table')}` row"
    if cond:
        s += f" where {cond}"
    s += f" {must} a `{tgt.get('table')}` row with {tgt.get('key')} = its {src.get('key')}"
    if tcond:
        s += f" and {tcond}"
    if spec.get("sample"):
        s += f" (checking its newest {spec['sample']:,} rows)"
    return s + "."


def as_check(spec, schema, name, severity="high"):
    """A QA check (for local/checks.yaml) that returns the failing rows.

    Checks carry no bound parameters, so filter values are inlined as properly escaped SQL literals.
    """
    from pymysql.converters import escape_item
    sql, params = statements(spec, schema, sample_rows=1000)["failing_rows"]
    inlined = sql % tuple(escape_item(p, "utf8mb4") for p in params) if params else sql
    return {"title": name, "area": "Data consistency", "severity": severity, "expect": "empty",
            "why": sentence(spec), "tags": ["consistency"], "sql": inlined + "\n"}
