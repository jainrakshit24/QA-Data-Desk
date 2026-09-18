# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""A short plain-English summary of a SELECT, without AI. Good enough to know what a step does at a glance."""
import re

CLAUSES = ["select", "from", "where", "group by", "having", "order by", "limit"]


def _strip(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"(--\s|#)[^\n]*", " ", sql)
    return re.sub(r"\s+", " ", sql).strip().rstrip(";")


def _top_level_split(sql):
    """Clause → text for the outermost query only (ignores sub-queries in brackets)."""
    depth, marks, low = 0, [], sql.lower()
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch in "'\"`":
            j = sql.find(ch, i + 1)
            i = len(sql) if j < 0 else j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            for c in CLAUSES:
                if low.startswith(c, i) and (i == 0 or not low[i - 1].isalnum() and low[i - 1] != "_") \
                        and (i + len(c) == len(low) or not (low[i + len(c)].isalnum() or low[i + len(c)] == "_")):
                    marks.append((i, c))
                    i += len(c) - 1
                    break
        i += 1
    parts = {}
    for n, (pos, c) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(sql)
        parts.setdefault(c, sql[pos + len(c):end].strip())
    return parts


def _name(x):
    return x.replace("`", "")


def summary(sql):
    sql = _strip(sql or "")
    if not re.match(r"(?i)^\(?\s*(select|with)\b", sql):
        return ""
    lead = "Uses a named sub-query (WITH) first. " if re.match(r"(?i)^with\b", sql) else ""
    if lead:
        # explain the final SELECT after the CTE list
        depth, idx = 0, None
        for m in re.finditer(r"[()]|\bselect\b", sql, flags=re.I):
            t = m.group(0)
            depth += {"(": 1, ")": -1}.get(t, 0)
            if t.lower() == "select" and depth == 0:
                idx = m.start()
        sql = sql[idx:] if idx is not None else sql
    p = _top_level_split(sql)
    out = []
    sel = p.get("select", "")
    frm = p.get("from", "")
    aggregates = re.findall(r"(?i)\b(count|sum|avg|min|max|group_concat)\s*\(", sel)
    if re.fullmatch(r"(?i)(distinct\s+)?(\w+\.)?\*", sel.strip()):
        what = "every column"
    elif aggregates and not p.get("group by"):
        what = "a single summary row (" + ", ".join(dict.fromkeys(a.upper() for a in aggregates)) + ")"
    else:
        n = len([c for c in re.split(r",(?![^(]*\))", sel) if c.strip()])
        what = f"{n} column{'s' if n != 1 else ''}"
    if re.match(r"(?i)distinct\b", sel.strip()):
        what += ", without duplicates"
    tables = [_name(t) for t in re.findall(r"(?i)(?:^|\bjoin\s+|,\s*)(`?[\w$]+`?(?:\.`?[\w$]+`?)?)", frm)
              if t.lower() not in ("select",)]
    joins = re.findall(r"(?i)\b(left|right|inner|cross)?\s*join\b", frm)
    source = "a sub-query" if frm.startswith("(") else (tables[0] if tables else "?")
    text = f"Returns {what} from {source}"
    if len(tables) > 1:
        kinds = {j.lower() for j in joins}
        text += " joined with " + ", ".join(tables[1:])
        if "left" in kinds:
            text += " (keeping rows that have no match)"
    out.append(text + ".")
    if p.get("where"):
        cond = p["where"]
        bits = []
        if re.search(r"(?i)\bis null\b", cond):
            bits.append("something is missing (IS NULL)")
        if re.search(r"(?i)\bnot exists\b|\bnot in\s*\(", cond):
            bits.append("a matching row does not exist")
        if re.search(r":\w+|%s", cond):
            bits.append("it matches the values you fill in")
        n = len(re.findall(r"(?i)\band\b|\bor\b", cond)) + 1
        out.append(f"Keeps only rows where {' and '.join(bits) if bits else 'the ' + str(n) + ' condition' + ('s' if n > 1 else '') + ' hold'}.")
    if p.get("group by"):
        out.append(f"Groups rows by {_name(p['group by'])}" + (" and keeps groups matching the HAVING condition." if p.get("having") else "."))
    if p.get("order by"):
        direction = "largest / newest first" if re.search(r"(?i)\bdesc\b", p["order by"]) else "smallest / oldest first"
        cols = _name(re.sub(r"(?i)\s+(asc|desc)\b", "", p["order by"]))
        out.append(f"Sorted by {cols}, {direction}.")
    if p.get("limit"):
        out.append(f"At most {p['limit'].split(',')[-1].strip()} rows.")
    return lead + " ".join(out)
