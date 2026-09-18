"""Database health: structure hints worked out from the cached schema. No data is read."""
import altair as alt
import streamlit as st

import ui
from core import evidence
from core import health


@st.cache_data(max_entries=4, show_spinner="Checking the structure…")
def _findings(connection, fetched_at, _schema):
    return health.findings(_schema)


def render():
    st.title("Database health")
    st.caption("Structure hints from table and column definitions — primary keys, indexes, link types, empty and "
               "very large tables. No data is read, so this is instant. Row counts are MySQL estimates.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    df = _findings(ui.active_connection(), s.fetched_at, s)
    summ = health.summary(s, df)

    m = st.columns(4)
    m[0].metric("Tables", f"{summ['tables']:,}")
    m[1].metric("Columns", f"{summ['columns']:,}")
    m[2].metric("Rows (estimate)", f"{summ['rows_est']:,}")
    m[3].metric("Links found", f"{summ['links']:,}" if summ["links"] is not None else "—")
    if st.button("Refresh table list", key="hl_refresh"):
        ui.refresh_schema()
        st.rerun()

    counts = [(health.CHECKS[k][0], v, k) for k, v in summ["by_check"].items()]
    cols = st.columns(3)
    for i, (title, v, k) in enumerate(counts):
        cols[i % 3].metric(title, f"{v:,}", help=health.CHECKS[k][1])

    st.subheader("Findings")
    a, b = st.columns([2, 3])
    ui.restore("hl_checks", "hl_term")
    picked = a.multiselect("Show", list(health.CHECKS), key="hl_checks", format_func=lambda k: health.CHECKS[k][0])
    term = b.text_input("Table or column contains", key="hl_term")
    ui.remember("hl_checks", "hl_term")
    view = df
    if picked:
        view = view[view["check"].isin(picked)]
    if term:
        view = view[view["table"].str.contains(term, case=False, regex=False) |
                    view["column"].str.contains(term, case=False, regex=False)]
    if view.empty:
        st.success("Nothing to show for this selection.")
    else:
        event = st.dataframe(view.drop(columns=["check"]), hide_index=True, width="stretch",
                             height=min(520, 38 + 35 * len(view)), on_select="rerun", selection_mode="single-row",
                             key="hl_grid", column_config={"rows_est": st.column_config.NumberColumn("rows (est.)", format="%d")})
        st.caption("Select a row to open that table in Find data.")
        sel = event.selection.rows if event else []
        if sel:
            table = view.iloc[sel[0]]["table"]
            if st.button(f"Open {table}", key="hl_open", type="primary"):
                ui.set_pending("ex_mode", "Table names")
                ui.set_pending("ex_term", table)
                st.session_state.ex_table = table
                ui.go("find")
    x, y, _ = st.columns([1.3, 1.3, 4])
    x.download_button("Download CSV", view.to_csv(index=False).encode("utf-8"), file_name=f"db_health_{ui.active_connection()}.csv",
                      mime="text/csv", on_click="ignore")
    if y.button("Add to evidence", key="hl_ev"):
        ui.add_evidence(evidence.make_item("note", "Database health findings", df=view.drop(columns=["check"]).head(200)))
        st.toast("Added to evidence.")

    with st.expander("Largest tables"):
        big = health.biggest(s)
        st.altair_chart(alt.Chart(big).mark_bar().encode(
            x=alt.X("rows_est:Q", title="rows (estimate)"), y=alt.Y("name:N", sort="-x", title=None),
            tooltip=["name", alt.Tooltip("rows_est:Q", format=","), "columns"]), width="stretch")
