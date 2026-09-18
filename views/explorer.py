# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Find data: type a table name or topic, then browse, filter, count and follow links — no SQL needed."""
import uuid

import pandas as pd
import streamlit as st

import ai
import db
import privacy
import ui

from core.filters import NO_VALUE, OPS  # noqa: E402  (re-exported for older imports)
from core import filters as core_filters  # noqa: E402
from core import schema_graph  # noqa: E402

HINTS = {"between": "two values, comma separated: 2026-09-01, 2026-09-30",
         "one of": "comma separated: 101, 102, 103"}


def build_where(filters, types):
    """Filters from the Rows tab, using the latest widget values even while that tab is closed."""
    resolved = []
    for f in filters:
        if "uid" in f:
            f = dict(f, col=ui.kept(f"ex_fc_{f['uid']}", f.get("col")), op=ui.kept(f"ex_fo_{f['uid']}", f.get("op")),
                     val=ui.kept(f"ex_fv_{f['uid']}", f.get("val")))
        resolved.append(f)
    return core_filters.build_where(resolved, types)


def open_table(name):
    st.session_state.ex_table = name


def render():
    st.title("Find data")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    st.caption(f"{len(s.names):,} tables in **{db.database_name(s.connection)}** · "
               f"table list refreshed {ui.cache_age(s)} · nothing here can change the database")

    ui.apply_pending("ex_term")
    ui.apply_pending("ex_mode")
    left, right = st.columns([4, 1.3])
    term = left.text_input(
        "What are you looking for?", key="ex_term",
        placeholder="Type part of a table name or a topic — orders, user, audit log, payment…")
    mode = right.radio("Search in", ["Table names", "Column names"], key="ex_mode", horizontal=True)

    if mode == "Table names":
        matches = s.find_tables(term, limit=60)
        names = matches["name"].tolist()
    else:
        found = s.find_columns(term)
        names = list(dict.fromkeys(found["tbl"].tolist()))
        if term:
            with st.expander(f"Columns matching “{term}” — {len(found):,} found in {len(names):,} tables",
                             expanded=True):
                show = found[["tbl", "col", "type", "key"]].rename(
                    columns={"tbl": "table", "col": "column", "key": "index"})
                st.dataframe(show, width="stretch", hide_index=True, height=240)

    if term != st.session_state.get("ex_last_term"):
        st.session_state.ex_last_term = term
        if term and names:
            st.session_state.ex_table = names[0]

    current = st.session_state.get("ex_table")
    if term and not names:
        st.info(f"No table matches “{term}”. Try a shorter word, switch to Column names, or ask the AI below.")

    options = list(names)
    if current and current in s.names and current not in options:
        options.insert(0, current)
    if options:
        est = s.rows_est
        idx = options.index(current) if current in options else 0
        choice = st.selectbox(
            f"Open a table ({len(names)} match{'es' if len(names) != 1 else ''})", options, index=idx,
            format_func=lambda n: f"{n}   ·   ~{est.get(n, 0):,} rows")
        if choice != current:
            st.session_state.ex_table = choice
            current = choice

    _ai_suggestions(s, term)

    if not current or current not in s.names:
        st.info("Type a table name or topic above to start. Partial words work — “user log” finds users_log and user_login_log.")
        return
    _table_view(s, current)


def _ai_suggestions(s, term):
    if not ai.configured():
        return
    with st.expander("Not sure which table? Describe what you need and let AI suggest tables"):
        q = st.text_input("Describe the data", key="ex_ai_q",
                          placeholder="e.g. where are payment refunds stored, and which table has the order status?")
        if st.button("Suggest tables", key="ex_ai_go", disabled=not q):
            with st.spinner("Asking Gemini…"):
                try:
                    st.session_state.ex_ai_out = ai.suggest_tables(q, s)
                except ai.AIError as e:
                    st.session_state.ex_ai_out = str(e)
        out = st.session_state.get("ex_ai_out")
        if isinstance(out, str):
            st.error(out)
        elif out:
            for i, item in enumerate(out):
                a, b = st.columns([5, 1])
                a.markdown(f"**{item['table']}** — {item.get('why', '')}")
                b.button("Open", key=f"ex_ai_open_{i}", on_click=open_table, args=(item["table"],))


def _table_view(s, t):
    meta = s.tables[s.tables["name"] == t].iloc[0]
    cols = s.table_columns(t)
    types = {c: ty for c, ty in zip(cols["col"], cols["type"]) if not privacy.is_secret_column(c)}
    stale = [k for k in (f"ex_cols_{t}", f"ex_sort_{t}", f"ex_g1_{t}", f"ex_g2_{t}")
             if k in st.session_state and st.session_state[k] and
             any(c not in types for c in (st.session_state[k] if isinstance(st.session_state[k], list)
                                          else [st.session_state[k]]) if not str(c).startswith("("))]
    for k in stale:
        del st.session_state[k]  # a chosen column no longer exists
    keys = dict(zip(cols["col"], cols["key"]))
    safe_cols = [c for c in cols["col"] if c in types]
    rows_est = int(meta["rows_est"])
    if state_retry := st.session_state.pop(f"ex_retry_{t}", None):
        st.toast(state_retry)

    st.divider()
    st.subheader(t)
    note = f"~{rows_est:,} rows · {len(cols)} columns"
    if meta.get("comment"):
        note += f" · {meta['comment']}"
    st.caption(note)
    if rows_est > 1_000_000:
        st.warning(f"Large table (~{rows_est:,} rows). Filter on a column marked 🔑 (indexed) "
                   "so queries finish within 60 seconds.")

    state = st.session_state.setdefault("ex_state", {}).setdefault(
        t, {"filters": [], "result": None, "count": None, "group": None, "linked": None})

    tab_rows, tab_group, tab_cols, tab_links = ui.lazy_tabs(
        ["Rows & filters", "Count & group", "Columns", "Linked tables"], key=f"ex_tabs_{t}")
    if tab_rows.open:
        with tab_rows:
            _rows_tab(s, t, state, safe_cols, types, keys)
    if tab_group.open:
        with tab_group:
            _group_tab(t, state, safe_cols, types, keys, rows_est)
    if tab_cols.open:
        with tab_cols:
            _columns_tab(s, t, cols)
    if tab_links.open:
        with tab_links:
            _links_tab(s, t)


def _filter_editor(t, state, safe_cols, types, keys):
    label = lambda c: ui.indexed_label(c, keys.get(c))
    for f in list(state["filters"]):
        uid = f["uid"]
        ui.restore(f"ex_fc_{uid}", f"ex_fo_{uid}", f"ex_fv_{uid}")
        a, b, c, d = st.columns([3, 2, 3, 0.5], vertical_alignment="bottom")
        f["col"] = a.selectbox("Column", safe_cols, index=safe_cols.index(f["col"]) if f["col"] in safe_cols else 0,
                               key=f"ex_fc_{uid}", format_func=label)
        f["op"] = b.selectbox("Condition", OPS, index=OPS.index(f["op"]), key=f"ex_fo_{uid}")
        if f["op"] in NO_VALUE:
            c.text_input("Value", value="—", disabled=True, key=f"ex_fvd_{uid}")
        else:
            f["val"] = c.text_input("Value", value=f.get("val", ""), key=f"ex_fv_{uid}",
                                    placeholder=HINTS.get(f["op"], "value"))
        ui.remember(f"ex_fc_{uid}", f"ex_fo_{uid}", f"ex_fv_{uid}")
        if d.button("✕", key=f"ex_fx_{uid}", help="Remove this filter"):
            state["filters"] = [x for x in state["filters"] if x["uid"] != uid]
            st.rerun()
    default_col = next((c for c in safe_cols if keys.get(c) == "PRI"), safe_cols[0] if safe_cols else None)
    if st.button("＋ Add filter", key=f"ex_add_{t}", disabled=not safe_cols):
        state["filters"].append({"uid": uuid.uuid4().hex[:8], "col": default_col, "op": "equals", "val": ""})
        st.rerun()


def _rows_tab(s, t, state, safe_cols, types, keys):
    if not safe_cols:
        st.info("This table only has hidden columns.")
        return
    _filter_editor(t, state, safe_cols, types, keys)
    row_keys = (f"ex_cols_{t}", f"ex_sort_{t}", f"ex_dir_{t}", f"ex_lim_{t}")
    ui.restore(*row_keys)
    a, b, c, d = st.columns([4, 2, 1.2, 1.2])
    chosen = a.multiselect("Columns to show (empty = all)", safe_cols, key=f"ex_cols_{t}")
    sort = b.selectbox("Sort by", ["(table order)"] + safe_cols, key=f"ex_sort_{t}",
                       format_func=lambda x: x if x.startswith("(") else ui.indexed_label(x, keys.get(x)))
    desc = c.selectbox("Order", ["Newest / largest first", "Oldest / smallest first"], key=f"ex_dir_{t}")
    limit = d.selectbox("Rows", [50, 100, 500, 1000, 5000], index=1, key=f"ex_lim_{t}")

    ui.remember(*row_keys)
    where, params = build_where(state["filters"], types)
    show_cols = chosen or safe_cols
    sql = (f"SELECT {', '.join(db.quote_ident(x) for x in show_cols)} FROM {db.quote_ident(t)}{where}")
    if sort != "(table order)":
        sql += f" ORDER BY {db.quote_ident(sort)} {'DESC' if desc.startswith('Newest') else 'ASC'}"
    sql += f" LIMIT {int(limit)}"

    x, y, z, _ = st.columns([1.2, 1.6, 1.6, 4])
    auto_run = state.pop("run_again", False)
    if x.button("Show rows", type="primary", key=f"ex_run_{t}") or auto_run:
        with st.spinner("Running…"):
            state["result"] = ui.run_query(sql, params, source="find data")
            state["linked"] = None
        if state["result"].get("stale_schema") and not auto_run:
            # The table changed since its columns were cached: refresh done in run_query, run once more.
            state["result"] = None
            state["run_again"] = True
            st.session_state[f"ex_retry_{t}"] = "The table’s columns changed in the database — list refreshed and query re-run."
            st.rerun()
    if y.button("Count matching rows", key=f"ex_count_{t}"):
        with st.spinner("Counting…"):
            state["count"] = ui.run_query(f"SELECT COUNT(*) AS matching_rows FROM {db.quote_ident(t)}{where}",
                                          params, source="find data")
    if z.button("Open in SQL editor", key=f"ex_tosql_{t}"):
        ui.set_pending("sql_text", ui.display_sql(sql, params))
        ui.go("sql")

    cnt = state.get("count")
    if cnt:
        if cnt["error"]:
            st.error(cnt["error"])
        else:
            st.metric("Matching rows", f"{int(cnt['df'].iloc[0, 0]):,}")

    res = state.get("result")
    if res:
        with st.expander("SQL used — copy it or learn from it"):
            st.code(res["sql"], language="sql")
        shown = ui.show_result(res, key=f"ex_{t}")
        if shown is not None and not res["error"] and not res["df"].empty:
            _follow_links(s, t, state, res["df"])


def _follow_links(s, t, state, df):
    out = s.outgoing(t)
    out = out[out["column"].isin(df.columns)]
    inc = s.incoming(t)
    if out.empty and inc.empty:
        return
    with st.expander("Open the records linked to one row"):
        n = st.number_input("Row number from the table above", 1, len(df), 1, key=f"ex_rowpick_{t}")
        row = df.iloc[int(n) - 1]
        if st.button("Load linked records", key=f"ex_follow_{t}"):
            results = []
            with st.spinner("Following links…"):
                for r in out.itertuples():
                    val = row.get(r.column)
                    if pd.isna(val) or str(val) in ("", "0"):
                        continue
                    tcols = [c for c in s.cols_by_table.get(r.table, []) if not privacy.is_secret_column(c)]
                    q = (f"SELECT {', '.join(db.quote_ident(c) for c in tcols)} FROM {db.quote_ident(r.table)} "
                         f"WHERE {db.quote_ident(r.target_col)} = %s LIMIT 20")
                    results.append((f"{r.column} = {val}  →  {r.table}", ui.run_query(q, [val], source="links")))
                for r in inc.itertuples():
                    val = row.get(r.target_col)
                    if val is None or pd.isna(val) or not s.key_of.get((r.table, r.column)):
                        continue  # only indexed back-references, so this stays fast
                    tcols = [c for c in s.cols_by_table.get(r.table, []) if not privacy.is_secret_column(c)]
                    q = (f"SELECT {', '.join(db.quote_ident(c) for c in tcols)} FROM {db.quote_ident(r.table)} "
                         f"WHERE {db.quote_ident(r.column)} = %s LIMIT 20")
                    results.append((f"{r.table}.{r.column} = {val}  (points here)", ui.run_query(q, [val], source="links")))
            state["linked"] = results
        for i, (title, res) in enumerate(state.get("linked") or []):
            rows = 0 if res["error"] or res["df"] is None else len(res["df"])
            with st.container(border=True):
                st.markdown(f"**{title}** — {'error' if res['error'] else f'{rows} row(s)'}")
                if rows or res["error"]:
                    ui.show_result(res, key=f"ex_lnk_{t}_{i}", chart=False)


def _group_tab(t, state, safe_cols, types, keys, rows_est):
    st.caption("Count rows per value — uses the filters from the Rows tab.")
    if not safe_cols:
        return
    label = lambda c: ui.indexed_label(c, keys.get(c))
    ui.restore(f"ex_g1_{t}", f"ex_g2_{t}", f"ex_gtop_{t}")
    a, b, c = st.columns([3, 3, 1.3])
    g1 = a.selectbox("Count rows per", safe_cols, key=f"ex_g1_{t}", format_func=label)
    g2 = b.selectbox("…and split by (optional)", ["(nothing)"] + safe_cols, key=f"ex_g2_{t}", format_func=label)
    top = c.selectbox("Show top", [20, 50, 100, 500], key=f"ex_gtop_{t}")
    ui.remember(f"ex_g1_{t}", f"ex_g2_{t}", f"ex_gtop_{t}")
    if rows_est > 1_000_000 and not state["filters"]:
        st.warning("Grouping a table this large without a filter may hit the 60-second limit.")
    if st.button("Count", type="primary", key=f"ex_grun_{t}"):
        where, params = build_where(state["filters"], types)
        group = [g1] + ([g2] if g2 != "(nothing)" and g2 != g1 else [])
        gsql = ", ".join(db.quote_ident(x) for x in group)
        sql = (f"SELECT {gsql}, COUNT(*) AS `rows` FROM {db.quote_ident(t)}{where} "
               f"GROUP BY {gsql} ORDER BY `rows` DESC LIMIT {int(top)}")
        with st.spinner("Counting…"):
            state["group"] = (ui.run_query(sql, params, source="count & group"), group)
    if state.get("group"):
        res, group = state["group"]
        if res["error"]:
            st.error(res["error"])
            return
        df = privacy.mask(res["df"], ui.is_admin())
        if not df.empty:
            data = df.copy()
            data[group[0]] = data[group[0]].astype(str)
            color = group[1] if len(group) > 1 else None
            if color:
                data[color] = data[color].astype(str)
            st.plotly_chart(ui.draw(data, "Bar", group[0], "rows", color), width="stretch", key=f"ex_gfig_{t}")
        with st.expander("SQL used"):
            st.code(res["sql"], language="sql")
        ui.show_result(res, key=f"ex_grp_{t}", chart=False)


def _columns_tab(s, t, cols):
    view = cols[["col", "type", "nullable", "key", "default", "extra", "comment"]].copy()
    view["links to"] = [
        (lambda l: f"{l[0]}.{l[1]}" if l else "")(s.link_for(t, c)) for c in view["col"]]
    view["note"] = ["hidden (secret)" if privacy.is_secret_column(c) else "" for c in view["col"]]
    view["key"] = view["key"].map({"PRI": "🔑 primary", "UNI": "🔑 unique", "MUL": "🔑 indexed"}).fillna("")
    st.dataframe(view.rename(columns={"col": "column", "key": "index"}), width="stretch", hide_index=True)
    safe = [c for c in cols["col"] if not privacy.is_secret_column(c)]
    st.code(f"SELECT {', '.join(safe)}\nFROM {t}\nLIMIT 100", language="sql")


def _links_tab(s, t):
    out, inc = s.outgoing(t), s.incoming(t)
    st.markdown("**This table points to**")
    if out.empty:
        st.caption("No links found from this table’s columns.")
    else:
        st.dataframe(out[["column", "points_to", "how"]], width="stretch", hide_index=True)
    st.markdown("**Tables that point here**")
    if inc.empty:
        st.caption("No other table links to this one by a known column name or foreign key.")
    else:
        st.dataframe(inc[["table", "column", "matches", "how"]], width="stretch", hide_index=True)
    others = list(dict.fromkeys(out["table"].tolist() + inc["table"].tolist()))
    _graph(s, t)
    if others:
        a, b = st.columns([4, 1], vertical_alignment="bottom")
        pick = a.selectbox("Open a linked table", others, key=f"ex_lnkpick_{t}")
        b.button("Open", key=f"ex_lnkopen_{t}", on_click=open_table, args=(pick,))
    st.caption("Links come from real foreign keys, the name pattern <name>_id → <name>.id, and links you configure. "
               "Add your own in local/links.yaml.")


def _graph(s, t):
    with st.expander("Relationship graph and JOIN builder", expanded=False):
        ui.restore(f"ex_gdepth_{t}", f"ex_gguess_{t}", f"ex_gto_{t}")
        a, b = st.columns([2, 3], vertical_alignment="bottom")
        depth = a.segmented_control("Distance", [1, 2], default=1, key=f"ex_gdepth_{t}",
                                    format_func=lambda d: "Direct links" if d == 1 else "Two steps away")
        guesses = b.toggle("Include name-pattern guesses", value=True, key=f"ex_gguess_{t}")
        tables, edges, cut = schema_graph.neighbourhood(s, t, depth or 1, guesses)
        if len(tables) == 1:
            st.caption("No linked tables.")
        else:
            st.graphviz_chart(schema_graph.dot(tables, edges, t, s.rows_est), width="stretch")
            st.caption(f"{len(tables) - 1} linked tables{' (first 40 shown)' if cut else ''} · arrows point from the "
                       "column to the table it refers to · solid = foreign key, dashed = configured, dotted = name guess")
        targets = sorted(n for n in s.names if n != t)
        goal = st.selectbox("Build a JOIN from this table to", targets, index=None, key=f"ex_gto_{t}",
                            placeholder="Choose any table — the shortest link chain is used")
        ui.remember(f"ex_gdepth_{t}", f"ex_gguess_{t}", f"ex_gto_{t}")
        if goal:
            path = schema_graph.join_path(s, t, goal, guesses)
            if path is None:
                st.info(f"No chain of up to 4 links connects {t} and {goal}.")
                return
            st.caption("Path: " + " → ".join([t] + [p[1] for p in path]))
            sql = schema_graph.join_sql(s, t, path)
            st.code(sql, language="sql")
            if st.button("Open in SQL editor", key=f"ex_gsql_{t}"):
                ui.set_pending("sql_text", sql)
                ui.go("sql")
