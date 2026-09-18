# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Database health from the cached structure only — no data is read, so it is instant on any size of database.

Findings are hints for QA and developers, not errors: some tables legitimately have no primary key.
"""
import re

import pandas as pd

from core.edge_cases import kind_of, parse_type

LARGE_ROWS = 1_000_000
WIDE_COLUMNS = 80
NULLABLE_SHARE = 0.8
LEFTOVER = re.compile(r"(^|_)(bak|backup|old|tmp|temp|copy|test|dump|archive|\d{6,8})($|_)", re.I)

CHECKS = {
    "no_primary_key": ("No primary key", "Rows cannot be identified reliably; updates and replication can misbehave."),
    "no_index": ("No index at all", "Every lookup reads the whole table."),
    "large_unindexed_links": ("Large table, link column not indexed", "Joins and lookups on this column scan millions of rows."),
    "type_mismatch": ("Link column type differs from its target", "e.g. int vs bigint or varchar vs int — joins get slow or silently miss rows."),
    "mostly_nullable": ("Most columns allow NULL", "Weak constraints; missing values reach the UI and APIs."),
    "wide": ("Very wide table", f"More than {WIDE_COLUMNS} columns — often a sign of copied or unused fields."),
    "empty": ("Empty table", "Features that read it cannot be tested on this database."),
    "leftover": ("Looks like a backup / temp copy", "Name suggests an old copy; confirm it is not read by the app."),
    "large": ("Large table", f"More than {LARGE_ROWS:,} rows (estimate) — filter on indexed columns."),
}


def _base_type(t):
    info = parse_type(t)
    return info["base"].replace("integer", "int"), info["unsigned"]


def findings(schema):
    """DataFrame: check, title, table, column, detail, rows_est."""
    rows = []
    add = lambda check, table, column="", detail="": rows.append(
        {"check": check, "title": CHECKS[check][0], "table": table, "column": column, "detail": detail,
         "rows_est": int(schema.rows_est.get(table, 0))})
    kinds = dict(zip(schema.tables["name"], schema.tables.get("kind", pd.Series(["BASE TABLE"] * len(schema.tables)))))
    types = {(t, c): ty for t, c, ty in zip(schema.columns["tbl"], schema.columns["col"], schema.columns["type"])}
    for t, frame in schema.columns.groupby("tbl"):
        if str(kinds.get(t, "BASE TABLE")).upper() == "VIEW":
            continue
        keys = set(frame["key"].fillna(""))
        n = len(frame)
        est = int(schema.rows_est.get(t, 0))
        if "PRI" not in keys:
            add("no_primary_key", t, detail=f"{n} columns")
        if not keys & {"PRI", "UNI", "MUL"}:
            add("no_index", t)
        if n >= 5 and (frame["nullable"].astype(str).str.upper() == "YES").mean() >= NULLABLE_SHARE:
            add("mostly_nullable", t, detail=f"{(frame['nullable'].astype(str).str.upper() == 'YES').sum()} of {n} columns")
        if n > WIDE_COLUMNS:
            add("wide", t, detail=f"{n} columns")
        if est == 0:
            add("empty", t)
        if est > LARGE_ROWS:
            add("large", t, detail=f"~{est:,} rows")
        if LEFTOVER.search(t):
            add("leftover", t)
        for r in frame.itertuples():
            link = schema.link_for(t, r.col) if r.col.endswith("_id") or (t, r.col) in schema._fk_by_col else None
            if not link:
                continue
            target = types.get((link[0], link[1]))
            if target and kind_of(parse_type(r.type)) in ("integer", "text") and _base_type(r.type) != _base_type(target):
                add("type_mismatch", t, r.col, f"{r.type} → {link[0]}.{link[1]} {target}")
            if est > LARGE_ROWS and not str(r.key or ""):
                add("large_unindexed_links", t, r.col, f"points to {link[0]}.{link[1]} ({link[2]})")
    df = pd.DataFrame(rows, columns=["check", "title", "table", "column", "detail", "rows_est"])
    return df.sort_values(["check", "rows_est"], ascending=[True, False]).reset_index(drop=True)


def summary(schema, df):
    total_rows = int(sum(schema.rows_est.values()))
    return {"tables": len(schema.names), "columns": len(schema.columns), "rows_est": total_rows,
            "links": int(sum(len(schema.outgoing(t)) for t in schema.names)) if len(schema.names) <= 3000 else None,
            "by_check": {k: int((df["check"] == k).sum()) for k in CHECKS}}


def biggest(schema, n=15):
    t = schema.tables[["name", "rows_est"]].sort_values("rows_est", ascending=False).head(n)
    return t.assign(columns=t["name"].map(lambda x: len(schema.cols_by_table.get(x, []))))
