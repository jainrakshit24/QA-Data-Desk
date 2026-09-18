"""Investigate one record: its fields, linked records, a relationship graph and a timeline."""
import datetime as dt

import pandas as pd
import streamlit as st

import privacy
import store
import ui
from core import evidence, records, search, snapshots, timeline


def render():
    st.title("Investigate record")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    target = st.session_state.get("rec_target") or {}
    _breadcrumb()

    tables = sorted(s.names)
    a, b, c, d = st.columns([3, 2.2, 2, 1], vertical_alignment="bottom")
    t_default = tables.index(target["table"]) if target.get("table") in s.names else None
    table = a.selectbox("Table", tables, index=t_default, key=f"rec_tbl_{target.get('table')}",
                        placeholder="Choose a table", format_func=lambda n: f"{n} · ~{s.rows_est.get(n, 0):,} rows")
    if not table:
        st.info("Open a record from Start investigation, or choose a table, a column and a value here.")
        return
    cols = records.safe_columns(s, table)
    key_col = target.get("column") if target.get("table") == table and target.get("column") in cols else \
        next((c_ for c_ in cols if s.key_of.get((table, c_)) == "PRI"), cols[0] if cols else None)
    column = b.selectbox("Column", cols, index=cols.index(key_col) if key_col in cols else 0,
                         key=f"rec_col_{table}_{target.get('column')}",
                         format_func=lambda c_: ui.indexed_label(c_, s.key_of.get((table, c_))))
    value = c.text_input("Value", value=target.get("value", "") if target.get("table") == table else "",
                         key=f"rec_val_{table}_{target.get('value')}")
    if d.button("Open", type="primary", disabled=not value.strip()):
        ui.open_record(table, column, value.strip())

    if not (target.get("table") == table and target.get("column") == column and target.get("value") == value.strip()):
        if value.strip():
            st.caption("Press Open to load this record.")
        return
    if not s.key_of.get((table, column)) and s.rows_est.get(table, 0) > 1_000_000:
        st.warning(f"`{column}` is not indexed on a table of ~{s.rows_est.get(table, 0):,} rows — this may take a while.")

    rkey = (table, column, value.strip())
    cache = st.session_state.setdefault("rec_cache", {})
    if rkey not in cache:
        with st.spinner("Loading record…"):
            cache[rkey] = {"rows": ui.run_query(records.select_rows_sql(s, table, column), [value.strip()],
                                                source="record"), "counts": {}, "timeline": None, "samples": {}}
        for k in list(cache)[:-6]:   # keep the last few records only
            cache.pop(k, None)
    state = cache[rkey]
    res = state["rows"]
    if res["error"]:
        st.error(res["error"])
        return
    df = res["df"]
    if df.empty:
        st.info(f"No row in `{table}` has `{column}` = {value}.")
        return

    pick = 0
    if len(df) > 1:
        st.caption(f"{len(df)} rows have this value{' (showing the first 50)' if len(df) >= 50 else ''}. Pick one to follow its links.")
        pick = st.selectbox("Row", range(len(df)), key=f"rec_row_{rkey}",
                            format_func=lambda i: records.label_for(table, df.iloc[i].to_dict(), column).replace("\n", " · "))
    row = df.iloc[int(pick)].to_dict()
    pk = next((c_ for c_ in cols if s.key_of.get((table, c_)) == "PRI"), column)

    with st.container(border=True):
        st.markdown(f"### {table} · `{column}` = {evidence.scrub(value)}")
        fields = pd.DataFrame({"field": list(row.keys()), "value": [_fmt(v) for v in row.values()]})
        masked = privacy.mask(pd.DataFrame([row]), ui.is_admin()).iloc[0].to_dict()
        fields["value"] = [_fmt(masked[k]) for k in row.keys()]
        st.dataframe(fields, hide_index=True, width="stretch", height=min(360, 38 + 35 * len(fields)))
        if st.button("Add this record to evidence", key=f"rec_ev_{rkey}"):
            ui.add_evidence(evidence.make_item("record", f"{table} record ({column} = {value})",
                                               df=pd.DataFrame([row]), sql=res["sql"]))

    rels = records.relations(s, table, row)
    tabs = ui.lazy_tabs([f"Linked records ({len(rels)})", "Graph", "Timeline", "All matching rows", "Snapshots"],
                        key=f"rec_tabs_{rkey}")
    if tabs[0].open:
        with tabs[0]:
            _linked(s, table, pk, row, rels, state, rkey)
    if tabs[1].open:
        with tabs[1]:
            _graph(s, table, row, column, rels, state, rkey)
    if tabs[2].open:
        with tabs[2]:
            _timeline(s, table, df, rels, state, rkey)
    if tabs[3].open:
        with tabs[3]:
            ui.show_result(res, key=f"rec_all_{abs(hash(rkey))}", evidence_title=f"{table} rows ({column} = {value})",
                           evidence_kind="record")
    if tabs[4].open:
        with tabs[4]:
            _snapshots(table, pk, row, state, rkey)


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "NULL"
    return str(v)


def _breadcrumb():
    trail = st.session_state.get("rec_trail") or []
    if len(trail) < 2:
        return
    cols = st.columns(min(len(trail), 6))
    for i, (col, t) in enumerate(zip(cols, trail[-6:])):
        label = f"{t['table']} {t['column']}={t['value']}"
        if col.button(label[:38], key=f"crumb_{i}_{label}", help=label, type="tertiary"):
            idx = len(trail) - len(trail[-6:]) + i
            del trail[idx + 1:]
            ui.open_record(t["table"], t["column"], t["value"], push=False)


def _count(s, rels, state, only_cheap=True):
    todo = [r for r in rels if records._key(r) not in state["counts"] and (records.countable(r, s) or not only_cheap)]
    if not todo:
        return
    results = ui.run_many([(records._key(r), *search.single_statement(r["table"], r["column"], str(r["value"]))[1:])
                           for r in todo], source="record links")
    for (k, res) in results:
        state["counts"][k] = None if res["error"] else int(res["df"].iloc[0]["matches"])


def _linked(s, table, pk, row, rels, state, rkey):
    if not rels:
        st.info("No links found for this record. Links come from foreign keys, local/links.yaml and the "
                "<name>_id naming pattern — add missing ones to local/links.yaml.")
        return
    with st.spinner("Counting linked records…"):
        _count(s, rels, state)
    slow = [r for r in rels if records._key(r) not in state["counts"]]
    view = pd.DataFrame([{
        "direction": r["direction"],
        "table": r["table"],
        "match": f"{r['column']} = {r['value']}",
        "rows": ("not counted" if records._key(r) not in state["counts"]
                 else "error" if state["counts"][records._key(r)] is None
                 else f"{state['counts'][records._key(r)]:,}+" if state["counts"][records._key(r)] >= search.CAP
                 else f"{state['counts'][records._key(r)]:,}"),
        "via": f"{table}.{r['this_column']}", "found by": r["how"],
    } for r in rels])
    ui.restore(f"rec_only_{rkey}", f"rec_pick_{rkey}")
    only = st.toggle("Only links that have rows", value=True, key=f"rec_only_{rkey}")
    keep = [i for i, r in enumerate(rels) if not only or state["counts"].get(records._key(r)) not in (0,)]
    st.dataframe(view.iloc[keep], hide_index=True, width="stretch", height=min(420, 38 + 35 * max(1, len(keep))))
    if slow and st.button(f"Count {len(slow)} more links on large unindexed tables (may be slow)", key=f"rec_slow_{rkey}"):
        with st.spinner("Counting…"):
            _count(s, slow, state, only_cheap=False)
        st.rerun()

    options = [i for i in keep]
    if not options:
        return
    i = st.selectbox("Linked table", options, key=f"rec_pick_{rkey}",
                     format_func=lambda i: f"{rels[i]['direction']} {rels[i]['table']} · {rels[i]['column']} = {rels[i]['value']}")
    rel = rels[i]
    ui.remember(f"rec_only_{rkey}", f"rec_pick_{rkey}")
    x, y, z, _ = st.columns([1.5, 1.4, 1.8, 3])
    if x.button("Open linked record", type="primary", key=f"rec_go_{rkey}"):
        ui.open_record(rel["table"], rel["column"], rel["value"])
    if y.button("Preview rows", key=f"rec_prev_{rkey}"):
        state["samples"][records._key(rel)] = ui.run_query(
            records.select_rows_sql(s, rel["table"], rel["column"], limit=20), [str(rel["value"])], source="record links")
    if z.button("Open JOIN in SQL editor", key=f"rec_join_{rkey}"):
        ui.set_pending("sql_text", records.join_sql(s, table, pk, rel))
        st.session_state["_pending_sq_param_value"] = str(row.get(pk))
        ui.go("sql")
    sample = state["samples"].get(records._key(rel))
    if sample:
        ui.show_result(sample, key=f"rec_sample_{abs(hash(records._key(rel)))}",
                       evidence_title=f"{rel['table']} rows where {rel['column']} = {rel['value']}", evidence_kind="related")


def _graph(s, table, row, column, rels, state, rkey):
    if not rels:
        st.info("No links to draw.")
        return
    with st.spinner("Counting linked records…"):
        _count(s, rels, state)
    shown = [r for r in rels if state["counts"].get(records._key(r)) != 0] if st.toggle(
        "Hide links with no rows", value=True, key=f"rec_ghide_{rkey}") else rels
    st.graphviz_chart(records.graph_dot(table, records.label_for(table, privacy.mask(pd.DataFrame([row]), False)
                                                                  .iloc[0].to_dict(), column), shown, state["counts"]),
                      width="stretch")
    st.caption("Numbers are matching rows (capped at 1,000). “?” means not counted — large unindexed tables are "
               "counted only on request in Linked records.")


def _timeline(s, table, df, rels, state, rkey):
    st.caption("Built only from timestamp columns that exist in these rows — nothing is inferred. "
               "Linked tables contribute up to 20 rows each.")
    if st.button("Build timeline", type="primary", key=f"rec_tl_{rkey}") or state["timeline"] is not None:
        if state["timeline"] is None:
            with st.spinner("Collecting rows from linked tables…"):
                _count(s, rels, state)
                with_rows = [r for r in rels if (state["counts"].get(records._key(r)) or 0) > 0][:25]
                fetched = ui.run_many([(i, records.select_rows_sql(s, r["table"], r["column"], limit=20),
                                        [str(r["value"])]) for i, r in enumerate(with_rows)], source="timeline")
                sources = [(table, df)] + [(with_rows[i]["table"], res["df"]) for i, res in fetched if not res["error"]]
                types = {(t, c_): ty for t, c_, ty in zip(s.columns["tbl"], s.columns["col"], s.columns["type"])
                         if t in {x for x, _ in sources}}
                state["timeline"] = timeline.build_events(sources, types)
        events = state["timeline"]
        if events.empty:
            st.info("None of these rows has a date or time column with a value.")
            return
        a, b, c = st.columns([2, 2, 3])
        tables = sorted(events["table"].unique())
        pick = a.multiselect("Tables", tables, default=tables, key=f"rec_tl_t_{rkey}")
        text = b.text_input("Event contains", key=f"rec_tl_q_{rkey}", placeholder="e.g. created, paid")
        lo, hi = events["time"].min().date(), events["time"].max().date()
        rng = c.date_input("Between", (lo, hi), min_value=lo, max_value=hi, key=f"rec_tl_d_{rkey}")
        view = events[events["table"].isin(pick)]
        if text:
            view = view[view["event"].str.contains(text, case=False)]
        if isinstance(rng, tuple) and len(rng) == 2:
            view = view[(view["time"] >= pd.Timestamp(rng[0])) &
                        (view["time"] < pd.Timestamp(rng[1]) + pd.Timedelta(days=1))]
        view = view.assign(status=view["status"].map(evidence.scrub))
        st.dataframe(view, hide_index=True, width="stretch", height=min(520, 38 + 35 * max(1, len(view))),
                     column_config={"time": st.column_config.DatetimeColumn("time", format="YYYY-MM-DD HH:mm:ss")})
        if st.button("Add timeline to evidence", key=f"rec_tl_ev_{rkey}"):
            ui.add_evidence(evidence.make_item("timeline", f"Timeline for {table} record", df=view))


def _snapshots(table, pk, row, state, rkey):
    st.caption("Manual snapshots: what this record looked like when someone pressed Save. This is not database "
               "history — changes between snapshots are all that can be shown. Values are stored masked; a keyed "
               "fingerprint detects changes to masked fields without keeping the real value.")
    key = store.snapshot_key()
    conn = ui.active_connection()
    key_value = row.get(pk)
    now = snapshots.capture(table, pk, key_value, row, key, state["counts"])
    a, b = st.columns([4, 1.4], vertical_alignment="bottom")
    label = a.text_input("Label", key=f"rec_snap_label_{rkey}", placeholder="e.g. before resume-all, after deploy 42")
    if b.button("Save snapshot", type="primary", key=f"rec_snap_save_{rkey}"):
        store.save_record_snapshot(ui.user()["id"], conn, label, now)
        st.toast("Snapshot saved.")
    saved = store.record_snapshots(conn, table, pk, now["key_digest"])
    if not saved:
        st.info(f"No snapshots yet for {table} {pk} = {now['key_label']}. Save one, reproduce the bug, then compare "
                "with the live record.")
        return
    options = {"now": "Now (live record, not saved)"}
    options.update({str(r["id"]): f"{r['created_at'][:16].replace('T', ' ')} · {r['label'] or 'no label'} · {r['owner'] or '?'}"
                    for r in saved})
    keys = list(options)
    c1, c2 = st.columns(2)
    left = c1.selectbox("Before", keys, index=1, key=f"rec_snap_a_{rkey}", format_func=options.get)
    right = c2.selectbox("After", keys, index=0, key=f"rec_snap_b_{rkey}", format_func=options.get)
    pick = lambda k: now if k == "now" else next(r["data"] for r in saved if str(r["id"]) == k)
    fields, links = snapshots.compare(pick(left), pick(right))
    changed = fields[fields["change"].isin(["changed", "added", "removed"])]
    if changed.empty and links.empty:
        st.success("No differences.")
    else:
        st.markdown(f"**{len(changed)} field{'s' if len(changed) != 1 else ''} changed**")
    only = st.toggle("Only changed fields", value=True, key=f"rec_snap_only_{rkey}")
    st.dataframe(changed if only else fields, hide_index=True, width="stretch")
    if not links.empty:
        st.markdown("**Linked record counts that changed** (only links counted at the time)")
        st.dataframe(links, hide_index=True, width="stretch")
    x, y, _ = st.columns([1.4, 1.4, 4])
    if x.button("Add comparison to evidence", key=f"rec_snap_ev_{rkey}"):
        ui.add_evidence(evidence.make_item(
            "record", f"{table} {pk} = {now['key_label']}: {options[left]} → {options[right]}",
            df=changed if not changed.empty else fields.head(0),
            text=f"{len(changed)} field(s) changed. Manual snapshots, not database history."))
        st.toast("Added to evidence.")
    mine = [r for r in saved if r["user_id"] == ui.user()["id"] or ui.is_admin()]
    if mine:
        with y.popover("Delete a snapshot"):
            d = st.selectbox("Snapshot", [str(r["id"]) for r in mine], format_func=options.get, key=f"rec_snap_del_{rkey}")
            if st.button("Delete", key=f"rec_snap_delgo_{rkey}"):
                store.delete_record_snapshot(int(d), ui.user()["id"], ui.is_admin())
                st.rerun()
