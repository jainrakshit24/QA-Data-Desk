# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Start investigation: find every record that uses an identifier, or plan an investigation from a bug description."""
import time

import pandas as pd
import streamlit as st

import ai
import db
import ui
from core import evidence, params, search

KIND_LABEL = {"number": "a number (ID)", "email": "an email address", "phone": "a mobile number", "text": "a text code / reference"}


def render():
    st.title("Start investigation")
    st.caption("Enter an identifier to find every record that uses it — or describe the bug and get a "
               "step-by-step plan. Read-only; nothing here changes the database.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    mode = st.segmented_control("I have", ["An identifier", "A bug description"], default="An identifier",
                                key="inv_mode", label_visibility="collapsed")
    if mode == "A bug description":
        _plan(s)
    else:
        _identifier(s)
    _shortcuts()


SHORTCUTS = [
    ("find", "Browse a table", "Filter, count and group without SQL"),
    ("tests", "Test ideas", "Edge cases and test data for a table"),
    ("checks", "QA checks", "Run the saved checks for this database"),
    ("validate", "Validate API", "Compare a response with the database"),
    ("sql", "SQL editor", "Write it yourself, with parameters"),
    ("evidence", "Bug evidence", "Turn findings into a report"),
]


def _shortcuts():
    """The first screen should answer “where do I go?” for someone who did not come with an identifier."""
    st.divider()
    st.caption("Or go straight to")
    cols = st.columns(3)
    for i, (key, label, why) in enumerate(SHORTCUTS):
        if key not in ui.PAGES:
            continue
        with cols[i % 3]:
            if st.button(f"**{label}**  \n{why}", key=f"inv_short_{key}", width="stretch"):
                ui.go(key)


# ------------------------------------------------------------------ identifier search
def _identifier(s):
    a, b, c = st.columns([3, 2, 2])
    value = a.text_input("Identifier", key="inv_value",
                         placeholder="User ID, order ID, lead ID, email, mobile number, reference code…")
    col_hint = b.text_input("Column name contains (optional)", key="inv_col", placeholder="e.g. user_id, lead, order")
    table_hint = c.text_input("Table name contains (optional)", key="inv_tbl", placeholder="e.g. payment, adbuddy")
    with st.expander("Search options"):
        include_ids = st.checkbox("Also match every table’s own `id` column", key="inv_ids",
                                  help="Off by default: almost every table has a row with id = N, which says little "
                                       "about the record you want. Turned on automatically when you filter by table.")
        include_small = st.checkbox("Also scan columns without an index in small tables (slower)", key="inv_small")

    value = value.strip()
    if not value:
        st.info("Type an identifier to search. The search covers indexed identifier-like columns across all "
                f"{len(s.names):,} tables, and email / mobile columns when the value looks like one.")
        return
    kind = search.classify(value)
    cands = search.candidates(s, value, table_filter=table_hint, column_filter=col_hint,
                              include_unindexed_small=include_small, include_primary_ids=include_ids)
    st.caption(f"This looks like {KIND_LABEL[kind]} · {len(cands):,} columns will be searched")
    if not cands:
        st.warning("No suitable columns to search. Clear the column or table filter, or turn on the options above.")
        return

    if st.button("Search everywhere", type="primary", key="inv_go"):
        started = time.time()
        with st.spinner(f"Searching {len(cands):,} columns…"):
            results = ui.run_many(search.count_statements(cands, value), source="global search")
            frames, failed = [], []
            batch = search.BATCH
            for i, (_, res) in enumerate(results):
                if res["error"]:
                    failed.extend(cands[i * batch:(i + 1) * batch])
                else:
                    frames.append(res["df"])
            errors = []
            if failed:  # retry a failed batch one column at a time so one bad table cannot hide the rest
                retry = ui.run_many([search.single_statement(t, col, value) for t, col, _ in failed[:300]],
                                    source="global search")
                for (t, col, _), (_, res) in zip(failed, retry):
                    if res["error"]:
                        errors.append(f"{t}.{col}: {res['error']}")
                    else:
                        frames.append(res["df"])
        hits = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["tbl", "col", "matches"])
        hits["matches"] = pd.to_numeric(hits["matches"], errors="coerce").fillna(0).astype(int)
        hits = hits[hits["matches"] > 0]
        keys = {(t, col): k for t, col, k in cands}
        hits["index"] = [keys.get((t, col), "") for t, col in zip(hits["tbl"], hits["col"])]
        hits["rank"] = hits["index"].map({"PRI": 0, "UNI": 1, "MUL": 2}).fillna(3)
        hits = hits.sort_values(["rank", "matches", "tbl"]).drop(columns="rank").reset_index(drop=True)
        st.session_state.inv_result = {"value": value, "hits": hits, "searched": len(cands),
                                       "seconds": time.time() - started, "errors": errors}

    res = st.session_state.get("inv_result")
    if not res or res["value"] != value:
        return
    hits = res["hits"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Columns searched", f"{res['searched']:,}")
    m2.metric("Tables with matches", f"{hits['tbl'].nunique():,}")
    m3.metric("Columns with matches", f"{len(hits):,}")
    m4.metric("Time", f"{res['seconds']:.1f}s")
    if res["errors"]:
        with st.expander(f"{len(res['errors'])} columns could not be searched"):
            st.write("\n".join(f"- {e}" for e in res["errors"][:50]))
    if hits.empty:
        st.info(f"“{value}” was not found in the searched columns. Try a column filter such as “user_id”, "
                "turn on the search options, or check the value.")
        return

    names = sorted(hits["col"].unique())
    pick_col = st.selectbox("Show matches in column", ["All columns"] + names, key="inv_colpick",
                            format_func=lambda c: c if c == "All columns"
                            else f"{c}  ·  {int((hits['col'] == c).sum())} tables")
    view = hits if pick_col == "All columns" else hits[hits["col"] == pick_col]
    shown = view.assign(matches=view["matches"].map(lambda n: f"{n:,}+" if n >= search.CAP else f"{n:,}"))
    st.caption("Select a row, then open it. Unique and primary-key matches are listed first — they identify one record.")
    event = st.dataframe(shown.rename(columns={"tbl": "table", "col": "column", "index": "index"}),
                         width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row",
                         key="inv_hits", height=min(420, 38 + 35 * len(shown)))
    rows = event.selection.rows if event and event.selection else []
    x, y, _ = st.columns([1.6, 1.4, 4])
    if rows:
        r = view.iloc[rows[0]]
        if x.button(f"Investigate {r['tbl']}", type="primary", key="inv_open"):
            ui.open_record(r["tbl"], r["col"], value)
    else:
        x.button("Investigate selected record", disabled=True, key="inv_open_disabled")
    if y.button("Add search to evidence", key="inv_ev"):
        ui.add_evidence(evidence.make_item(
            "record", f"Where “{evidence.scrub(value)}” appears ({len(hits)} columns)",
            df=hits.rename(columns={"tbl": "table", "col": "column"})))


# ------------------------------------------------------------------ bug description → plan
def _plan(s):
    issue = st.text_area("Describe the bug", key="inv_issue", height=100,
                         placeholder="e.g. Payment is successful but the lead is still pending for this user")
    a, b = st.columns([2, 3])
    ident = a.text_input("Identifier (optional)", key="inv_plan_value", placeholder="e.g. 12345")
    ident_kind = b.text_input("What the identifier is (optional)", key="inv_plan_kind",
                              placeholder="e.g. user id, campaign id, order number")

    if not ai.configured():
        st.info("Planning needs the AI helper. An admin can add a Gemini key on Admin → AI. "
                "Meanwhile, these tables match the words in your description:")
        if issue.strip():
            st.dataframe(s.find_tables(issue, limit=12)[["name", "rows_est"]], hide_index=True, width="stretch")
        return
    st.caption("Gemini receives your description (with emails and phone numbers masked), what kind of identifier "
               "it is, and table/column names. It never receives the identifier value or any database rows, and it "
               "never runs anything — every step waits for you to press Run.")
    if st.button("Plan the investigation", type="primary", disabled=not issue.strip(), key="inv_plan_go"):
        with st.spinner("Finding the relevant tables and planning…"):
            try:
                picks = ai.suggest_tables(evidence.scrub(issue) + (f" ({ident_kind})" if ident_kind else ""), s, limit=10)
                tables = [p["table"] for p in picks] or s.find_tables(issue).head(8)["name"].tolist()
                plan = ai.investigation_plan(evidence.scrub(issue), evidence.scrub(ident_kind), tables, s)
                st.session_state.inv_plan = {"plan": plan, "tables": tables, "issue": issue}
                st.session_state.inv_step_results = {}
            except ai.AIError as e:
                st.error(str(e))

    saved = st.session_state.get("inv_plan")
    if not saved:
        return
    plan = saved["plan"]
    with st.container(border=True):
        st.markdown(f"**Understanding:** {plan['understanding'] or '—'}")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**What you told us** (stated facts)")
            st.markdown("\n".join(f"- {f}" for f in plan["stated_facts"]) or "—")
            if plan["data_paths"]:
                st.markdown("**Data paths to follow**")
                st.markdown("\n".join(f"- `{p}`" for p in plan["data_paths"]))
        with c2:
            st.markdown("**Possible explanations** — not confirmed until a step shows it")
            st.markdown("\n".join(f"- {e.get('explanation', '')}  \n  _Confirm with:_ {e.get('how_to_confirm', '')}"
                                  for e in plan["possible_explanations"]) or "—")

    if st.button("Save this plan as a playbook", key="inv_save_pb",
                 help="Reuse these steps later for another identifier — you can edit them on the Playbooks page"):
        import store
        from core import playbooks as pb
        spec = pb.from_ai_plan(plan, saved["issue"], ident_kind)
        pid = store.save_playbook(ui.user()["id"], spec["name"], spec, False)
        st.session_state["_pending_pb_pick"] = f"saved:{pid}"
        st.session_state.pop("_keep_pb_pick", None)
        if ident.strip():
            st.session_state[f"_pending_pbr_saved:{pid}_p_identifier"] = ident.strip()
        ui.go("playbooks")
    results = st.session_state.setdefault("inv_step_results", {})
    for i, step in enumerate(plan["steps"]):
        with st.container(border=True):
            st.markdown(f"**Step {i + 1} — {step['title']}**")
            st.caption(step["purpose"] + (f" · Tables: {', '.join(step['tables'])}" if step["tables"] else ""))
            st.code(step["sql"], language="sql")
            if step["look_for"]:
                st.caption(f"Look for: {step['look_for']}")
            names = params.names(step["sql"])
            problem = None
            try:
                db.check_read_only(step["sql"])
            except db.ReadOnlyError as e:
                problem = str(e)
            if problem:
                st.error(f"This step was not accepted: {problem}")
                continue
            missing = [n for n in names if n != "identifier"]
            values = {"identifier": ident.strip()} if "identifier" in names else {}
            for n in missing:
                values[n] = st.text_input(f":{n}", key=f"inv_p_{i}_{n}")
            x, y, _ = st.columns([1, 1.6, 5])
            blocked = ("identifier" in names and not ident.strip()) or any(not values.get(n) for n in missing)
            if x.button("Run", key=f"inv_run_{i}", disabled=blocked,
                        help="Enter the identifier above first" if blocked else None):
                try:
                    sql, bound = params.bind(step["sql"], values)
                    results[i] = ui.run_query(sql, bound, source="investigation", row_limit=1000)
                except KeyError as e:
                    st.error(f"Fill in :{e.args[0]} first.")
            if y.button("Open in SQL editor", key=f"inv_sql_{i}"):
                ui.set_pending("sql_text", step["sql"])
                if ident.strip() and "identifier" in names:
                    st.session_state["_pending_sq_param_identifier"] = ident.strip()
                ui.go("sql")
            if i in results:
                ui.show_result(results[i], key=f"inv_step_{i}", evidence_title=f"Step {i + 1}: {step['title']}")
