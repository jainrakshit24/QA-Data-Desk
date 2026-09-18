# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Global record search: find which tables and columns contain an identifier.

Only builds SQL — pages run it through the normal read-only query layer.
Each candidate is counted with a capped subquery, many candidates per statement,
so a search over a thousand tables is a handful of fast round trips.
"""
import re

import privacy

CAP = 1000                 # stop counting a column after this many matches
BATCH = 120                # candidates per UNION ALL statement
UNINDEXED_ROW_LIMIT = 200_000

NUMERIC_TYPES = ("int", "bigint", "smallint", "mediumint", "tinyint", "decimal")
TEXT_TYPES = ("char", "varchar", "text", "tinytext", "mediumtext")
ID_NAME = re.compile(r"(^id$|_id$|^uid$|^cid$|_no$|_number$|^ref|_ref$|reference|txn|transaction|order|"
                     r"lead|application|invoice|booking|uuid|code$)", re.I)
EMAIL_NAME = re.compile(r"e_?mail", re.I)
PHONE_NAME = re.compile(r"mobile|phone|contact|whatsapp", re.I)


def classify(value):
    v = (value or "").strip()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        return "email"
    digits = re.sub(r"[\s+\-()]", "", v)
    if re.fullmatch(r"\d{10,13}", digits) and (v.startswith("+") or len(digits) in (10, 12)):
        return "phone"
    if re.fullmatch(r"-?\d+", v):
        return "number"
    return "text"


def _base_type(t):
    return (t or "").lower().split("(")[0].replace(" unsigned", "").strip()


def candidates(schema, value, table_filter="", include_unindexed_small=False, include_primary_ids=False,
               column_filter=""):
    """(table, column, index) pairs worth searching for this value, most selective first.

    By default only indexed columns are searched, and for numbers the `id` primary key of every
    table is skipped — almost every table has a row with id = N, which says nothing about the record.
    """
    kind = classify(value)
    tf = (table_filter or "").strip().lower()
    cf = (column_filter or "").strip().lower()
    out = []
    for r in schema.columns.itertuples():
        if tf and tf not in r.tbl.lower():
            continue
        if cf and cf not in r.col.lower():
            continue
        if privacy.is_secret_column(r.col):
            continue
        base = _base_type(r.type)
        is_num, is_text = base in NUMERIC_TYPES, base in TEXT_TYPES
        name = r.col
        if kind == "email":
            ok = is_text and EMAIL_NAME.search(name)
        elif kind == "phone":
            ok = (is_text or base in ("bigint", "decimal")) and PHONE_NAME.search(name)
        elif kind == "number":
            ok = (is_num or (is_text and "char" in base)) and (ID_NAME.search(name) or PHONE_NAME.search(name))
            if base == "tinyint":
                ok = False
            if ok and name == "id" and r.key == "PRI" and not (include_primary_ids or tf or cf == "id"):
                ok = False
        else:
            ok = is_text and "char" in base and ID_NAME.search(name)
        if not ok:
            continue
        indexed = r.key in ("PRI", "UNI", "MUL")
        if not indexed and not (include_unindexed_small and schema.rows_est.get(r.tbl, 0) < UNINDEXED_ROW_LIMIT):
            continue
        rank = {"PRI": 0, "UNI": 1, "MUL": 2}.get(r.key, 3)
        out.append((rank, r.tbl, r.col, r.key or ""))
    out.sort()
    return [(t, c, k) for _, t, c, k in out]


def count_statements(pairs, value, cap=CAP, batch=BATCH):
    """UNION ALL statements that count matches per (table, column), capped at `cap`.

    Returns a list of (label, sql, params) ready for the query runner.
    """
    statements = []
    for start in range(0, len(pairs), batch):
        chunk = pairs[start:start + batch]
        parts, params = [], []
        for t, c, _ in chunk:
            _check_name(t)
            _check_name(c)
            parts.append(
                f"SELECT '{t}' AS tbl, '{c}' AS col, COUNT(*) AS matches "
                f"FROM (SELECT 1 FROM `{t}` WHERE `{c}` = %s LIMIT {int(cap)}) AS x")
            params.append(value)
        statements.append((f"batch {start // batch + 1}", " UNION ALL ".join(parts), params))
    return statements


def single_statement(table, column, value, cap=CAP):
    _check_name(table)
    _check_name(column)
    return (f"{table}.{column}",
            f"SELECT '{table}' AS tbl, '{column}' AS col, COUNT(*) AS matches "
            f"FROM (SELECT 1 FROM `{table}` WHERE `{column}` = %s LIMIT {int(cap)}) AS x", [value])


def _check_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_$]+", name or ""):
        raise ValueError(f"unsafe identifier {name!r}")
