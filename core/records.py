"""One record and everything linked to it: relations, JOIN SQL and a relationship graph."""
import re

import pandas as pd

import privacy
from core import search

RELATED_SAMPLE = 20


def safe_columns(schema, table):
    return [c for c in schema.cols_by_table.get(table, []) if not privacy.is_secret_column(c)]


def select_rows_sql(schema, table, column, limit=50):
    cols = ", ".join(f"`{c}`" for c in safe_columns(schema, table))
    search._check_name(table)
    search._check_name(column)
    return f"SELECT {cols} FROM `{table}` WHERE `{column}` = %s LIMIT {int(limit)}"


def relations(schema, table, row):
    """Links from one row. Each: direction, table, column, value, this_column, indexed, how."""
    out = []
    for r in schema.outgoing(table).itertuples():
        val = row.get(r.column)
        if _empty(val):
            continue
        out.append({"direction": "points to", "table": r.table, "column": r.target_col, "value": val,
                    "this_column": r.column, "how": r.how,
                    "indexed": schema.key_of.get((r.table, r.target_col), "") in ("PRI", "UNI", "MUL")})
    for r in schema.incoming(table).itertuples():
        val = row.get(r.target_col)
        if _empty(val):
            continue
        out.append({"direction": "referenced by", "table": r.table, "column": r.column, "value": val,
                    "this_column": r.target_col, "how": r.how,
                    "indexed": schema.key_of.get((r.table, r.column), "") in ("PRI", "UNI", "MUL")})
    # stable, readable order: things this row points to first, then indexed back-references
    out.sort(key=lambda x: (x["direction"] != "points to", not x["indexed"], x["table"]))
    return out


def _empty(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v) in ("", "0", "None", "NaT")


def countable(rel, schema):
    """Count automatically only when it is cheap: indexed, or a small table."""
    return rel["indexed"] or schema.rows_est.get(rel["table"], 0) < search.UNINDEXED_ROW_LIMIT


def join_sql(schema, table, key_column, rel, limit=100):
    """A JOIN from this record to a related table, with :value as a named parameter."""
    a_cols = safe_columns(schema, table)[:12]
    b_cols = safe_columns(schema, rel["table"])[:12]
    alias_b = "r"
    select = ",\n       ".join([f"a.`{c}` AS `{table}.{c}`" for c in a_cols] +
                               [f"{alias_b}.`{c}` AS `{rel['table']}.{c}`" for c in b_cols])
    return (f"SELECT {select}\n"
            f"FROM `{table}` AS a\n"
            f"JOIN `{rel['table']}` AS {alias_b} ON {alias_b}.`{rel['column']}` = a.`{rel['this_column']}`\n"
            f"WHERE a.`{key_column}` = :value\n"
            f"LIMIT {int(limit)}")


def graph_dot(center_table, center_label, rels, counts):
    """Graphviz DOT: the record in the middle, related tables around it with match counts."""
    def q(s):
        return '"' + str(s).replace('"', "'") + '"'

    lines = ["digraph G {", "  rankdir=LR;", '  graph [bgcolor="transparent", pad="0.2", nodesep="0.25", ranksep="0.9"];',
             '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, color="#C3CCE6", fillcolor="#FFFFFF"];',
             '  edge [color="#8A93A2", fontname="Helvetica", fontsize=9, arrowsize=0.6];',
             f'  center [label={q(center_label)}, fillcolor="#33478F", fontcolor="white", color="#33478F"];']
    for i, rel in enumerate(rels[:40]):
        n = counts.get(_key(rel))
        count_txt = "?" if n is None else (f"{n}+" if n >= search.CAP else str(n))
        node = f"n{i}"
        fill = "#FFFFFF" if n != 0 else "#F1F2F5"
        lines.append(f"  {node} [label={q(rel['table'] + chr(10) + rel['column'] + ' = ' + str(rel['value'])[:24])}, "
                     f'fillcolor="{fill}"];')
        if rel["direction"] == "points to":
            lines.append(f"  center -> {node} [label={q(rel['this_column'] + '  (' + count_txt + ')')}];")
        else:
            lines.append(f"  {node} -> center [label={q(count_txt + (' row' if n == 1 else ' rows'))}];")
    if len(rels) > 40:
        lines.append(f'  more [label={q(f"+{len(rels) - 40} more links")}, shape=plaintext];')
    lines.append("}")
    return "\n".join(lines)


def _key(rel):
    return (rel["table"], rel["column"], str(rel["value"]))


def label_for(table, row, key_column):
    val = row.get(key_column)
    name = next((row[c] for c in ("name", "title", "display_name", "full_name") if c in row and not _empty(row[c])), None)
    label = f"{table}\n{key_column} = {val}"
    if name is not None:
        label += "\n" + re.sub(r"\s+", " ", str(name))[:30]
    return label
