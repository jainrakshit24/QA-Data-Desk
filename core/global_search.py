"""One search box over everything the app knows about: tables, columns, saved queries, checks, playbooks,
consistency rules and query history. Works from cached metadata — no database queries."""
import re

KINDS = ["Table", "Column", "Saved query", "QA check", "Playbook", "Consistency rule", "History"]


def _score(term, *fields):
    """Higher is better; 0 means no match. Exact name > starts with > whole word > anywhere."""
    t = term.lower()
    best = 0
    for i, f in enumerate(fields):
        f = str(f or "").lower()
        if not f:
            continue
        weight = 1.0 if i == 0 else 0.5
        if f == t:
            best = max(best, 100 * weight)
        elif f.startswith(t):
            best = max(best, 60 * weight)
        elif re.search(rf"(^|[^a-z0-9]){re.escape(t)}", f):
            best = max(best, 40 * weight)
        elif t in f:
            best = max(best, 20 * weight)
    return best


def search(term, schema=None, saved_queries=(), checks=(), playbooks=(), rules=(), history=(), limit_per_kind=25):
    term = (term or "").strip()
    if len(term) < 2:
        return []
    words = [w for w in re.split(r"\s+", term) if w]
    results = []

    def add(kind, title, detail, target, *fields):
        score = min(_score(w, *fields) for w in words) if len(words) > 1 else _score(term, *fields)
        if score:
            results.append({"kind": kind, "title": title, "detail": detail, "target": target, "score": score})

    if schema is not None:
        for name in schema.names:
            add("Table", name, f"~{schema.rows_est.get(name, 0):,} rows · {len(schema.cols_by_table.get(name, []))} columns",
                {"table": name}, name)
        seen = 0
        for r in schema.columns.itertuples():
            if term.lower() in r.col.lower():
                add("Column", f"{r.tbl}.{r.col}", r.type, {"table": r.tbl, "column": r.col}, r.col, r.tbl)
                seen += 1
                if seen > 2000:
                    break
    for q in saved_queries:
        add("Saved query", q["title"], f"by {q.get('owner', '')} · {q.get('tags') or 'no tags'}",
            {"query_id": q["id"], "sql": q["sql"]}, q["title"], q.get("tags"), q.get("category"), q.get("notes"), q["sql"])
    for c in checks:
        add("QA check", c["title"], f"{c.get('severity', '')} · {c.get('area', '')}", {"check_id": c["id"], "title": c["title"]},
            c["title"], " ".join(c.get("tags") or []), c.get("area"), c.get("why"), c.get("sql"))
    for p in playbooks:
        spec = p["spec"]
        add("Playbook", spec["name"], p.get("origin", ""), {"playbook_key": p["key"]},
            spec["name"], spec.get("objective"), " ".join(s.get("sql", "") for s in spec.get("steps", [])))
    for r in rules:
        spec = r.get("spec") or {}
        add("Consistency rule", r["name"],
            f"{(spec.get('source') or {}).get('table', '?')} → {(spec.get('target') or {}).get('table', '?')}",
            {"rule_id": str(r["id"])}, r["name"], (spec.get("source") or {}).get("table"), (spec.get("target") or {}).get("table"))
    for h in history:
        add("History", (h["sql"] or "")[:90], f"{h.get('ran_at', '')[:16].replace('T', ' ')} · {h.get('source', '')}",
            {"sql": h["sql"]}, h["sql"])

    out = []
    for kind in KINDS:
        items = sorted([r for r in results if r["kind"] == kind], key=lambda r: -r["score"])
        if kind == "History":           # the same SQL run many times appears once
            items = list({r["title"]: r for r in reversed(items)}.values())[::-1]
        out.extend(items[:limit_per_kind])
    return out
