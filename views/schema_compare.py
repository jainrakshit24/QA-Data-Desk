# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Schema compare: two connections, or a connection against a saved snapshot of its structure."""
import datetime as dt

import pandas as pd
import streamlit as st

import db
import schema as schema_mod
import store
import ui
from core import evidence
from core import schema_compare as sc


def _sources():
    out = [(f"conn:{n}", f"Live · {n}") for n in db.connection_names()]
    out += [(f"snap:{r['id']}", f"Snapshot · {r['name']} · {r['connection']} · {r['created_at'][:16].replace('T', ' ')} "
                                f"({r['tables']} tables)") for r in store.snapshots()]
    return out


def _load(source_key):
    kind, _, ref = source_key.partition(":")
    if kind == "conn":
        return sc.from_schema(schema_mod.load(ref))
    return store.snapshot_data(int(ref))


def render():
    st.title("Schema compare")
    st.caption("Compare the structure of two databases, or of one database now against a saved snapshot. "
               "Only table and column definitions are compared — no data. Indexes are compared per column "
               "(primary / unique / indexed).")
    if not ui.require_connection():
        return
    conn = ui.active_connection()

    with st.expander("Save a snapshot of the current structure", expanded=not store.snapshots()):
        st.caption("Take a snapshot before a deploy or a data refresh, then compare afterwards to see exactly what changed.")
        a, b = st.columns([3, 1], vertical_alignment="bottom")
        name = a.text_input("Snapshot name", value=f"{conn} · {dt.datetime.now():%d %b %H:%M}", key="sch_snap_name")
        if b.button("Save snapshot", type="primary", key="sch_snap_save"):
            with st.spinner("Reading the current structure…"):
                ui.refresh_schema()
                store.save_snapshot(ui.user()["id"], name, conn, db.database_name(conn), ui.get_schema())
            st.success("Snapshot saved.")
            st.rerun()

    sources = _sources()
    labels = dict(sources)
    keys = [k for k, _ in sources]
    if len(keys) < 2:
        st.info("You need two things to compare: add a second database connection on the Admin page, "
                "or save a snapshot above and compare it with the live database later.")
        return
    default_left = next((k for k in keys if k.startswith("snap:")), keys[0])
    default_right = f"conn:{conn}" if f"conn:{conn}" in keys and f"conn:{conn}" != default_left else keys[-1]
    ui.restore("sch_left", "sch_right")
    st.session_state.setdefault("sch_left", default_left)
    st.session_state.setdefault("sch_right", default_right)
    if st.session_state["sch_left"] not in keys:
        st.session_state["sch_left"] = default_left
    if st.session_state["sch_right"] not in keys:
        st.session_state["sch_right"] = default_right
    c1, c2 = st.columns(2)
    left = c1.selectbox("Left (before / reference)", keys, key="sch_left", format_func=labels.get)
    right = c2.selectbox("Right (after / compared)", keys, key="sch_right", format_func=labels.get)
    ui.remember("sch_left", "sch_right")

    if st.button("Compare", type="primary", disabled=left == right, key="sch_go"):
        with st.spinner("Comparing structures…"):
            a, b = _load(left), _load(right)
            st.session_state.sch_result = {"left": left, "right": right, "diff": sc.diff(a, b),
                                           "counts": sc.row_counts(a, b), "tables": (len(a["tables"]), len(b["tables"]))}
    res = st.session_state.get("sch_result")
    if not res or (res["left"], res["right"]) != (left, right):
        return
    diff, counts = res["diff"], res["counts"]
    s = sc.summary(diff)
    st.caption(f"Left has {res['tables'][0]:,} tables · right has {res['tables'][1]:,} tables · "
               "“Missing” = only on the left · “Extra” = only on the right")
    m = st.columns(5)
    m[0].metric("Missing tables", s["Missing table"])
    m[1].metric("Extra tables", s["Extra table"])
    m[2].metric("Missing columns", s["Missing column"])
    m[3].metric("Extra columns", s["Extra column"])
    m[4].metric("Changed columns", s["Type differs"] + s["Nullable differs"] + s["Index differs"] + s["Default differs"])
    if diff.empty:
        st.success("The two structures are identical.")
    else:
        f1, f2 = st.columns([2, 3])
        kinds = f1.multiselect("Show", sc.KINDS, key="sch_kinds")
        term = f2.text_input("Table or column contains", key="sch_term")
        view = diff
        if kinds:
            view = view[view["difference"].isin(kinds)]
        if term:
            view = view[view["table"].str.contains(term, case=False) | view["column"].str.contains(term, case=False)]
        st.dataframe(view, hide_index=True, width="stretch", height=min(520, 38 + 35 * max(1, len(view))))
    if not counts.empty:
        with st.expander(f"{len(counts)} tables whose approximate row count changed a lot"):
            st.dataframe(counts, hide_index=True, width="stretch")
    report = sc.text_report(diff, labels[left], labels[right], counts)
    a, b, c, _ = st.columns([1.2, 1.2, 1.4, 3])
    a.download_button("Download TXT", report.encode("utf-8"), file_name="schema_compare.txt", mime="text/plain",
                      on_click="ignore")
    b.download_button("Download CSV", diff.to_csv(index=False).encode("utf-8"), file_name="schema_compare.csv",
                      mime="text/csv", on_click="ignore")
    if c.button("Add to evidence", key="sch_ev"):
        ui.add_evidence(evidence.make_item(
            "note", f"Schema differences: {labels[left]} vs {labels[right]}",
            df=diff if not diff.empty else pd.DataFrame([{"result": "identical"}]),
            text=", ".join(f"{k}: {v}" for k, v in s.items() if v)))
    if ui.is_admin():
        with st.expander("Manage snapshots"):
            snaps = store.snapshots()
            if snaps:
                pick = st.selectbox("Snapshot", [r["id"] for r in snaps], key="sch_del_pick",
                                    format_func=lambda i: next(f"{r['name']} ({r['created_at'][:16]})" for r in snaps if r["id"] == i))
                if st.button("Delete snapshot", key="sch_del"):
                    store.delete_snapshot(pick)
                    st.session_state.pop("sch_result", None)
                    st.rerun()
