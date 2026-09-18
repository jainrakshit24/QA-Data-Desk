"""Table-level relationship graph from the cached schema: neighbourhood, join paths and JOIN SQL. No queries run."""
from collections import deque

import privacy
from core.edge_cases import _q

HOW_STYLE = {"foreign key": "solid", "configured link": "dashed", "name pattern": "dotted"}


def _edges_of(schema, table, include_guesses):
    """(child_table, child_col, parent_table, parent_col, how) for links touching `table`."""
    out = []
    for r in schema.outgoing(table).itertuples():
        out.append((table, r.column, r.table, r.target_col, r.how))
    for r in schema.incoming(table).itertuples():
        out.append((r.table, r.column, table, r.target_col, r.how))
    if not include_guesses:
        out = [e for e in out if e[4] != "name pattern"]
    return out


def neighbourhood(schema, table, depth=1, include_guesses=True, max_tables=40):
    """Tables within `depth` links of `table`. Returns (tables in visit order, unique edges, truncated flag)."""
    seen, edges, queue, truncated = [table], {}, deque([(table, 0)]), False
    while queue:
        t, d = queue.popleft()
        if d >= depth:
            continue
        for e in _edges_of(schema, t, include_guesses):
            other = e[2] if e[0] == t else e[0]
            if other not in seen:
                if len(seen) >= max_tables:
                    truncated = True
                    continue
                seen.append(other)
                queue.append((other, d + 1))
            edges[e[:4]] = e
    edges = [e for e in edges.values() if e[0] in seen and e[2] in seen]
    return seen, edges, truncated


def dot(tables, edges, center, rows_est=None):
    rows_est = rows_est or {}
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent";',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, fillcolor="#f4f6fa", color="#9aa5b8"];',
             'edge [fontname="Helvetica", fontsize=9, color="#7a869a"];']
    for t in tables:
        label = f"{t}\\n~{rows_est.get(t, 0):,} rows"
        extra = ', fillcolor="#dbe8ff", color="#3b6fd8", penwidth=2' if t == center else ""
        lines.append(f'"{t}" [label="{label}"{extra}];')
    for child, col, parent, pcol, how in edges:
        lines.append(f'"{child}" -> "{parent}" [label="{col}", style={HOW_STYLE.get(how, "solid")}, tooltip="{child}.{col} → {parent}.{pcol} ({how})"];')
    lines.append("}")
    return "\n".join(lines)


def join_path(schema, start, goal, include_guesses=True, max_depth=4):
    """Shortest chain of links from start to goal (either direction). List of edges in walking order, or None."""
    if start == goal:
        return []
    prev, queue = {start: None}, deque([(start, 0)])
    while queue:
        t, d = queue.popleft()
        if d >= max_depth:
            continue
        for e in _edges_of(schema, t, include_guesses):
            other = e[2] if e[0] == t else e[0]
            if other in prev:
                continue
            prev[other] = (t, e)
            if other == goal:
                path, cur = [], goal
                while prev[cur]:
                    p, edge = prev[cur]
                    path.append((p, cur, edge))
                    cur = p
                return path[::-1]
            queue.append((other, d + 1))
    return None


def join_sql(schema, start, path, columns_per_table=6, limit=50):
    """SELECT … FROM start JOIN … built from a join_path. Picks a few safe columns per table, aliased table__column."""
    aliases = {start: "t0"}
    joins = []
    for i, (frm, to, (child, col, parent, pcol, how)) in enumerate(path, start=1):
        aliases[to] = f"t{i}"
        if frm == child:              # frm.col → to.pcol
            cond = f"t{i}.{_q(pcol)} = {aliases[frm]}.{_q(col)}"
        else:                         # to.col → frm.pcol
            cond = f"t{i}.{_q(col)} = {aliases[frm]}.{_q(pcol)}"
        joins.append(f"JOIN {_q(to)} AS t{i} ON {cond}   -- {how}")
    select = []
    for t, a in aliases.items():
        cols = [c for c in schema.cols_by_table.get(t, []) if not privacy.is_secret_column(c)]
        keyed = [c for c in cols if schema.key_of.get((t, c)) in ("PRI", "UNI", "MUL")]
        picked = list(dict.fromkeys(keyed + cols))[:columns_per_table]
        select += [f"{a}.{_q(c)} AS {_q(f'{t}__{c}')}" for c in picked]
    return (f"SELECT {', '.join(select)}\nFROM {_q(start)} AS t0\n" + "\n".join(joins) +
            f"\nWHERE t0.{_q(_pk(schema, start))} = :id\nLIMIT {int(limit)}")


def _pk(schema, table):
    cols = schema.cols_by_table.get(table, [])
    return next((c for c in cols if schema.key_of.get((table, c)) == "PRI"), cols[0] if cols else "id")
