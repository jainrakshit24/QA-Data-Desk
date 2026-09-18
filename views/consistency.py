# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Data consistency: rules between tables (“every X must have a Y”) and field-by-field table comparison."""
import os

import pandas as pd
import streamlit as st
import yaml

import packs
import privacy
import store
import ui
from core import api_compare as ac
from core import checks as cc
from core import consistency, evidence, filters, table_compare

FILTER_COLS = ["col", "op", "val"]


def render():
    st.title("Data consistency")
    st.caption("Check that related tables agree. Everything runs as read-only queries with limits; "
               "only counts and the rows you look at are fetched.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    rules_tab, compare_tab = ui.lazy_tabs(["Consistency rules", "Compare tables"], key="cons_tabs")
    if rules_tab.open:
        with rules_tab:
            _rules(s)
    if compare_tab.open:
        with compare_tab:
            _compare(s)


# ------------------------------------------------------------------ shared widgets
def _table_pick(s, label, key, default=None):
    ui.restore(key)
    tables = sorted(s.names)
    if key not in st.session_state and default in s.names:
        st.session_state[key] = default
    t = st.selectbox(label, tables, index=None, key=key, placeholder="Choose a table",
                     format_func=lambda n: f"{n} · ~{s.rows_est.get(n, 0):,} rows")
    ui.remember(key)
    return t


def _column_pick(s, table, label, key, default=None):
    cols = [c for c in s.cols_by_table.get(table, []) if not privacy.is_secret_column(c)]
    ui.restore(key)
    if st.session_state.get(key) not in cols:
        st.session_state[key] = default if default in cols else next(
            (c for c in cols if s.key_of.get((table, c)) == "PRI"), cols[0] if cols else None)
    col = st.selectbox(label, cols, key=key, format_func=lambda c: ui.indexed_label(c, s.key_of.get((table, c))))
    ui.remember(key)
    return col


def _filters_editor(s, table, key, initial=None):
    cols = [c for c in s.cols_by_table.get(table, []) if not privacy.is_secret_column(c)]
    df = pd.DataFrame(initial or [], columns=FILTER_COLS)
    edited = ui.sticky_editor(f"{key}_{table}", df, num_rows="dynamic", width="stretch", column_config={
        "col": st.column_config.SelectboxColumn("Column", options=cols, width="medium"),
        "op": st.column_config.SelectboxColumn("Condition", options=filters.OPS, width="small"),
        "val": st.column_config.TextColumn("Value", help="between: two values, comma separated · one of: comma separated"),
    })
    return [{k: ("" if pd.isna(v) else str(v)) for k, v in r.items()} for r in edited.to_dict("records") if r.get("col")]


def _options(prefix):
    with st.expander("Comparison options"):
        c = st.columns(3)
        return ac.Options(
            case_insensitive=c[0].checkbox("Ignore letter case", True, key=f"{prefix}_case"),
            trim=c[0].checkbox("Ignore surrounding spaces", True, key=f"{prefix}_trim"),
            numbers=c[1].checkbox("\"5\" equals 5", True, key=f"{prefix}_num"),
            booleans=c[1].checkbox("true equals 1", True, key=f"{prefix}_bool"),
            dates=c[2].checkbox("Compare dates as times", True, key=f"{prefix}_date"),
            empty_is_null=c[2].checkbox("Empty text equals NULL", False, key=f"{prefix}_empty"))


# ------------------------------------------------------------------ rules
def _local_rules():
    path = os.path.join(packs.LOCAL_DIR, "consistency_rules.yaml")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or []
    return [{"id": f"local:{i}", "name": r.get("name", f"Rule {i + 1}"), "owner": "local/consistency_rules.yaml",
             "spec": r, "user_id": None} for i, r in enumerate(data) if isinstance(r, dict)]


def _rules(s):
    u = ui.user()
    saved = store.rules(u["id"]) + _local_rules()
    labels = {str(r["id"]): f"{r['name']} · {r['owner']}" for r in saved}
    ui.apply_pending("cons_pick")
    ui.restore("cons_pick")
    pick = st.selectbox("Rule", ["(new rule)"] + list(labels), key="cons_pick",
                        format_func=lambda k: k if k == "(new rule)" else labels[k])
    ui.remember("cons_pick")
    chosen = next((r for r in saved if str(r["id"]) == pick), None)
    spec0 = (chosen or {}).get("spec") or {}
    p = f"cons_{pick}"

    c1, c2 = st.columns([3, 1])
    ui.restore(f"{p}_name", f"{p}_sev")
    if f"{p}_name" not in st.session_state:
        st.session_state[f"{p}_name"] = spec0.get("name", "")
    name = c1.text_input("Rule name", key=f"{p}_name", placeholder="Every successful payment has a lead")
    sev_default = spec0.get("severity", "high")
    if f"{p}_sev" not in st.session_state:
        st.session_state[f"{p}_sev"] = sev_default if sev_default in cc.SEVERITIES else "high"
    severity = c2.selectbox("Severity", cc.SEVERITIES, key=f"{p}_sev", format_func=lambda x: cc.SEVERITY_MARK[x])
    ui.remember(f"{p}_name", f"{p}_sev")

    left, right = st.columns(2)
    with left:
        st.markdown("**Every row in**")
        src = _table_pick(s, "Source table", f"{p}_st", (spec0.get("source") or {}).get("table"))
        src_filters, src_key = [], None
        if src:
            st.caption("…that matches (leave empty for all rows)")
            src_filters = _filters_editor(s, src, f"{p}_sf", (spec0.get("source") or {}).get("filters"))
            src_key = _column_pick(s, src, "linked by its column", f"{p}_sk_{src}", (spec0.get("source") or {}).get("key"))
    with right:
        ui.restore(f"{p}_expect")
        if f"{p}_expect" not in st.session_state:
            st.session_state[f"{p}_expect"] = "must have" if spec0.get("expect", "exists") == "exists" else "must not have"
        expect = st.radio("Rule type", ["must have", "must not have"], horizontal=True, key=f"{p}_expect",
                          label_visibility="collapsed")
        ui.remember(f"{p}_expect")
        tgt = _table_pick(s, "a row in target table", f"{p}_tt", (spec0.get("target") or {}).get("table"))
        tgt_filters, tgt_key = [], None
        if tgt:
            tgt_key = _column_pick(s, tgt, "whose column equals it", f"{p}_tk_{tgt}", (spec0.get("target") or {}).get("key"))
            st.caption("…and also matches (optional)")
            tgt_filters = _filters_editor(s, tgt, f"{p}_tf", (spec0.get("target") or {}).get("filters"))

    ui.restore(f"{p}_scope", f"{p}_n")
    if f"{p}_scope" not in st.session_state:
        st.session_state[f"{p}_scope"] = "Newest rows only" if spec0.get("sample") else "All matching rows"
    a, b = st.columns([2, 1])
    scope = a.radio("Check", ["All matching rows", "Newest rows only"], horizontal=True, key=f"{p}_scope")
    n = b.number_input("Newest", 100, 1_000_000, int(spec0.get("sample") or 10_000), step=1000, key=f"{p}_n",
                       disabled=scope != "Newest rows only")
    ui.remember(f"{p}_scope", f"{p}_n")

    spec = {"name": name, "severity": severity,
            "source": {"table": src, "key": src_key, "filters": src_filters},
            "target": {"table": tgt, "key": tgt_key, "filters": tgt_filters},
            "expect": "exists" if expect == "must have" else "not exists",
            "sample": int(n) if scope == "Newest rows only" else 0,
            "source_pk": next((c for c in s.cols_by_table.get(src or "", []) if s.key_of.get((src, c)) == "PRI"), src_key)}
    problems = consistency.validate(spec, s)
    if problems:
        st.info(" ".join(problems))
        return
    st.markdown(f"> {consistency.sentence(spec)}")
    if not spec["sample"] and not src_filters and s.rows_est.get(src, 0) > 1_000_000:
        st.warning(f"`{src}` has ~{s.rows_est[src]:,} rows. Checking all of them may hit the 60-second limit — "
                   "add a filter or check the newest rows only.")

    x, y, z, w, _ = st.columns([1.1, 1.1, 1.5, 1, 2])
    run_key = f"cons_result_{pick}"
    if x.button("Run check", type="primary", key=f"{p}_run"):
        stmts = consistency.statements(spec, s)
        with st.spinner("Counting…"):
            done = dict(ui.run_many([(k, sql, params) for k, (sql, params) in stmts.items()], source="consistency"))
        st.session_state[run_key] = {"spec": spec, "done": done}
    if y.button("Save rule", key=f"{p}_save", disabled=not name.strip() or bool(chosen and chosen["user_id"] is None)):
        rid = store.save_rule(u["id"], name, spec, True, chosen["id"] if chosen and chosen.get("user_id") else None)
        st.session_state["_pending_cons_pick"] = str(rid)
        st.session_state.pop("_keep_cons_pick", None)
        st.toast("Saved and shared with the team.")
        st.rerun()
    if z.button("Save as QA check", key=f"{p}_as_check", disabled=not name.strip(),
                help="Adds a check that lists the failing rows to local/checks.yaml, so it runs with the others"):
        check = consistency.as_check(spec, s, name, severity)
        check["id"] = "consistency_" + "".join(ch if ch.isalnum() else "_" for ch in name.lower())[:40]
        from qa_checks import database_name
        check["database"] = database_name()
        with open(packs.local_file("checks.yaml"), "a") as f:
            f.write("\n" + yaml.safe_dump([check], sort_keys=False, allow_unicode=True, width=120))
        st.success("Added to local/checks.yaml — it now appears on QA checks and the dashboard.")
    if chosen and chosen.get("user_id") and (chosen["user_id"] == u["id"] or ui.is_admin()) and w.button("Delete", key=f"{p}_del"):
        store.delete_rule(chosen["id"], u["id"], ui.is_admin())
        st.session_state.pop("_keep_cons_pick", None)
        st.session_state.pop("cons_pick", None)
        st.rerun()

    result = st.session_state.get(run_key)
    if not result:
        return
    done = result["done"]
    errs = [r["error"] for r in done.values() if r["error"]]
    if errs:
        st.error(errs[0])
        return
    checked = int(done["checked"]["df"].iloc[0, 0])
    failed = int(done["failed"]["df"].iloc[0, 0])
    m = st.columns(4)
    m[0].metric("Checked", f"{checked:,}")
    m[1].metric("Passed", f"{checked - failed:,}")
    m[2].metric("Failed", f"{failed:,}")
    m[3].metric("Pass rate", f"{(100 * (checked - failed) / checked):.2f}%" if checked else "—")
    with st.expander("SQL used"):
        for k, r in done.items():
            st.code(r["sql"], language="sql")
    if failed:
        st.markdown(f"**Failing rows** (first {min(failed, 200)}) — select one to investigate it")
        rows = done["failing_rows"]["df"]
        shown = privacy.mask(rows, ui.is_admin())
        ev = st.dataframe(shown, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                          key=f"{p}_failing")
        sel = ev.selection.rows if ev and ev.selection else []
        pk = result["spec"]["source_pk"]
        c_a, c_b, _ = st.columns([1.8, 1.6, 4])
        if sel and pk in rows.columns and c_a.button("Investigate selected row", type="primary", key=f"{p}_open"):
            ui.open_record(result["spec"]["source"]["table"], pk, rows.iloc[sel[0]][pk])
        if c_b.button("Add to evidence", key=f"{p}_ev"):
            ui.add_evidence(evidence.make_item(
                "check", f"Consistency rule failed: {consistency.sentence(result['spec'])} — {failed} of {checked} rows",
                df=rows, sql=done["failed"]["sql"]))
    else:
        st.success("Every checked row satisfies the rule.")


# ------------------------------------------------------------------ compare tables
def _compare(s):
    left, right = st.columns(2)
    with left:
        src = _table_pick(s, "Source table", "cmp_st")
        skey = _column_pick(s, src, "Source key", f"cmp_sk_{src}") if src else None
        sfil = []
        if src:
            with st.expander("Only source rows that match (optional)"):
                sfil = _filters_editor(s, src, "cmp_sf")
    with right:
        tgt = _table_pick(s, "Target table", "cmp_tt")
        tkey = _column_pick(s, tgt, "Target key (matches the source key)", f"cmp_tk_{tgt}") if tgt else None
        tfil = []
        if tgt:
            with st.expander("Only target rows that match (optional)"):
                tfil = _filters_editor(s, tgt, "cmp_tf")
    if not (src and tgt and skey and tkey):
        st.info("Choose both tables and the columns that link them.")
        return

    s_cols = [c for c in s.cols_by_table.get(src, []) if not privacy.is_secret_column(c)]
    t_cols = [c for c in s.cols_by_table.get(tgt, []) if not privacy.is_secret_column(c)]
    st.markdown("**Fields to compare**")
    fields_df = ui.sticky_editor(f"cmp_fields_{src}_{tgt}",
                                 pd.DataFrame(table_compare.suggest_fields(s, src, tgt, skey, tkey) or [{"source": "", "target": ""}]),
                                 num_rows="dynamic", width="stretch", column_config={
                                     "source": st.column_config.SelectboxColumn(f"{src} column", options=s_cols),
                                     "target": st.column_config.SelectboxColumn(f"{tgt} column", options=t_cols)})
    fields = [{"source": r["source"], "target": r["target"]} for r in fields_df.to_dict("records")
              if r.get("source") and r.get("target") and not pd.isna(r["source"]) and not pd.isna(r["target"])]
    ui.restore("cmp_n")
    n = st.selectbox("Compare the newest", [200, 1000, 5000], index=1, key="cmp_n",
                     format_func=lambda v: f"{v:,} source rows (by key)")
    ui.remember("cmp_n")
    opt = _options("cmp")
    if not fields:
        st.info("Map at least one pair of columns.")
        return

    spec = {"source": {"table": src, "key": skey, "filters": sfil}, "target": {"table": tgt, "key": tkey, "filters": tfil},
            "fields": fields}
    if st.button("Compare", type="primary", key="cmp_go"):
        with st.spinner("Fetching and comparing…"):
            sql, params = table_compare.source_sql(spec, s, n)
            src_res = ui.run_query(sql, params, source="compare tables", max_rows=n)
            out = {"spec": spec, "source": src_res, "error": src_res["error"]}
            if not src_res["error"]:
                batches = table_compare.target_sql_batches(spec, s, src_res["df"][skey].tolist())
                tgt_res = ui.run_many([(i, q, p) for i, (q, p) in enumerate(batches)], source="compare tables")
                errs = [r["error"] for _, r in tgt_res if r["error"]]
                target = pd.concat([r["df"] for _, r in tgt_res if not r["error"]] or [pd.DataFrame()], ignore_index=True)
                rev_sql, rev_params = table_compare.reverse_sql(spec, s, n)
                rev = ui.run_query(rev_sql, rev_params, source="compare tables")
                summary, details = table_compare.compare(spec, src_res["df"], target, opt)
                out.update({"error": errs[0] if errs else None, "summary": summary, "details": details, "reverse": rev,
                            "sql": [sql, batches[0][0] if batches else "", rev_sql]})
        st.session_state.cmp_result = out

    res = st.session_state.get("cmp_result")
    if not res or res["spec"] != spec:
        return
    if res["error"]:
        st.error(res["error"])
        return
    sm, details, rev = res["summary"], res["details"], res["reverse"]
    missing_source = 0 if rev["error"] else len(rev["df"])
    m = st.columns(6)
    m[0].metric("Compared", f"{sm['compared']:,}")
    m[1].metric("Matching", f"{sm['matching']:,}")
    m[2].metric("Mismatched", f"{sm['mismatched']:,}")
    m[3].metric("Missing in target", f"{sm['missing_in_target']:,}")
    m[4].metric("Missing in source", f"{missing_source:,}", help=f"Among the newest {n:,} target rows")
    m[5].metric("Duplicates in target", f"{sm['duplicate_in_target']:,}")
    if not rev["error"] and missing_source:
        details = pd.concat([details, pd.DataFrame({"key": rev["df"]["target_key"], "status": "Missing in source",
                                                    "field": "", "source_value": "", "target_value": ""})], ignore_index=True)
    with st.expander("SQL used"):
        for q in res["sql"]:
            if q:
                st.code(q, language="sql")
    if details.empty:
        st.success("Every compared row matches.")
        return
    status = st.segmented_control("Show", ["All", "Mismatch", "Missing in target", "Missing in source", "Duplicate in target"],
                                  default="All", key="cmp_show")
    view = details if status in (None, "All") else details[details["status"] == status]
    shown = _mask(view, ui.is_admin())
    ev = st.dataframe(shown, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="cmp_details")
    sel = ev.selection.rows if ev and ev.selection else []
    a, b, c, _ = st.columns([1.6, 1.6, 1.5, 3])
    if sel:
        key = view.iloc[sel[0]]["key"]
        if a.button("Open source record", key="cmp_open_s"):
            ui.open_record(src, skey, key)
        if b.button("Open target record", key="cmp_open_t"):
            ui.open_record(tgt, tkey, key)
    if c.button("Add to evidence", key="cmp_ev"):
        ui.add_evidence(evidence.make_item(
            "compare", f"{src} ↔ {tgt}: {sm['mismatched']} mismatched, {sm['missing_in_target']} missing in target "
                       f"(of {sm['compared']} compared)", df=_mask(details, False), sql=res["sql"][0]))


def _mask(df, admin):
    out = df.copy()
    secret = out["field"].map(lambda f: any(privacy.is_secret_column(p.strip()) for p in str(f).split("↔")))
    phone = out["field"].map(lambda f: bool(privacy.PHONE_COLUMN.search(str(f))))
    for col in ("source_value", "target_value"):
        out.loc[secret, col] = privacy.HIDDEN
        if not admin:
            out.loc[phone & ~secret, col] = out.loc[phone & ~secret, col].map(privacy._mask_phone)
            out[col] = out[col].map(privacy._mask_emails)
    return out
