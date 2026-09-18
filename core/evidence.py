# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Evidence items and Jira-ready bug reports. Everything passes through masking before it is stored."""
import datetime as dt
import os
import re

import pandas as pd

import packs
import privacy

MAX_ROWS_IN_REPORT = 25
PLACEHOLDERS = ["title", "environment", "identifier", "summary", "steps", "expected", "actual",
                "db_evidence", "api_evidence", "mismatches", "sql", "generated_at"]

KINDS = {  # where each evidence kind is placed in the report
    "api": "api_evidence", "rules": "api_evidence", "compare": "mismatches",
    "record": "db_evidence", "related": "db_evidence", "timeline": "db_evidence",
    "query": "db_evidence", "check": "db_evidence", "note": "db_evidence",
}


def make_item(kind, title, df=None, sql=None, text=None):
    """An evidence item with personal data masked and secrets removed — safe to paste anywhere."""
    table = None
    if df is not None:
        safe = privacy.mask(df, is_admin=False)
        table = safe.head(MAX_ROWS_IN_REPORT).astype(str).to_dict("records")
        total = len(df)
    else:
        total = None
    return {
        "kind": kind if kind in KINDS else "note",
        "title": scrub(title or "Evidence"),
        "rows": table,
        "total_rows": total,
        "sql": sql,
        "text": scrub(text) if text else None,
        "added_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(r"(?<!\d)(\+?\d{2})\d{6,9}(\d{2})(?!\d)")
_SECRETISH = re.compile(r'(?i)("?(?:password|passwd|token|access_token|refresh_token|api_key|secret|otp|'
                        r'authorization|aadhaar|pan)"?\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s,}]+)')


def scrub(text):
    """Mask emails, phone numbers and secret values inside free text."""
    if not text:
        return text
    text = _SECRETISH.sub(lambda m: m.group(1) + '"•••• hidden"', str(text))
    text = _EMAIL.sub(lambda m: m.group(1) + "•••" + m.group(2), text)
    return _PHONE.sub(lambda m: m.group(1) + "••••••" + m.group(2), text)


# ------------------------------------------------------------------ rendering
def _table(rows, fmt):
    if not rows:
        return ""
    cols = list(rows[0].keys())
    clip = lambda v: str(v).replace("\n", " ").replace("|", "/")[:120]
    if fmt == "jira":
        head = "||" + "||".join(cols) + "||"
        body = ["|" + "|".join(clip(r.get(c, "")) for c in cols) + "|" for r in rows]
        return "\n".join([head] + body)
    if fmt == "markdown":
        head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
        body = ["| " + " | ".join(clip(r.get(c, "")) for c in cols) + " |" for r in rows]
        return "\n".join([head] + body)
    widths = {c: min(40, max(len(c), *(len(clip(r.get(c, ""))) for r in rows))) for c in cols}
    line = lambda vals: "  ".join(str(v)[:widths[c]].ljust(widths[c]) for c, v in zip(cols, vals))
    return "\n".join([line(cols)] + [line([clip(r.get(c, "")) for c in cols]) for r in rows])


def _code(sql, fmt):
    if not sql:
        return ""
    if fmt == "jira":
        return "{code:sql}\n" + sql.strip() + "\n{code}"
    if fmt == "markdown":
        return "```sql\n" + sql.strip() + "\n```"
    return sql.strip()


def _item_block(item, fmt):
    title = item["title"]
    heading = f"*{title}*" if fmt == "jira" else (f"**{title}**" if fmt == "markdown" else title)
    parts = [heading]
    if item.get("text"):
        parts.append(item["text"])
    if item.get("rows"):
        parts.append(_table(item["rows"], fmt))
        if item.get("total_rows") and item["total_rows"] > len(item["rows"]):
            parts.append(f"(showing {len(item['rows'])} of {item['total_rows']} rows)")
    elif item.get("rows") == [] or item.get("total_rows") == 0:
        parts.append("No rows.")
    return "\n".join(parts)


def sections(items, fmt):
    grouped = {"db_evidence": [], "api_evidence": [], "mismatches": []}
    sqls = []
    for it in items:
        grouped[KINDS.get(it["kind"], "db_evidence")].append(_item_block(it, fmt))
        if it.get("sql"):
            sqls.append(f"-- {it['title']}\n{it['sql'].strip()}")
    return {k: ("\n\n".join(v) if v else "None collected.") for k, v in grouped.items()} | {
        "sql": _code("\n\n".join(sqls), fmt) if sqls else "None."}


def load_template(fmt):
    """local/templates/bug_<fmt>.txt overrides config/templates/bug_<fmt>.txt."""
    name = f"bug_{fmt}.txt"
    for folder in (os.path.join(packs.LOCAL_DIR, "templates"), os.path.join(packs.CONFIG_DIR, "templates")):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            with open(path) as f:
                return f.read()
    raise FileNotFoundError(name)


def render(fields, items, fmt="jira", template=None):
    """fields: title, environment, identifier, summary, steps, expected, actual (strings)."""
    values = {k: scrub((fields.get(k) or "").strip()) or "—" for k in
              ("title", "environment", "identifier", "summary", "steps", "expected", "actual")}
    values["generated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values.update(sections(items, fmt))
    tpl = template if template is not None else load_template(fmt)
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), tpl)
