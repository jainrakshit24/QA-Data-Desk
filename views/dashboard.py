"""QA health dashboard: latest status of every check, what changed, trends, and a downloadable data quality report."""
import datetime as dt
import io

import pandas as pd
import streamlit as st

import db
import qa_checks
import store
import ui
from core import checks as cc


def render():
    st.title("QA dashboard")
    if not ui.require_connection():
        return
    conn = ui.active_connection()
    dbname = qa_checks.database_name()
    checks = qa_checks.load(dbname)
    by_id = {c["id"]: c for c in checks}
    latest = store.latest_runs(conn)
    st.caption(f"{conn} · {len(checks)} checks apply to “{dbname}”. Counts come from recorded runs — no result rows are stored.")

    a, b, _ = st.columns([1.6, 2, 4])
    if a.button(f"Run all {len(checks)} checks", type="primary", disabled=not checks):
        with st.spinner("Running every check…"):
            qa_checks.results_state().update(qa_checks.run(checks))
        latest = store.latest_runs(conn)
    stale = [c for c in checks if (latest.get(c["id"]) or {}).get("latest", {}).get("status") in ("failed", "error", "outdated")]
    if b.button(f"Re-run {len(stale)} with problems", disabled=not stale):
        with st.spinner("Running…"):
            qa_checks.results_state().update(qa_checks.run(stale))
        latest = store.latest_runs(conn)

    today = dt.datetime.utcnow().date().isoformat()
    rows = []
    for c in checks:
        runs = latest.get(c["id"]) or {}
        last, prev = runs.get("latest"), runs.get("previous")
        status = last["status"] if last else "not run"
        count = last["result_rows"] if last else None
        delta = cc.change(count, (prev or {}).get("result_rows")) if status not in ("error", "outdated") else None
        rows.append({"id": c["id"], "check": c["title"], "area": c["area"], "severity": c["severity"], "status": status,
                     "rows": count, "change": delta, "duration_s": (last or {}).get("seconds"),
                     "last_run": (last or {}).get("ran_at", ""), "ran_today": bool(last and last["ran_at"][:10] == today),
                     "owner": c["owner"], "tags": ", ".join(c["tags"])})
    table = pd.DataFrame(rows)
    if table.empty:
        st.info("No checks for this database yet.")
        return
    table["change"] = pd.to_numeric(table["change"], errors="coerce")
    table["rows"] = pd.to_numeric(table["rows"], errors="coerce")

    ran_today = table[table["ran_today"]]
    failed = ran_today[ran_today["status"] == "failed"]
    m = st.columns(5)
    m[0].metric("Checks run today", len(ran_today))
    m[1].metric("Passed", int((ran_today["status"] == "passed").sum()))
    m[2].metric("Failed", len(failed))
    m[3].metric("Warnings", int(ran_today["status"].isin(["error", "outdated"]).sum()),
                help="Checks that errored, or use tables/columns that no longer exist")
    m[4].metric("Total issues", f"{int(failed['rows'].fillna(0).sum()):,}", help="Rows returned by failed checks")

    worse = table[(table["change"].fillna(0) > 0) & (table["status"] == "failed")]
    if not worse.empty:
        st.warning("Got worse since the previous run: " + " · ".join(f"{r.check} (+{int(r.change)})" for r in worse.itertuples()))

    show = st.segmented_control("Show", ["Problems", "All", "Not run"], default="Problems", key="dash_show")
    view = table
    if show == "Problems":
        view = table[table["status"].isin(["failed", "error", "outdated"])]
    elif show == "Not run":
        view = table[table["status"] == "not run"]
    sev_order = {s: i for i, s in enumerate(cc.SEVERITIES)}
    stat_order = {s: i for i, s in enumerate(["failed", "error", "outdated", "passed", "info", "not run"])}
    view = view.assign(_s=view["status"].map(stat_order), _v=view["severity"].map(sev_order)).sort_values(["_s", "_v"])
    display = view.assign(status=view["status"].map(cc.STATUS_MARK), severity=view["severity"].map(cc.SEVERITY_MARK),
                          change=view["change"].map(lambda d: cc.change_text(None if pd.isna(d) else int(d))),
                          last_run=view["last_run"].str.replace("T", " "))
    st.dataframe(display[["status", "severity", "check", "rows", "change", "duration_s", "last_run", "area", "owner"]],
                 hide_index=True, width="stretch", column_config={"rows": st.column_config.NumberColumn(format="%d"),
                                                                  "duration_s": st.column_config.NumberColumn("seconds", format="%.1f")})
    if show == "Problems" and view.empty:
        st.success("No failed, errored or outdated checks in their latest run.")
    st.caption("Open a check’s results on the QA checks page.")

    history = pd.DataFrame(store.check_history(conn, limit=5000))
    if not history.empty:
        with st.expander("Trend — issues found per day"):
            history["day"] = history["ran_at"].str[:10]
            last_per_day = history.sort_values("id").groupby(["day", "check_id"]).tail(1)
            trend = last_per_day[last_per_day["status"] == "failed"].groupby("day")["result_rows"].sum().reset_index()
            if trend.empty:
                st.caption("No failed checks recorded yet.")
            else:
                st.plotly_chart(ui.draw(trend.rename(columns={"result_rows": "issues"}), "Line", "day", "issues"),
                                width="stretch", key="dash_trend")

    st.subheader("Data quality report")
    report_df = table.drop(columns=["id", "ran_today"])
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download TXT", lambda: _text_report(conn, dbname, table).encode("utf-8"),
                       file_name=f"data_quality_{dbname}_{today}.txt", mime="text/plain", on_click="ignore")
    c2.download_button("Download CSV", lambda: report_df.to_csv(index=False).encode("utf-8"),
                       file_name=f"data_quality_{dbname}_{today}.csv", mime="text/csv", on_click="ignore")
    c3.download_button("Download XLSX", lambda: _xlsx(report_df, conn, dbname),
                       file_name=f"data_quality_{dbname}_{today}.xlsx", on_click="ignore",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _text_report(conn, dbname, table):
    ran = table[table["status"] != "not run"]
    failed = ran[ran["status"] == "failed"].sort_values("rows", ascending=False)
    lines = [
        "DATA QUALITY REPORT", "=" * 60,
        f"Database:        {dbname} ({conn})",
        f"Generated at:    {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Checks defined:  {len(table)}",
        f"Checks executed: {len(ran)}",
        f"Passed:          {int((ran['status'] == 'passed').sum())}",
        f"Failed:          {len(failed)}",
        f"Warnings:        {int(ran['status'].isin(['error', 'outdated']).sum())} (errors or outdated checks)",
        f"Total issues:    {int(failed['rows'].fillna(0).sum())} rows returned by failed checks",
        "", "TOP ISSUES", "-" * 60,
    ]
    for r in failed.head(15).itertuples():
        delta = "" if pd.isna(r.change) else f" ({cc.change_text(int(r.change))})"
        lines.append(f"[{r.severity.upper()}] {r.check}: {int(r.rows or 0)} rows{delta} — last run {r.last_run.replace('T', ' ')}")
    if failed.empty:
        lines.append("None.")
    slow = ran.dropna(subset=["duration_s"]).sort_values("duration_s", ascending=False).head(5)
    lines += ["", "PERFORMANCE — slowest checks", "-" * 60]
    lines += [f"{r.duration_s:.1f}s  {r.check}" for r in slow.itertuples()] or ["No runs."]
    warn = ran[ran["status"].isin(["error", "outdated"])]
    lines += ["", "WARNINGS", "-" * 60]
    lines += [f"{r.status}: {r.check}" for r in warn.itertuples()] or ["None."]
    return "\n".join(lines) + "\n"


def _xlsx(df, conn, dbname):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name="Checks")
        pd.DataFrame([{"database": dbname, "connection": conn, "generated_at": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"}]) \
            .to_excel(xl, index=False, sheet_name="About")
    return buf.getvalue()
