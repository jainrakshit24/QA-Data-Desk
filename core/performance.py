"""Read MySQL `EXPLAIN FORMAT=JSON` output and explain query cost in plain language.

Analysis only: nothing here rewrites or runs the query.
"""
import json
import re

ACCESS = {
    "system": ("one row", "The table has a single row."),
    "const": ("one row by key", "Reads at most one row through a primary or unique key."),
    "eq_ref": ("one row per join", "Reads exactly one row for each row from the previous table, via a unique key."),
    "ref": ("index lookup", "Reads matching rows through an index."),
    "fulltext": ("full-text index", "Uses a FULLTEXT index."),
    "ref_or_null": ("index lookup + NULLs", "Index lookup that also searches for NULL values."),
    "index_merge": ("several indexes", "Combines more than one index."),
    "unique_subquery": ("subquery by unique key", "Subquery resolved through a unique index."),
    "index_subquery": ("subquery by index", "Subquery resolved through an index."),
    "range": ("index range", "Reads a range of an index — good for BETWEEN, <, >, IN."),
    "index": ("full index scan", "Reads the whole index instead of the table; cheaper than a table scan, still every entry."),
    "ALL": ("full table scan", "Reads every row of the table."),
}

LARGE = 100_000
MEDIUM = 10_000


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _walk(node, tables, flags):
    if isinstance(node, dict):
        if node.get("using_filesort") is True:
            flags["filesort"] = True
        if node.get("using_temporary_table") is True:
            flags["temporary"] = True
        tbl = node.get("table")
        if isinstance(tbl, dict) and "table_name" in tbl:
            tables.append(tbl)
        for k, v in node.items():
            if k == "table" and isinstance(v, dict) and "table_name" in v:
                _walk({kk: vv for kk, vv in v.items() if kk not in ("table_name",)}, tables, flags)
            else:
                _walk(v, tables, flags)
    elif isinstance(node, list):
        for item in node:
            _walk(item, tables, flags)


def parse_plan(explain_json):
    data = json.loads(explain_json) if isinstance(explain_json, str) else explain_json
    tables, flags = [], {"filesort": False, "temporary": False}
    _walk(data, tables, flags)
    block = data.get("query_block", {}) if isinstance(data, dict) else {}
    cost = _num((block.get("cost_info") or {}).get("query_cost"))
    out = []
    for t in tables:
        access = t.get("access_type", "")
        label, meaning = ACCESS.get(access, (access or "unknown", ""))
        rows = _num(t.get("rows_examined_per_scan"))
        out.append({
            "table": t.get("table_name"), "access": access, "how": label, "meaning": meaning,
            "index used": t.get("key") or "", "rows examined": int(rows) if rows is not None else None,
            "filtered %": _num(t.get("filtered")), "covering index": bool(t.get("using_index")),
            "condition": (t.get("attached_condition") or "")[:200],
        })
    return {"query_cost": cost, "tables": out, "filesort": flags["filesort"], "temporary": flags["temporary"]}


_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def sql_observations(sql):
    """Patterns in the SQL text that commonly defeat indexes or return too much."""
    text = sql or ""
    bare = _STRINGS.sub("''", text)
    low = bare.lower()
    notes = []
    if re.search(r"select\s+(\w+\.)?\*", low):
        notes.append(("low", "SELECT * reads every column. Name only the columns you need."))
    if not re.search(r"\blimit\s+\d+", low) and not re.search(r"\b(count|sum|avg|min|max)\s*\(", low):
        notes.append(("low", "No LIMIT. The app stops at its row limit, but MySQL may still read far more rows."))
    for m in re.finditer(r"like\s*'%[^']*'", text, re.I):
        notes.append(("medium", f"{m.group(0)[:40]} starts with a wildcard, so no index can be used for it."))
        break
    fn = re.search(r"\b(date|year|month|lower|upper|trim|substring|cast|convert|date_format)\s*\(\s*`?[\w.]+`?[^()]*\)\s*"
                   r"(=|<|>|<=|>=|like|in|between)", low)
    if fn:
        notes.append(("medium", f"A function wraps a column in the filter ({fn.group(1).upper()}(…)). MySQL cannot use an index "
                                "on that column — compare the raw column to a range instead, e.g. "
                                "created >= '2026-09-01' AND created < '2026-09-02'."))
    if re.search(r"\bwhere\b.*\bor\b", low, re.S):
        notes.append(("low", "OR in the filter can stop MySQL from using a single index."))
    if re.search(r"order\s+by\s+rand\s*\(", low):
        notes.append(("medium", "ORDER BY RAND() sorts every matching row."))
    if re.search(r"not\s+in\s*\(\s*select", low):
        notes.append(("low", "NOT IN (SELECT …) behaves unexpectedly with NULLs and is often slower than NOT EXISTS."))
    return notes


def assess(plan, sql):
    """Warnings (level, message) and suggestions, from the plan and the SQL text."""
    warnings, suggestions = [], []
    for t in plan["tables"]:
        rows = t["rows examined"] or 0
        if t["access"] == "ALL":
            level = "high" if rows >= LARGE else ("medium" if rows >= MEDIUM else "low")
            warnings.append((level, f"Full table scan on `{t['table']}` — about {rows:,} rows read."))
            if rows >= MEDIUM:
                suggestions.append(f"Filter `{t['table']}` on an indexed column (marked 🔑 in Find data), or narrow a date range.")
        elif t["access"] == "index" and rows >= LARGE:
            warnings.append(("medium", f"Full index scan on `{t['table']}` — about {rows:,} index entries read."))
    big = max([t["rows examined"] or 0 for t in plan["tables"]] or [0])
    if plan["filesort"] and big >= MEDIUM:
        warnings.append(("medium", "Sorting needs an extra pass (filesort) over many rows."))
        suggestions.append("Sort on an indexed column, or filter first so fewer rows are sorted.")
    if plan["temporary"] and big >= MEDIUM:
        warnings.append(("medium", "A temporary table is built (usually for GROUP BY / DISTINCT)."))
    for level, msg in sql_observations(sql):
        if msg.startswith("No LIMIT") and big <= 1000:
            continue  # a handful of rows either way
        warnings.append((level, msg))
    if not any(level in ("high", "medium") for level, _ in warnings):
        suggestions.append("No serious problems: every table is read through an index or is small.")
    order = {"high": 0, "medium": 1, "low": 2}
    warnings.sort(key=lambda w: order[w[0]])
    return warnings, list(dict.fromkeys(suggestions))
