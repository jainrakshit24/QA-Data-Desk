"""Test data suggestions. This module only builds text — it never connects to a database.

Generated INSERT statements are clearly labelled NOT EXECUTED; QA Data Desk cannot run them (the read-only guard
blocks them). "Find existing" queries are plain SELECTs, so QA can reuse data that is already there instead.
"""
import datetime as dt
import json
import re

import privacy
from core import edge_cases as ec

BIG_TABLE = 1_000_000        # above this estimate, "find" queries look at the newest rows only
BIG_SAMPLE = 50_000
NOT_EXECUTED = ("-- NOT EXECUTED. Suggested by QA Data Desk; this app is read-only and cannot run it.\n"
                "-- Review every value, then run it yourself in a test environment you are allowed to change.\n")


def _value(col, col_type, variant, n=1):
    """A plausible value from the column name and type. variant: 'valid' or an edge variant name."""
    info = ec.parse_type(col_type)
    kind = ec.kind_of(info)
    name = col.lower()
    if privacy.is_secret_column(col):
        return "CHANGE_ME"
    if kind == "text":
        max_len = info["length"] or 255
        if variant == "longest":
            return "x" * max_len
        if variant == "empty":
            return ""
        if variant == "unicode":
            return "नमस्ते 😀 O'Brien"[:max_len]
        if re.search(r"e_?mail", name):
            v = f"qa.test+{n}@example.com"
        elif re.search(r"(phone|mobile|contact|whatsapp)", name):
            v = f"99999{n:05d}"
        elif re.search(r"(url|link|website)", name):
            v = f"https://example.com/qa-test-{n}"
        elif re.search(r"(pin_?code|zip|postal)", name):
            v = "110001"
        elif re.search(r"(^|_)(status|state|stage)$", name):
            v = "active"
        elif re.search(r"slug", name):
            v = f"qa-test-{n}"
        elif re.search(r"name|title", name):
            v = f"QA Test {n}"
        else:
            v = f"qa_test_{n}"
        return v[:max_len]
    if kind == "integer":
        lo, hi = ec.int_limits(info)
        if variant == "longest":
            return hi
        if variant == "empty":
            return 0
        return 1 if re.search(r"(status|active|published|flag)", name) else n
    if kind == "decimal":
        if variant == "longest" and info["precision"]:
            p, s = info["precision"], info["scale"]
            return float("9" * (p - s) + ("." + "9" * s if s else ""))
        return 0 if variant == "empty" else 100.0
    if kind == "boolean":
        return 0 if variant == "empty" else 1
    if kind == "choice":
        return info["values"][0] if info["values"] else ""
    if kind == "date":
        now = dt.datetime(2026, 1, 15, 10, 30, 0)
        if variant == "longest":
            now = dt.datetime(2028, 2, 29, 23, 59, 59)
        if info["base"] == "date":
            return now.strftime("%Y-%m-%d")
        if info["base"] == "time":
            return now.strftime("%H:%M:%S")
        if info["base"] == "year":
            return now.year
        return now.strftime("%Y-%m-%d %H:%M:%S")
    if kind == "json":
        return "{}"
    return None


VARIANTS = {"valid": "Typical valid row", "longest": "Every field at its limit", "empty": "Empty / zero where allowed",
            "unicode": "Unicode, emoji and quotes in text"}


def sample_rows(schema, table, variant="valid", count=1):
    """Rows as {column: value}. Auto-increment and generated columns are left out; links become placeholders."""
    rows, notes = [], []
    frame = schema.table_columns(table)
    for i in range(1, count + 1):
        row = {}
        for r in frame.itertuples():
            extra = str(r.extra or "").lower()
            if "auto_increment" in extra or "generated" in extra:
                continue
            link = schema.link_for(table, r.col)
            if link:
                row[r.col] = f":existing_{link[0]}_{link[1]}"
                if i == 1:
                    notes.append(f"{r.col}: use a real {link[0]}.{link[1]} ({link[2]}) — see “Find existing data”.")
                continue
            if variant == "empty" and str(r.nullable).upper() == "YES":
                row[r.col] = None
                continue
            if r.default is not None and variant == "valid" and not re.search(r"(name|title|mail|phone|mobile)", r.col, re.I):
                continue          # let the database default apply
            row[r.col] = _value(r.col, r.type, variant, i)
            if privacy.is_secret_column(r.col) and i == 1:
                notes.append(f"{r.col}: secret column — set a real test value yourself.")
        rows.append(row)
    return rows, notes


def literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if re.fullmatch(r":existing_[A-Za-z0-9_$]+", s):
        return s                                  # placeholder, deliberately not a valid literal
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


def insert_sql(table, rows):
    if not rows:
        return NOT_EXECUTED
    cols = list(dict.fromkeys(c for r in rows for c in r))
    values = ",\n".join("  (" + ", ".join(literal(r.get(c)) for c in cols) + ")" for r in rows)
    return (NOT_EXECUTED + f"INSERT INTO {ec._q(table)} ({', '.join(ec._q(c) for c in cols)})\nVALUES\n{values};\n")


def as_json(rows):
    return json.dumps(rows, indent=2, ensure_ascii=False, default=str)


# ------------------------------------------------------------------ reuse data that already exists
def find_existing(schema, table):
    """Read-only SELECTs that find rows already in useful states. [{title, sql}]"""
    frame = schema.table_columns(table)
    pk = next((r.col for r in frame.itertuples() if r.key == "PRI"), None)
    order = f" ORDER BY {ec._q(pk)} DESC" if pk else ""
    t = ec._q(table)
    out = [{"title": "Newest rows", "sql": f"SELECT * FROM {t}{order} LIMIT 20"}]
    big = schema.rows_est.get(table, 0) > BIG_TABLE
    src = f"(SELECT * FROM {t}{order} LIMIT {BIG_SAMPLE}) AS s" if big else t
    note = f" (newest {BIG_SAMPLE:,} rows)" if big else ""
    for r in frame.itertuples():
        if privacy.is_secret_column(r.col):
            continue
        q = ec._q(r.col)
        info = ec.parse_type(r.type)
        kind = ec.kind_of(info)
        if ec.STATUS.search(r.col) or kind in ("choice", "boolean"):
            out.append({"title": f"One example for each {r.col}{note}",
                        "sql": f"SELECT {q}, COUNT(*) AS `rows`, MAX({ec._q(pk) if pk else q}) AS `example_{pk or r.col}` "
                               f"FROM {src} GROUP BY {q} ORDER BY `rows` DESC LIMIT 50"})
        if ec.SOFT_DELETE.search(r.col):
            cond = f"{q} IS NOT NULL" if kind == "date" else f"{q} = 1"
            if r.col.lower() == "is_active":
                cond = f"{q} = 0"
            out.append({"title": f"Deleted / inactive rows ({r.col})", "sql": f"SELECT * FROM {t} WHERE {cond}{order} LIMIT 20"})
        if kind == "text":
            out.append({"title": f"{r.col} is empty{note}",
                        "sql": f"SELECT * FROM {src} WHERE ({q} IS NULL OR {q} = ''){order} LIMIT 20"})
        if kind == "text" and info["length"] and info["length"] >= 20:
            out.append({"title": f"Longest {r.col} (newest 5000 rows)",
                        "sql": f"SELECT *, CHAR_LENGTH({q}) AS `{r.col}_length` FROM (SELECT * FROM {t}{order} LIMIT 5000) AS c "
                               f"ORDER BY CHAR_LENGTH({q}) DESC LIMIT 5"})
        link = schema.link_for(table, r.col)
        if link and schema.has(link[0], link[1]):
            out.append({"title": f"{r.col} points to a missing {link[0]}",
                        "sql": f"SELECT c.* FROM (SELECT * FROM {t}{order} LIMIT 5000) AS c "
                               f"LEFT JOIN {ec._q(link[0])} AS p ON p.{ec._q(link[1])} = c.{q} "
                               f"WHERE c.{q} IS NOT NULL AND p.{ec._q(link[1])} IS NULL LIMIT 20"})
    return out
