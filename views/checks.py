"""QA check library: filter by severity, tag and area; run one or all; every run's counts are recorded."""
import re

import pandas as pd
import streamlit as st
import yaml

import db
import packs
import privacy
import qa_checks
import store
import ui
from core import checks as cc


def render():
    st.title("QA checks")
    st.caption("Saved checks that look for known problems. Every run records its result count (never the rows), "
               "so the QA dashboard can show trends and what changed since the last run.")
    if not ui.require_connection():
        return
    dbname = qa_checks.database_name()
    conn = ui.active_connection()
    checks = qa_checks.load(dbname)
    results = qa_checks.results_state()
    history = store.latest_runs(conn)

    if not checks:
        st.info(f"No checks apply to “{dbname}” yet. Add one below or in local/checks.yaml.")
    areas = sorted({c["area"] for c in checks})
    tags = sorted({t for c in checks for t in c["tags"]})
    ui.apply_pending("chk_term")
    ui.restore("chk_area", "chk_sev", "chk_tags", "chk_term")
    a, b, c_, d = st.columns([2, 2, 2, 3])
    area = a.selectbox("Area", ["All areas"] + areas, key="chk_area")
    sev = b.multiselect("Severity", cc.SEVERITIES, key="chk_sev", format_func=lambda s: cc.SEVERITY_MARK[s])
    tag = c_.multiselect("Tags", tags, key="chk_tags")
    term = d.text_input("Search", key="chk_term", placeholder="title, SQL, owner…")
    ui.remember("chk_area", "chk_sev", "chk_tags", "chk_term")
    shown = cc.filter_checks(checks, dbname, sev, tag, area, term)

    x, y, _ = st.columns([1.6, 2.2, 4])
    if x.button(f"Run {len(shown)} checks", type="primary", disabled=not shown):
        with st.spinner(f"Running {len(shown)} checks at once…"):
            results.update(qa_checks.run(shown))
        history = store.latest_runs(conn)
    failed_ids = [c["id"] for c in shown if (history.get(c["id"]) or {}).get("latest", {}).get("status") in ("failed", "error")]
    if y.button(f"Re-run {len(failed_ids)} failed / errored", disabled=not failed_ids):
        with st.spinner("Running…"):
            results.update(qa_checks.run([c for c in shown if c["id"] in failed_ids]))
        history = store.latest_runs(conn)

    if shown:
        st.dataframe(_summary(shown, results, history), hide_index=True, width="stretch",
                     column_config={"rows": st.column_config.NumberColumn("rows", format="%d")})

    current_area = None
    for c in shown:
        if c["area"] != current_area:
            current_area = c["area"]
            st.subheader(current_area)
        res = results.get(c["id"])
        last = (history.get(c["id"]) or {}).get("latest")
        status = cc.evaluate(c, res) if res else (last["status"] if last else "not run")
        rows = cc.result_rows(res) if res and not res.get("error") else (last or {}).get("result_rows")
        label = f"{cc.STATUS_MARK[status]} · {cc.SEVERITY_MARK[c['severity']]} · {c['title']}"
        if rows is not None and status not in ("error", "outdated"):
            label += f" · {rows:,} rows"
        with st.expander(label):
            if status == "outdated":
                st.warning("This check uses a table or column that no longer exists in the database. "
                           "Update its SQL in local/checks.yaml, or delete it.")
            if res and res.get("alerts"):
                st.error("Alert: " + "; ".join(res["alerts"]))
            st.markdown(c.get("why", ""))
            meta = [cc.expectation_text(c)]
            if c["tags"]:
                meta.append("tags: " + ", ".join(c["tags"]))
            if c["owner"]:
                meta.append(f"owner: {c['owner']}")
            if c["alerts"]:
                meta.append("alerts: " + ", ".join(c["alerts"]))
            st.caption(" · ".join(meta))
            with st.popover("Show SQL"):
                st.code(c["sql"].strip(), language="sql")
            p, q, r, _ = st.columns([1, 1.6, 1.3, 4])
            if p.button("Run", key=f"chk_run_{c['id']}"):
                with st.spinner("Running…"):
                    results.update(qa_checks.run([c]))
                st.rerun()
            if q.button("Open in SQL editor", key=f"chk_sql_{c['id']}"):
                ui.set_pending("sql_text", c["sql"].strip())
                ui.go("sql")
            if r.toggle("History", key=f"chk_hist_{c['id']}"):
                runs = store.check_history(conn, c["id"], limit=20)
                if runs:
                    st.dataframe(pd.DataFrame(runs)[["ran_at", "status", "result_rows", "seconds", "error"]],
                                 hide_index=True, width="stretch")
                else:
                    st.caption("Not run yet on this database.")
            if res:
                ui.show_result(res, key=f"chk_{c['id']}", evidence_title=f"Check: {c['title']}", evidence_kind="check")

    if ui.is_admin():
        st.divider()
        _add_check_form(dbname, checks)


def _summary(shown, results, history):
    rows = []
    for c in shown:
        res = results.get(c["id"])
        runs = history.get(c["id"]) or {}
        last, prev = runs.get("latest"), runs.get("previous")
        status = cc.evaluate(c, res) if res else (last["status"] if last else "not run")
        count = (last or {}).get("result_rows")
        rows.append({
            "status": cc.STATUS_MARK[status], "severity": cc.SEVERITY_MARK[c["severity"]], "check": c["title"],
            "area": c["area"], "rows": count if status not in ("error", "outdated") else None,
            "change": cc.change_text(cc.change(count, (prev or {}).get("result_rows"))),
            "last run": (last or {}).get("ran_at", "").replace("T", " "),
            "tags": ", ".join(c["tags"]),
        })
    df = pd.DataFrame(rows)
    order = {cc.STATUS_MARK[s]: i for i, s in enumerate(["failed", "error", "outdated", "passed", "info", "not run"])}
    sev = {cc.SEVERITY_MARK[s]: i for i, s in enumerate(cc.SEVERITIES)}
    return df.assign(_o=df["status"].map(order), _s=df["severity"].map(sev)).sort_values(["_o", "_s"]).drop(columns=["_o", "_s"])


def _add_check_form(dbname, checks):
    with st.expander("Add a new check (admins)"):
        with st.form("chk_new", clear_on_submit=True):
            title = st.text_input("Title", placeholder="Successful payment without a lead")
            c1, c2, c3 = st.columns(3)
            area = c1.text_input("Area", placeholder="Payments")
            severity = c2.selectbox("Severity", cc.SEVERITIES, index=2, format_func=lambda s: cc.SEVERITY_MARK[s])
            owner = c3.text_input("Owner", placeholder="team or person")
            why = st.text_area("What it looks for", height=70)
            e1, e2 = st.columns(2)
            expect = e1.selectbox("Expected result", ["0 rows (any row is a problem)", "at most N rows", "at least N rows",
                                                      "information only"])
            n = e2.number_input("N", min_value=0, value=0, step=1)
            tags = st.text_input("Tags (comma separated)", placeholder="payments, leads, sync")
            alerts = st.multiselect("Alert when", ["rows > 0", "rows > 10", "rows increased", "query failed", "slower than 10s"])
            only_here = st.checkbox(f"Only show for the “{dbname}” database", value=True)
            sql = st.text_area("SQL (read-only)", height=160)
            if st.form_submit_button("Save check", type="primary"):
                try:
                    if not (title.strip() and sql.strip()):
                        raise ValueError("Give the check a title and a query.")
                    db.check_read_only(sql)
                    privacy.check_sql(sql)
                except (ValueError, privacy.PrivacyError) as e:
                    st.error(str(e))
                    return
                slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40]
                existing = {c["id"] for c in checks}
                cid, k = slug, 2
                while cid in existing:
                    cid, k = f"{slug}_{k}", k + 1
                exp = {"0 rows (any row is a problem)": "empty", "information only": "info",
                       "at most N rows": f"at most {int(n)}", "at least N rows": f"at least {int(n)}"}[expect]
                item = {"id": cid, "area": area.strip() or "Other", "title": title.strip(), "why": why.strip(),
                        "severity": severity, "expect": exp,
                        "tags": [t.strip().lower() for t in tags.split(",") if t.strip()],
                        "owner": owner.strip(), "alerts": alerts}
                if only_here:
                    item["database"] = dbname
                item["sql"] = sql.strip() + "\n"
                with open(packs.local_file("checks.yaml"), "a") as f:
                    f.write("\n" + yaml.safe_dump([item], sort_keys=False, allow_unicode=True, width=120))
                st.success(f"Saved “{title}” to local/checks.yaml.")
