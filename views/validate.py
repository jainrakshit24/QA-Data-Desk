# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Validate an API response: against the database (optionally with expected values), or against rules."""
import os

import pandas as pd
import streamlit as st
import yaml

import auth
import packs
import privacy
import ui
from core import api_compare as ac
from core import evidence, records

RESULT_MARK = {"Match": "✅ Match", "Mismatch": "❌ Mismatch", "Missing in API": "⚠️ Missing in API",
               "Missing in DB": "⚠️ Missing in DB", "Missing in API and DB": "⚠️ Missing in both"}
RULE_MARK = {"pass": "✅ Pass", "fail": "❌ Fail", "warn": "⚠️ Could not check"}


def render():
    st.title("Validate API")
    st.caption("Paste an API response to compare it with the database, or to check it against rules. "
               "The response stays in this browser session; it is never sent to AI.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    tab_db, tab_rules = ui.lazy_tabs(["API ↔ Database", "Response rules"], key="val_tabs")
    if tab_db.open:
        with tab_db:
            _api_vs_db(s)
    if tab_rules.open:
        with tab_rules:
            _rules()


# ------------------------------------------------------------------ helpers
def _parse(text, where):
    if not (text or "").strip():
        return None
    try:
        return ac.parse_json(text)
    except ac.JSONProblem as e:
        where.error(str(e))
        return None


def _options(prefix):
    with st.expander("Comparison options"):
        c = st.columns(3)
        return ac.Options(
            case_insensitive=c[0].checkbox("Ignore letter case", True, key=f"{prefix}_case"),
            trim=c[0].checkbox("Ignore surrounding spaces", True, key=f"{prefix}_trim"),
            numbers=c[1].checkbox("\"5\" equals 5", True, key=f"{prefix}_num"),
            booleans=c[1].checkbox("true equals 1 / \"yes\"", True, key=f"{prefix}_bool"),
            dates=c[2].checkbox("Compare dates as times", True, key=f"{prefix}_date"),
            empty_is_null=c[2].checkbox("Empty text equals NULL", False, key=f"{prefix}_empty"),
        )


def _mask_table(df, admin):
    """Hide secret fields; mask emails and phone numbers in the value columns."""
    out = df.copy()
    secret = out["field"].map(lambda f: privacy.is_secret_column(str(f).split(".")[-1])) | \
        out["db_column"].map(privacy.is_secret_column)
    for col in ("expected", "api", "db"):
        if col in out:
            out.loc[secret, col] = privacy.HIDDEN
            if not admin:
                phone = out["field"].map(lambda f: bool(privacy.PHONE_COLUMN.search(str(f).split(".")[-1])))
                out.loc[phone & ~secret, col] = out.loc[phone & ~secret, col].map(privacy._mask_phone)
                out[col] = out[col].map(privacy._mask_emails)
    return out


# ------------------------------------------------------------------ API ↔ DB (+ expected)
def _api_vs_db(s):
    ui.restore("val_json", "val_table", "val_how", "val_mv")
    left, right = st.columns([1.1, 1])
    with left:
        text = st.text_area("API response (JSON)", key="val_json", height=300,
                            placeholder='{\n  "data": {"user_id": 123, "email": "abc@example.com", "status": "active"}\n}')
        data = _parse(text, st)
        fields = ac.flatten(data) if data is not None else {}
        if data is not None:
            st.caption(f"Valid JSON · {len(fields)} fields found")
    with right:
        table = st.selectbox("Database table", sorted(s.names), index=None, key="val_table",
                             placeholder="Choose the table the API reads from",
                             format_func=lambda n: f"{n} · ~{s.rows_est.get(n, 0):,} rows")
        ui.remember("val_json", "val_table")
        if not table:
            st.info("Choose a table to compare with.")
            return
        cols = records.safe_columns(s, table)
        ui.restore(f"val_mc_{table}", f"val_mf_{table}")
        match_col = st.selectbox("Find the DB row where column", cols, key=f"val_mc_{table}",
                                 index=next((i for i, c in enumerate(cols) if s.key_of.get((table, c)) == "PRI"), 0),
                                 format_func=lambda c: ui.indexed_label(c, s.key_of.get((table, c))))
        how = st.radio("equals", ["a field from the API response", "a value I type"], horizontal=True, key="val_how")
        if how.startswith("a field"):
            scalar = [f for f, v in fields.items() if not isinstance(v, (list, dict))]
            api_key = st.selectbox("API field", scalar, index=None, key=f"val_mf_{table}",
                                   placeholder="Paste JSON first" if not scalar else "Choose a field")
            match_value = ac.get_path(data, api_key) if (api_key and data is not None) else None
            match_value = None if match_value is ac.MISSING else match_value
        else:
            match_value = st.text_input("Value", key="val_mv") or None
        ui.remember("val_how", "val_mv", f"val_mc_{table}", f"val_mf_{table}")
        if st.button("Load DB row", type="primary", disabled=match_value in (None, ""), key="val_load"):
            st.session_state.val_row = {"key": (table, match_col, str(match_value)),
                                        "res": ui.run_query(records.select_rows_sql(s, table, match_col, limit=2),
                                                            [str(match_value)], source="api validator")}

    loaded = st.session_state.get("val_row")
    if not loaded or loaded["key"][0] != table:
        return
    res = loaded["res"]
    if res["error"]:
        st.error(res["error"])
        return
    db_row = res["df"].iloc[0].to_dict() if not res["df"].empty else None
    if db_row is None:
        st.warning(f"No row in `{table}` where `{loaded['key'][1]}` = {evidence.scrub(loaded['key'][2])}. "
                   "Every mapped field will show as Missing in DB.")
    elif len(res["df"]) > 1:
        st.warning("More than one DB row matches — comparing with the first. Match on a unique column to be sure.")

    st.markdown("**Map API fields to DB columns** — add an Expected value to check all three layers")
    map_key = f"val_map_{table}"
    if st.button("Auto-map by name again", key="val_automap"):
        for k in (map_key, f"{map_key}_editor", f"{map_key}_last"):
            st.session_state.pop(k, None)
    initial = pd.DataFrame(ac.suggest_mapping(list(fields), cols) or [{"api_field": "", "db_column": "", "expected": ""}])
    mapping = ui.sticky_editor(
        map_key, initial, num_rows="dynamic", width="stretch",
        column_config={
            "api_field": st.column_config.SelectboxColumn("API field", options=list(fields), width="large"),
            "db_column": st.column_config.SelectboxColumn("DB column", options=cols, width="medium"),
            "expected": st.column_config.TextColumn("Expected (optional)", width="medium"),
        })
    opt = _options("val")
    if data is None:
        st.info("Paste a valid API response to compare.")
        return
    results = pd.DataFrame(ac.compare(mapping.fillna("").to_dict("records"), data, db_row, opt))
    if results.empty:
        st.info("Map at least one field.")
        return
    counts = results["result"].value_counts()
    m = st.columns(4)
    m[0].metric("Matched", int(counts.get("Match", 0)))
    m[1].metric("Mismatched", int(counts.get("Mismatch", 0)))
    m[2].metric("Missing in API", int(counts.get("Missing in API", 0) + counts.get("Missing in API and DB", 0)))
    m[3].metric("Missing in DB", int(counts.get("Missing in DB", 0) + counts.get("Missing in API and DB", 0)))
    show = st.segmented_control("Show", ["All", "Only mismatches", "Only matches"], default="All", key="val_filter")
    view = results
    if show == "Only mismatches":
        view = results[results["result"] != "Match"]
    elif show == "Only matches":
        view = results[results["result"] == "Match"]
    has_expected = (results["expected"] != "").any()
    shown = _mask_table(view, ui.is_admin()).assign(result=lambda d: d["result"].map(RESULT_MARK))
    order = ["result", "field", "db_column"] + (["expected"] if has_expected else []) + ["api", "db", "where"]
    st.dataframe(shown[order], hide_index=True, width="stretch",
                 column_config={"where": st.column_config.TextColumn("which layer differs")})
    with st.expander("SQL used for the DB side"):
        st.code(res["sql"], language="sql")
    if st.button("Add comparison to evidence", key="val_ev"):
        ui.add_evidence(evidence.make_item(
            "compare", f"API ↔ DB comparison on {table} ({int(counts.get('Mismatch', 0))} mismatches)",
            df=_mask_table(results, False)[order], sql=res["sql"]))


# ------------------------------------------------------------------ response rules
def _local_rule_sets():
    path = os.path.join(packs.LOCAL_DIR, "validation_rules.yaml")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = yaml.safe_load(f) or []
    return [{"id": f"local:{i}", "name": r.get("name", f"Rule set {i + 1}"), "owner": "local/validation_rules.yaml",
             "rules": r.get("rules", []), "user_id": None} for i, r in enumerate(data) if isinstance(r, dict)]


def _rules():
    u = ui.user()
    ui.restore("val_rules_json", "val_status", "val_set")
    left, right = st.columns([1.1, 1])
    with left:
        if st.button("Use the JSON from the API ↔ Database tab", key="val_rules_copy", type="tertiary"):
            st.session_state.val_rules_json = ui.kept("val_json", "")
        text = st.text_area("API response (JSON)", key="val_rules_json", height=260,
                            placeholder='{"status": "success", "data": {"user_id": 123, "items": [1, 2]}}')
        status_code = st.number_input("HTTP status code (optional)", min_value=0, max_value=599, value=0, step=1,
                                      key="val_status", help="Use the path response.status_code in a rule")
        data = _parse(text, st)
    with right:
        sets = auth.rule_sets(u["id"]) + _local_rule_sets()
        labels = {str(r["id"]): f"{r['name']} · {r['owner']}" for r in sets}
        pick = st.selectbox("Saved rule sets", ["(new)"] + list(labels), key="val_set",
                            format_func=lambda k: k if k == "(new)" else labels[k])
        st.caption("Paths use dots and [index]: data.items[0].id · Operators: " + ", ".join(ac.OPERATORS))
    ui.remember("val_rules_json", "val_status", "val_set")

    set_key = f"val_rules_{pick}"
    chosen = next((r for r in sets if str(r["id"]) == pick), None)
    rows = chosen["rules"] if chosen else [
        {"path": "response.status_code", "operator": "equals", "expected": "200"},
        {"path": "status", "operator": "equals", "expected": "success"},
        {"path": "data.user_id", "operator": "not null", "expected": ""},
    ]
    rules_df = ui.sticky_editor(set_key, pd.DataFrame(rows, columns=["path", "operator", "expected"]),
                                num_rows="dynamic", width="stretch",
                              column_config={
                                  "path": st.column_config.TextColumn("Path", width="large"),
                                  "operator": st.column_config.SelectboxColumn("Check", options=list(ac.OPERATORS)),
                                  "expected": st.column_config.TextColumn("Expected"),
                              })
    opt = _options("rules")
    rules = [ac.Rule(str(r["path"] or "").strip(), str(r["operator"] or ""), "" if pd.isna(r["expected"]) else str(r["expected"]))
             for r in rules_df.to_dict("records") if str(r.get("path") or "").strip()]

    with st.popover("Save rule set"):
        name = st.text_input("Name", key="val_save_name")
        shared = st.checkbox("Share with everyone", key="val_save_shared")
        if st.button("Save", type="primary", disabled=not (name.strip() and rules), key="val_save"):
            auth.save_rule_set(u["id"], name, [r.__dict__ for r in rules], shared)
            st.success("Saved.")
        owned = next((r for r in sets if str(r["id"]) == pick and r.get("user_id")), None)
        if owned and (owned["user_id"] == u["id"] or ui.is_admin()) and st.button("Delete selected set", key="val_del"):
            auth.delete_rule_set(owned["id"], u["id"], ui.is_admin())
            for k in (set_key, f"{set_key}_editor", f"{set_key}_last"):
                st.session_state.pop(k, None)
            st.rerun()

    if data is None:
        st.info("Paste a valid API response to run the rules.")
        return
    payload = {"response": {"status_code": int(status_code) if status_code else ac.MISSING}}
    payload.update(data if isinstance(data, dict) else {"body": data})
    if not status_code:
        payload["response"].pop("status_code")
    rows = []
    for r in rules:
        status, detail = ac.check_rule(payload, r, opt)
        secret = privacy.is_secret_column(r.path.split(".")[-1])
        rows.append({"result": RULE_MARK[status], "rule": f"{r.path} {r.operator} {r.expected}".strip(),
                     "actual": privacy.HIDDEN if secret else evidence.scrub(detail)})
    out = pd.DataFrame(rows)
    if out.empty:
        st.info("Add at least one rule.")
        return
    c = out["result"].value_counts()
    m = st.columns(3)
    m[0].metric("Passed", int(c.get(RULE_MARK["pass"], 0)))
    m[1].metric("Failed", int(c.get(RULE_MARK["fail"], 0)))
    m[2].metric("Could not check", int(c.get(RULE_MARK["warn"], 0)))
    st.dataframe(out, hide_index=True, width="stretch")
    if st.button("Add rule results to evidence", key="val_rules_ev"):
        ui.add_evidence(evidence.make_item("rules", f"API response rules ({int(c.get(RULE_MARK['fail'], 0))} failed)", df=out))
