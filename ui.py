# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Shared helpers for every page: current user, connection, running queries, showing results."""
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import plotly.express as px
import pymysql
import streamlit as st
from pymysql.converters import escape_item

import auth
import db
import privacy
import schema

PAGES = {}  # filled by app.py: page key -> st.Page
LEGAL_PAGES = ("privacy", "terms")
DISPLAY_ROWS = 1000        # rows drawn in the browser; downloads include everything fetched
SCHEMA_ERRORS = {1054, 1146}  # unknown column / table: the cached table list is out of date


# ------------------------------------------------------------------ session
def user():
    return st.session_state.get("user")


def is_admin():
    u = user()
    return bool(u and u.get("role") == "admin")


def active_connection():
    names = db.connection_names()
    if not names:
        return None
    if st.session_state.get("conn_name") not in names:
        st.session_state.conn_name = names[0]
    return st.session_state.conn_name


def require_connection():
    if active_connection():
        return True
    st.warning("No database is connected yet." +
               (" Add one on the Databases page." if is_admin() else " Ask an admin to add one."))
    if is_admin() and st.button("Connect a database", type="primary", key=f"req_conn_{st.session_state.get('_page_key', '')}"):
        go("databases")
    return False


def remember_current_page(pages, current):
    """Note which page is open, so pages like the legal documents can offer a way back to it."""
    key = next((k for k, page in pages.items() if page.url_path == current.url_path), None)
    if key and key not in LEGAL_PAGES:
        st.session_state["_last_page"] = key


def back_button(label="Back", fallback="investigate", key="back"):
    """A way back to whatever the reader was doing before. Falls back to the home page."""
    target = st.session_state.get("_last_page") or fallback
    if target not in PAGES:
        target = fallback if fallback in PAGES else next(iter(PAGES), None)
    if target and st.button(f"← {label}", key=key):
        go(target)


def go(page_key):
    page = PAGES.get(page_key)
    if page:
        st.switch_page(page)


def open_record(table, column, value, push=True):
    """Go to the Record page for table.column = value, keeping a trail for the breadcrumb."""
    target = {"table": table, "column": column, "value": str(value)}
    trail = st.session_state.setdefault("rec_trail", [])
    if push and (not trail or trail[-1] != target):
        trail.append(target)
        del trail[:-12]
    st.session_state.rec_target = target
    go("record")


def set_pending(key, value):
    """Change a widget's value on the next run (Streamlit forbids changing it after it renders)."""
    st.session_state[f"_pending_{key}"] = value


def apply_pending(key):
    pk = f"_pending_{key}"
    if pk in st.session_state:
        st.session_state[key] = st.session_state.pop(pk)


# ------------------------------------------------------------------ schema
@st.cache_resource(show_spinner="Reading the database structure…")
def _schema(name, version):
    return schema.load(name)


SCHEMA_CHECK_SECONDS = 600
_schema_checked = {}  # connection name -> last time the structure fingerprint was compared


def get_schema():
    """Cached table list, compared with the live database every 10 minutes and refreshed if it changed."""
    name = active_connection()
    s = _schema(name, st.session_state.get("schema_version", 0))
    now = time.time()
    if now - _schema_checked.get(name, 0) > SCHEMA_CHECK_SECONDS:
        _schema_checked[name] = now
        try:
            changed = schema.signature(name) != s.signature
        except Exception:
            changed = False  # the database is unreachable; the page will say so when it queries
        if changed:
            refresh_schema()
            st.toast("The database structure changed — the table list has been refreshed.")
            s = _schema(name, st.session_state.get("schema_version", 0))
    return s


def refresh_schema_if_changed():
    """Refresh only when the live structure really differs (a 0.1s check), not on every missing-column error."""
    name = active_connection()
    try:
        changed = schema.signature(name) != get_schema().signature
    except Exception:
        return False
    if changed:
        refresh_schema()
    _schema_checked[name] = time.time()
    return changed


def refresh_schema():
    schema.load(active_connection(), force=True)
    _schema.clear()
    _cached.clear()
    st.session_state.schema_version = st.session_state.get("schema_version", 0) + 1


def cache_age(s):
    mins = int((time.time() - s.fetched_at) // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    return f"{mins // 60} h ago"


# ------------------------------------------------------------------ queries
def display_sql(sql, params=None):
    if not params:
        return sql.strip()
    try:
        return (sql % tuple(escape_item(p, "utf8mb4") for p in params)).strip()
    except (TypeError, ValueError):
        return sql.strip()


def error_help(e):
    """dict(message, reasons, tries, technical) for any exception raised while querying."""
    from core import errors
    if isinstance(e, db.ReadOnlyError):
        info = errors.explain("read_only", str(e))
    elif isinstance(e, privacy.PrivacyError):
        info = errors.explain("privacy", str(e))
    elif isinstance(e, pymysql.MySQLError):
        code = e.args[0] if e.args else None
        msg = e.args[1] if len(e.args) > 1 else str(e)
        info = errors.explain(code, msg)
        info["technical"] = f"MySQL error {code}: {msg}"
        return info
    else:
        info = errors.explain(None, str(e))
    info["technical"] = f"{type(e).__name__}: {e}"
    return info


def friendly_error(e):
    return error_help(e)["message"]


def run_query(sql, params=None, source="sql", max_rows=db.MAX_ROWS, row_limit=None, connection=None):
    """Run a read query and return a result dict; errors come back as text, never raised.

    row_limit adds `LIMIT row_limit + 1` to a query that has no LIMIT, so a stray
    `SELECT * FROM huge_table` returns in a second instead of timing out.
    connection names the database to use; the one chosen in the sidebar by default.
    """
    conn = connection or active_connection()
    limited = False
    if row_limit:
        sql, limited = db.apply_row_limit(sql, row_limit + 1)
        if limited:
            max_rows = row_limit
    shown = display_sql(sql, params)
    try:
        privacy.check_sql(sql)
        df, secs, truncated = db.run(sql, params=params, max_rows=max_rows, name=conn)
        auth.log_query(user(), source, shown, len(df), round(secs, 2), connection=conn)
        return {"df": df, "seconds": secs, "truncated": truncated, "error": None, "sql": shown,
                "ran_at": dt.datetime.now().strftime("%H:%M:%S"), "auto_limit": row_limit if limited else None}
    except Exception as e:  # shown to the user, never raised
        help_ = error_help(e)
        msg = help_["message"]
        if _schema_is_stale(e) and refresh_schema_if_changed():
            msg += " The saved table list was out of date and has now been refreshed — try again."
        auth.log_query(user(), source, shown, None, None, msg, connection=conn)
        return {"df": None, "seconds": 0, "truncated": False, "error": msg, "sql": shown, "ran_at": None,
                "auto_limit": None, "stale_schema": _schema_is_stale(e), "help": help_}


def _schema_is_stale(e):
    return isinstance(e, pymysql.MySQLError) and bool(e.args) and e.args[0] in SCHEMA_ERRORS


def run_many(queries, source, workers=6, row_limit=None, connection=None):
    """Run several independent read queries at the same time. queries: list of (label, sql, params)."""
    conn, who = connection or active_connection(), user()

    def one(item):
        label, sql, params = item
        limited = False
        if row_limit:
            sql, limited = db.apply_row_limit(sql, row_limit + 1)
        try:
            privacy.check_sql(sql)
            df, secs, truncated = db.run(sql, params=params, name=conn,
                                         max_rows=row_limit if limited else db.MAX_ROWS)
            return label, {"df": df, "seconds": secs, "truncated": truncated, "error": None,
                           "sql": display_sql(sql, params), "ran_at": None,
                           "auto_limit": row_limit if limited else None}
        except Exception as e:
            help_ = error_help(e)
            return label, {"df": None, "seconds": 0, "truncated": False, "error": help_["message"],
                           "sql": display_sql(sql, params), "ran_at": None, "stale_schema": _schema_is_stale(e),
                           "help": help_}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, queries))
    if any(res.get("stale_schema") for _, res in results):
        refresh_schema_if_changed()
    for label, res in results:  # the activity log is written from this thread only
        auth.log_query(who, source, res["sql"], None if res["error"] else len(res["df"]),
                       None if res["error"] else round(res["seconds"], 2), res["error"], connection=conn)
    return results


@st.cache_data(ttl=600, show_spinner=False)
def _cached(conn, sql):
    df, secs, truncated = db.run(sql, name=conn)
    return df, secs, truncated


def cached_query(sql):
    """For charts: same result for 10 minutes, so switching charts is instant."""
    try:
        privacy.check_sql(sql)
        df, secs, truncated = _cached(active_connection(), sql)
        return {"df": df, "seconds": secs, "truncated": truncated, "error": None, "sql": sql}
    except Exception as e:
        return {"df": None, "seconds": 0, "truncated": False, "error": friendly_error(e), "sql": sql}


# ------------------------------------------------------------------ evidence basket
def evidence_items():
    return st.session_state.setdefault("evidence", [])


def add_evidence(item):
    evidence_items().append(item)
    st.toast(f"Added to evidence: {item['title']}")


# ------------------------------------------------------------------ output
def show_result(res, key, chart=True, height=None, evidence_title=None, evidence_kind="query"):
    if not res:
        return None
    if res["error"]:
        show_error(res)
        return None
    df = res["df"]
    admin = is_admin()
    shown = privacy.mask(df.head(DISPLAY_ROWS), admin)
    parts = [f"{len(df):,} row{'s' if len(df) != 1 else ''}", f"{res['seconds']:.1f}s"]
    if res.get("ran_at"):
        parts.append(f"ran at {res['ran_at']}")
    if res.get("auto_limit") and res["truncated"]:
        parts.append(f"stopped at {res['auto_limit']:,} rows — add your own LIMIT to get more")
    elif res["truncated"]:
        parts.append(f"stopped at {len(df):,} rows")
    if len(df) > DISPLAY_ROWS:
        parts.append(f"showing the first {DISPLAY_ROWS:,} here, all {len(df):,} in the download")
    masked = privacy.masked_columns(df, admin)
    if masked:
        parts.append("hidden or masked: " + ", ".join(masked[:6]))
    if not admin and privacy.contains_personal_data(shown):
        parts.append("emails and phone numbers are masked")
    st.caption(" · ".join(parts))
    if df.empty:
        st.info("No rows matched.")
        return shown
    kwargs = {"height": height} if height else {}
    st.dataframe(shown, width="stretch", hide_index=True, **kwargs)
    a, b, _ = st.columns([1.2, 1.4, 5])
    # The CSV is built only when someone clicks, and clicking does not rerun the page.
    a.download_button("Download CSV", lambda: privacy.mask(df, admin).to_csv(index=False).encode("utf-8"),
                      file_name=f"{key}.csv", mime="text/csv", key=f"dl_{key}", on_click="ignore")
    if b.button("Add to evidence", key=f"ev_{key}", help="Collect this result for a bug report (always masked)"):
        from core import evidence
        add_evidence(evidence.make_item(evidence_kind, evidence_title or f"Query result ({len(df)} rows)",
                                        df=df, sql=res.get("sql")))
    if chart and len(shown.columns):
        # Built only when switched on — drawing a chart on every click made pages slow.
        if st.toggle("Make a chart from this result", key=f"qc_on_{key}"):
            quick_chart(privacy.mask(df, admin), key)
    return shown


def show_error(res):
    """The plain message, then likely reasons, what to try and the technical detail on demand."""
    st.error(res["error"])
    info = res.get("help") or {}
    if info.get("reasons") or info.get("tries") or info.get("technical"):
        with st.expander("Why this happens and what to try"):
            if info.get("reasons"):
                st.markdown("**Possible reasons**\n" + "\n".join(f"- {r}" for r in info["reasons"]))
            if info.get("tries"):
                st.markdown("**Try**\n" + "\n".join(f"- {t}" for t in info["tries"]))
            if info.get("technical"):
                st.caption("Technical details")
                st.code(privacy.scrub_sql(info["technical"]), language=None)


def quick_chart(df, key):
    cols = list(df.columns)
    numeric = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    a, b, c, d = st.columns(4)
    kind = a.selectbox("Chart type", ["Bar", "Line", "Pie", "Scatter"], key=f"qc_kind_{key}")
    x = b.selectbox("Group by / X axis", cols, key=f"qc_x_{key}")
    y = c.selectbox("Value", ["Count of rows"] + numeric, key=f"qc_y_{key}")
    split = d.selectbox("Split by colour", ["None"] + [col for col in cols if col != x], key=f"qc_c_{key}")
    data = df.head(20000).copy()
    color = None if split == "None" else split
    if y == "Count of rows":
        group = [x] + ([color] if color else [])
        data = data.groupby(group, dropna=False).size().reset_index(name="rows")
        value = "rows"
    else:
        value = y
    if kind in ("Bar", "Pie"):
        data[x] = data[x].astype(str)
    if color:
        data[color] = data[color].astype(str)
    fig = draw(data, kind, x, value, color)
    st.plotly_chart(fig, width="stretch", key=f"qc_fig_{key}")


PALETTE = ["#33478F", "#E0913A", "#2E8B7A", "#C2524A", "#7B6BB5", "#8A9A3B", "#4F8FCB", "#B5648F"]


def draw(data, kind, x, y, color=None, title=None):
    common = dict(color_discrete_sequence=PALETTE, title=title)
    if kind == "Pie":
        fig = px.pie(data, names=x, values=y, hole=0.45, **common)
    elif kind == "Line":
        fig = px.line(data.sort_values(x), x=x, y=y, color=color, markers=True, **common)
    elif kind == "Scatter":
        fig = px.scatter(data, x=x, y=y, color=color, **common)
    else:
        fig = px.bar(data, x=x, y=y, color=color, **common)
    fig.update_layout(margin=dict(l=10, r=10, t=40 if title else 20, b=10), height=440,
                      legend_title_text="", font=dict(size=13))
    return fig


def restore(*keys):
    """Bring back widget values that Streamlit dropped while their tab was closed. Call before the widgets."""
    for k in keys:
        if k not in st.session_state and f"_keep_{k}" in st.session_state:
            st.session_state[k] = st.session_state[f"_keep_{k}"]


def remember(*keys):
    """Keep a copy of widget values so they survive their tab being closed. Call after the widgets."""
    for k in keys:
        if k in st.session_state:
            st.session_state[f"_keep_{k}"] = st.session_state[k]


def kept(key, default=None):
    """A widget's value even while its tab is closed."""
    return st.session_state.get(key, st.session_state.get(f"_keep_{key}", default))


def sticky_editor(base_key, initial, **kwargs):
    """st.data_editor whose edits survive the tab being closed and reopened."""
    editor_key, last_key = f"{base_key}_editor", f"{base_key}_last"
    if base_key not in st.session_state:
        st.session_state[base_key] = initial
    if editor_key not in st.session_state and last_key in st.session_state:
        st.session_state[base_key] = st.session_state[last_key]
    edited = st.data_editor(st.session_state[base_key], key=editor_key, **kwargs)
    st.session_state[last_key] = edited
    return edited


def lazy_tabs(labels, key):
    """Tabs whose content runs only while the tab is open (plain st.tabs run every tab on every click)."""
    return st.tabs(labels, key=key, on_change="rerun")


def indexed_label(col, key):
    return f"{col} 🔑" if key in ("PRI", "UNI", "MUL") else col


# ------------------------------------------------------------------ notices (cookies, insecure connection)
def _header(name, default=""):
    """One request header, or default. Streamlit exposes them read-only; missing behind some proxies."""
    try:
        return st.context.headers.get(name, default) or default
    except Exception:
        return default


def is_secure_connection():
    """True when the browser reached us over HTTPS, or over a loopback address where plain HTTP is fine."""
    proto = _header("X-Forwarded-Proto").split(",")[0].strip().lower()
    host = _header("Host", "").split(":")[0].lower()
    if proto:
        return proto == "https"
    return host in ("localhost", "127.0.0.1", "::1", "")


def insecure_warning():
    """Warn when the app is reachable over plain HTTP from another machine — passwords would cross the network."""
    if is_secure_connection() or st.session_state.get("_hid_insecure"):
        return
    box = st.container(border=True)
    box.warning("**This page was served over plain HTTP.** Passwords and query results are crossing the network "
                "unencrypted. Put the app behind HTTPS before using it from another machine — see "
                "`deploy/` in the project for a ready Caddy or nginx configuration.")
    if box.button("I understand, hide this", key="hide_insecure", type="tertiary"):
        st.session_state["_hid_insecure"] = True
        st.rerun()


def cookie_notice():
    """A one-time notice about the strictly necessary cookies. Remembered per user, or per session if signed out."""
    from core import legal
    who = (user() or {}).get("id")
    if who:
        import store
        if store.get_setting(f"cookies.ack.{who}"):
            return
    elif st.session_state.get("_cookie_ack"):
        return
    box = st.container(border=True)
    text, ack, more = box.columns([6, 1.1, 1.8], vertical_alignment="center")
    text.caption("🍪 " + legal.cookie_notice())
    a, b = ack, more
    if a.button("Got it", type="primary", key="cookie_ack", width="stretch"):
        st.session_state["_cookie_ack"] = True
        if who:
            import datetime as _dt
            import store
            store.set_setting(f"cookies.ack.{who}", _dt.datetime.utcnow().isoformat(timespec="seconds"))
        st.rerun()
    if b.button("Privacy policy", key="cookie_privacy", width="stretch"):
        go("privacy")
