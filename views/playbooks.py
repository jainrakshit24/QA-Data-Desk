"""Playbooks: saved, parameterised investigations — run step by step, edit, duplicate, share."""
import os

import pandas as pd
import streamlit as st
import yaml

import ai
import packs
import privacy
import store
import ui
from core import evidence
from core import playbooks as pb
from core import sql_explain


def _templates():
    items = []
    for folder, origin in ((packs.CONFIG_DIR, "Template"), (packs.LOCAL_DIR, "Local")):
        path = os.path.join(folder, "playbooks.yaml")
        if os.path.exists(path):
            with open(path) as f:
                for i, spec in enumerate(yaml.safe_load(f) or []):
                    if isinstance(spec, dict):
                        items.append({"key": f"{origin.lower()}:{spec.get('id', i)}", "origin": origin, "owner": origin,
                                      "spec": pb.normalise(spec), "editable": False, "record": None})
    return items


def _all(u):
    saved = [{"key": f"saved:{r['id']}", "origin": "Shared" if r["shared"] and r["user_id"] != u["id"] else "Mine",
              "owner": r["owner"], "spec": pb.normalise(r["spec"]), "record": r,
              "editable": r["user_id"] == u["id"] or ui.is_admin()} for r in store.playbooks(u["id"])]
    return saved + _templates()


def render():
    st.title("Playbooks")
    st.caption("Reusable investigations: fill in the parameters, then run each reviewed step. "
               "Values are bound safely; table and column names are checked against the database first.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    u = ui.user()
    items = _all(u)
    ui.apply_pending("pb_pick")
    keys = ["(new playbook)"] + [i["key"] for i in items]
    labels = {i["key"]: f"{i['spec']['name']} · {i['origin']}" + (f" ({i['owner']})" if i["origin"] == "Shared" else "")
              for i in items}
    ui.restore("pb_pick")
    if st.session_state.get("pb_pick") not in keys:
        st.session_state.pb_pick = keys[1] if len(keys) > 1 else keys[0]
    pick = st.selectbox("Playbook", keys, key="pb_pick", format_func=lambda k: labels.get(k, k))
    ui.remember("pb_pick")
    item = next((i for i in items if i["key"] == pick), None)

    run_tab, edit_tab = ui.lazy_tabs(["Run", "Edit"], key=f"pb_tabs_{pick}")
    if run_tab.open:
        with run_tab:
            if item:
                _run(s, item)
            else:
                st.info("Create a playbook on the Edit tab, or pick a template above.")
    if edit_tab.open:
        with edit_tab:
            _edit(u, item)


# ------------------------------------------------------------------ run
def _param_inputs(s, spec, prefix):
    values = {}
    params = spec["parameters"]
    if not params:
        return values
    st.markdown("**Parameters**")
    cols = st.columns(min(3, len(params)))
    for i, p in enumerate(params):
        key = f"{prefix}_p_{p['name']}"
        ui.apply_pending(key)
        ui.restore(key)
        box = cols[i % len(cols)]
        if p["type"] == "table":
            values[p["name"]] = box.selectbox(p["label"], sorted(s.names), index=None, key=key, placeholder="Choose a table",
                                              format_func=lambda n: f"{n} · ~{s.rows_est.get(n, 0):,} rows")
        elif p["type"] == "column":
            table = values.get(p["table_param"]) if p["table_param"] else None
            options = ([c for c in s.cols_by_table.get(table, []) if not privacy.is_secret_column(c)] if table
                       else sorted({c for c in s.columns["col"] if not privacy.is_secret_column(c)}))
            if st.session_state.get(key) not in options:
                st.session_state.pop(key, None)
            values[p["name"]] = box.selectbox(p["label"], options, index=None, key=key, placeholder="Choose a column",
                                              disabled=bool(p["table_param"]) and not table)
        else:
            values[p["name"]] = box.text_input(p["label"], key=key, placeholder=p.get("hint") or "")
        ui.remember(key)
    return {k: (v or "") for k, v in values.items()}


def _run(s, item):
    spec = item["spec"]
    prefix = f"pbr_{item['key']}"
    if spec["objective"]:
        st.markdown(f"**Objective:** {spec['objective']}")
    issues = pb.problems(spec)
    if issues:
        st.warning("This playbook has problems: " + " ".join(issues))
    values = _param_inputs(s, spec, prefix)
    results = st.session_state.setdefault(f"{prefix}_results", {})

    rendered = [pb.render_sql(step["sql"], values, s, spec) for step in spec["steps"]]
    ready = [i for i, (sql, _, missing, err) in enumerate(rendered) if sql and not missing and not err]
    a, b, _ = st.columns([1.6, 2, 4])
    if a.button(f"Run all {len(spec['steps'])} steps", type="primary", disabled=len(ready) != len(spec["steps"]),
                key=f"{prefix}_all", help=None if len(ready) == len(spec["steps"]) else "Fill in every parameter first"):
        with st.spinner("Running every step…"):
            done = ui.run_many([(i, rendered[i][0], rendered[i][1] or None) for i in ready], source=f"playbook:{spec['name']}",
                               row_limit=1000)
        results.update(dict(done))
    if b.button("Add all results to evidence", disabled=not results, key=f"{prefix}_ev"):
        for i, res in sorted(results.items()):
            if not res["error"] and i < len(spec["steps"]):
                ui.add_evidence(evidence.make_item("query", f"{spec['name']} — step {i + 1}: {spec['steps'][i]['title']}",
                                                   df=res["df"], sql=res["sql"]))

    for i, step in enumerate(spec["steps"]):
        sql, params, missing, err = rendered[i]
        with st.container(border=True):
            st.markdown(f"**Step {i + 1} — {step['title']}**")
            if step["purpose"]:
                st.caption(step["purpose"])
            st.code(step["sql"].strip(), language="sql")
            if short := sql_explain.summary(step["sql"]):
                st.caption(f"In short: {short}")
            if step["look_for"]:
                st.caption(f"Look for: {step['look_for']}")
            if missing:
                st.caption("Needs: " + ", ".join(missing))
            if err:
                st.error(err)
            x, y, z, _ = st.columns([1, 1.6, 1.5, 3.5])
            if ai.configured() and z.button("Explain simply", key=f"{prefix}_expl_{i}"):
                with st.spinner("Asking AI…"):
                    try:
                        st.session_state[f"{prefix}_expl_txt_{i}"] = ai.explain_sql(
                            step["sql"], s, ai.tables_in_sql(step["sql"], s))
                    except ai.AIError as e:
                        st.error(str(e))
            if st.session_state.get(f"{prefix}_expl_txt_{i}"):
                st.info(st.session_state[f"{prefix}_expl_txt_{i}"])
            if x.button("Run", key=f"{prefix}_run_{i}", disabled=not sql):
                with st.spinner("Running…"):
                    results[i] = ui.run_query(sql, params or None, source=f"playbook:{spec['name']}", row_limit=1000)
            if y.button("Open in SQL editor", key=f"{prefix}_sql_{i}", disabled=not sql):
                ui.set_pending("sql_text", ui.display_sql(sql, params))
                ui.go("sql")
            if i in results:
                ui.show_result(results[i], key=f"{prefix}_res_{i}",
                               evidence_title=f"{spec['name']} — step {i + 1}: {step['title']}")


# ------------------------------------------------------------------ edit
def _edit(u, item):
    base = item["spec"] if item else pb.normalise({"name": "", "objective": "", "parameters": [], "steps": []})
    editable = bool(item and item["editable"])
    prefix = f"pbe_{item['key'] if item else 'new'}"
    if item and not editable:
        st.info(f"{item['origin']} playbooks are read-only. Duplicate it to make your own copy.")

    name_key, obj_key = f"{prefix}_name", f"{prefix}_objective"
    ui.restore(name_key, obj_key)
    st.session_state.setdefault(name_key, base["name"])
    st.session_state.setdefault(obj_key, base["objective"])
    name = st.text_input("Name", key=name_key, disabled=item is not None and not editable)
    objective = st.text_area("Objective", key=obj_key, height=70, disabled=item is not None and not editable)
    ui.remember(name_key, obj_key)

    st.markdown("**Parameters** — `:name` in SQL is a value; `{{name}}` is a table or column name")
    params_df = ui.sticky_editor(f"{prefix}_params", pd.DataFrame(base["parameters"] or [],
                                                                  columns=["name", "label", "type", "table_param", "hint"]),
                                 num_rows="dynamic", width="stretch", disabled=item is not None and not editable,
                                 column_config={"type": st.column_config.SelectboxColumn("type", options=pb.PARAM_TYPES),
                                                "table_param": st.column_config.TextColumn(
                                                    "table_param", help="For a column parameter: the table parameter it belongs to")})
    st.markdown("**Steps**")
    steps_df = ui.sticky_editor(f"{prefix}_steps", pd.DataFrame(base["steps"] or [{"title": "", "purpose": "", "sql": "", "look_for": ""}],
                                                                columns=["title", "purpose", "sql", "look_for"]),
                                num_rows="dynamic", width="stretch", disabled=item is not None and not editable,
                                column_config={"sql": st.column_config.TextColumn("sql", width="large"),
                                               "title": st.column_config.TextColumn("title", width="medium")})
    clean = lambda df: [{k: ("" if pd.isna(v) else v) for k, v in r.items()} for r in df.to_dict("records")]
    spec = pb.normalise({"name": name, "objective": objective, "parameters": clean(params_df), "steps": clean(steps_df)})
    issues = pb.problems(spec)
    for msg in issues:
        st.caption(f"⚠️ {msg}")

    shared_key = f"{prefix}_shared"
    ui.restore(shared_key)
    shared = st.checkbox("Share with everyone", value=bool(item and item["record"] and item["record"]["shared"]),
                         key=shared_key, disabled=item is not None and not editable)
    ui.remember(shared_key)
    a, b, c, _ = st.columns([1, 1.2, 1, 4])
    if (item is None or editable) and a.button("Save", type="primary", disabled=bool(issues), key=f"{prefix}_save"):
        pid = store.save_playbook(u["id"], spec["name"], spec, shared,
                                  item["record"]["id"] if item and item["record"] else None)
        _select(f"saved:{pid}")
    if item and b.button("Duplicate", key=f"{prefix}_dup"):
        copy = dict(item["spec"], name=f"{item['spec']['name']} (copy)")
        pid = store.save_playbook(u["id"], copy["name"], copy, False)
        _select(f"saved:{pid}")
    if item and editable and item["record"] and c.button("Delete", key=f"{prefix}_del"):
        store.delete_playbook(item["record"]["id"], u["id"], ui.is_admin())
        _select("(new playbook)")


def _select(key):
    st.session_state["_pending_pb_pick"] = key
    st.session_state.pop("_keep_pb_pick", None)
    st.rerun()
