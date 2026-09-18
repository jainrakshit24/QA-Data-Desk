"""Phase 3–5 features: query library and history, secret-column settings, friendly errors, search, edge cases,
test data, schema explorer, database health, record snapshots, environment compare, scheduled checks."""
import json
import os
import sqlite3
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import privacy  # noqa: E402
import store  # noqa: E402


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setattr(auth, "SETUP_CODE_FILE", str(tmp_path / ".setup_code"))
    monkeypatch.setenv("QA_SETUP_CODE", "CODE")
    auth.init()
    store.init()
    auth.sign_up("owner", "Owner", "o@example.com", "Password1234", code="CODE")
    auth.sign_up("guest", "Guest", "g@example.com", "Password1234")
    return {u["username"]: u for u in auth.list_users()}


# ------------------------------------------------------------------ query history & library
@pytest.mark.parametrize("sql, hidden, kept", [
    ("SELECT * FROM users WHERE mobile_number = '9876543210'", ["9876543210"], ["mobile_number"]),
    ("SELECT * FROM users WHERE email = 'first.last@gmail.com'", ["first.last@"], ["@gmail.com"]),
    ("select uid from users where u.phone like '98%' and password='hunter2'", ["98%", "hunter2"], ["u.phone"]),
    ("SELECT * FROM leads WHERE user_mobile IN ('9876543210', '9123456780')", ["9876543210", "9123456780"], ["IN ("]),
    ("SELECT * FROM t WHERE api_token = \"abc123\"", ["abc123"], ["api_token"]),
    ("SELECT * FROM campaigns WHERE cid = 12448 AND status = 'active'", [], ["12448", "'active'"]),
])
def test_logged_sql_masks_sensitive_values_only(sql, hidden, kept):
    out = privacy.scrub_sql(sql)
    assert all(h not in out for h in hidden)
    assert all(k in out for k in kept)


def test_query_log_stores_masked_text_and_connection(app_db):
    owner = app_db["owner"]
    auth.log_query(owner, "sql editor", "SELECT * FROM users WHERE mobile = '9876543210'", 1, 0.1, connection="staging")
    auth.log_query(owner, "sql editor", "SELECT x", None, None, "Column not found — x for a@b.io", connection="prod")
    rows = auth.recent_log(owner["id"])
    assert {r["connection"] for r in rows} == {"staging", "prod"}
    assert all("9876543210" not in r["sql"] for r in rows)
    assert "a@b.io" not in next(r["error"] for r in rows if r["error"])


def test_old_app_database_is_migrated_in_place(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, email TEXT, pw_hash TEXT,
            role TEXT, status TEXT, failed_attempts INTEGER DEFAULT 0, locked_until TEXT, created_at TEXT, last_login TEXT);
        CREATE TABLE saved_queries (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, notes TEXT, sql TEXT,
            shared INTEGER DEFAULT 0, created_at TEXT);
        CREATE TABLE query_log (id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, source TEXT, sql TEXT,
            rows INTEGER, seconds REAL, error TEXT, ran_at TEXT);
        INSERT INTO saved_queries (user_id, title, notes, sql, shared, created_at) VALUES (1, 'old', '', 'SELECT 1', 0, '2026-01-01');
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(auth, "APP_DB", str(path))
    auth.init()
    with auth.conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(saved_queries)")}
        logcols = {r[1] for r in c.execute("PRAGMA table_info(query_log)")}
        old = dict(c.execute("SELECT * FROM saved_queries").fetchone())
    assert {"tags", "category", "updated_at", "last_executed", "execution_count"} <= cols
    assert "connection" in logcols and old["title"] == "old" and old["execution_count"] == 0


def test_saved_queries_tags_edit_permissions_and_run_counts(app_db):
    owner, guest = app_db["owner"]["id"], app_db["guest"]["id"]
    qid = auth.save_query(owner, "Paid without lead", "SELECT 1", tags="Payments, leads, payments", category="Investigation")
    q = auth.saved_queries(owner)[0]
    assert q["tag_list"] == ["payments", "leads"] and q["category"] == "Investigation"
    auth.update_query(qid, guest, title="hijacked")                 # not the owner
    assert auth.saved_queries(owner)[0]["title"] == "Paid without lead"
    auth.update_query(qid, owner, title="Paid, no lead", shared=True, tags="debugging")
    assert [x["title"] for x in auth.saved_queries(guest)] == ["Paid, no lead"]
    auth.mark_query_run(qid)
    auth.mark_query_run(qid)
    q = auth.saved_queries(owner)[0]
    assert q["execution_count"] == 2 and q["last_executed"] and q["tag_list"] == ["debugging"]



# ------------------------------------------------------------------ QA-friendly errors
@pytest.mark.parametrize("code, message, expect_msg, expect_try", [
    (1054, "Unknown column 'status_code' in 'where clause'", "`status_code` was not found", "Refresh table list"),
    (1146, "Table 'shop.payments' doesn't exist", "`shop.payments` does not exist", "Schema compare"),
    (1064, "You have an error in your SQL syntax ... near 'FORM users' at line 1", "near “FORM users”", "backticks"),
    (3024, "Query execution was interrupted", "longer than 60 seconds", "Analyze"),
    (2003, "Can't connect to MySQL server", "Could not reach the database", "VPN"),
    (1052, "Column 'id' in field list is ambiguous", "`id` is ambiguous", "alias"),
    (1242, "Subquery returns more than 1 row", "more than one row", "IN (SELECT"),
    (9999, "something odd", "MySQL error 9999", "SQL used"),
])
def test_errors_explain_reasons_and_next_steps(code, message, expect_msg, expect_try):
    from core import errors
    info = errors.explain(code, message)
    assert expect_msg in info["message"]
    assert any(expect_try in t for t in info["tries"])


def test_ui_error_help_keeps_technical_detail_and_masks_it_on_screen():
    import pymysql
    import ui
    info = ui.error_help(pymysql.err.OperationalError(1054, "Unknown column 'x' in 'where clause'"))
    assert info["technical"] == "MySQL error 1054: Unknown column 'x' in 'where clause'"
    import db as dbmod
    ro = ui.error_help(dbmod.ReadOnlyError("Only read queries can run here"))
    assert ro["message"].startswith("Only read queries") and ro["reasons"]
    assert privacy.scrub_sql("MySQL error 1054: mobile = '9876543210'").count("9876543210") == 0


# ------------------------------------------------------------------ configurable secret / personal columns
def test_admin_configured_names_apply_everywhere_and_persist(app_db):
    try:
        store.set_setting("privacy.secret_columns", ["bank_account", "ifsc"])
        store.set_setting("privacy.personal_columns", ["guardian_name"])
        store.apply_privacy_settings()
        with pytest.raises(privacy.PrivacyError):
            privacy.check_sql("SELECT bank_account FROM students")
        df = pd.DataFrame([{"bank_account_no": "1234", "guardian_name": "9876543210", "name": "a@b.io"}])
        masked = privacy.mask(df, is_admin=False).iloc[0]
        assert masked["bank_account_no"] == privacy.HIDDEN
        assert "9876543210" not in masked["guardian_name"] and "a@" not in masked["name"]
        assert "1234567" not in privacy.scrub_sql("SELECT * FROM s WHERE ifsc = '1234567'")
        assert store.get_setting("privacy.secret_columns") == ["bank_account", "ifsc"]
    finally:
        privacy.configure()          # other tests expect the built-in lists only


def test_invalid_names_are_ignored_and_reported():
    valid, invalid = privacy.parse_names("bank_account, drop table x; --, cvv\nok_name")
    assert valid == ["bank_account", "cvv", "ok_name"] and invalid == ["drop table x; --"]
    try:
        privacy.configure(["bad name", "x"], [])
        assert privacy.EXTRA_SECRET == [] and not privacy.SECRET_COLUMN.search("bad name")
    finally:
        privacy.configure()


# ------------------------------------------------------------------ global search
def test_global_search_ranks_and_groups_everything():
    from core import global_search
    from test_investigation import fake_schema
    s = fake_schema()
    results = global_search.search(
        "payment", schema=s,
        saved_queries=[{"id": 1, "title": "Failed payment query", "sql": "SELECT 1", "owner": "a", "tags": "payments"}],
        checks=[{"id": "c1", "title": "Payment mismatch", "severity": "high", "area": "Payments", "tags": ["payments"], "sql": "S"}],
        playbooks=[{"key": "saved:1", "origin": "Mine", "spec": {"name": "Payment → Lead", "objective": "", "steps": []}}],
        rules=[{"id": 3, "name": "Paid has lead", "spec": {"source": {"table": "payments"}, "target": {"table": "leads"}}}],
        history=[{"sql": "SELECT * FROM payments", "ran_at": "2026-09-17T10:00", "source": "sql editor"},
                 {"sql": "SELECT * FROM payments", "ran_at": "2026-09-17T11:00", "source": "sql editor"}])
    kinds = [r["kind"] for r in results]
    assert kinds.index("Table") < kinds.index("Saved query") < kinds.index("QA check") < kinds.index("Playbook")
    assert {r["title"] for r in results if r["kind"] == "Table"} == {"payments"}
    assert sum(1 for r in results if r["kind"] == "History") == 1
    assert any(r["kind"] == "Consistency rule" for r in results)
    assert global_search.search("p", schema=s) == []


def test_global_search_multiple_words_must_all_match():
    from core import global_search
    results = global_search.search("paid lead", checks=[
        {"id": "a", "title": "Paid without lead", "sql": ""}, {"id": "b", "title": "Paid orders", "sql": ""}])
    assert [r["title"] for r in results] == ["Paid without lead"]


# ------------------------------------------------------------------ edge cases & test data
def test_type_parsing():
    from core import edge_cases as ec
    assert ec.parse_type("varchar(255)")["length"] == 255
    d = ec.parse_type("decimal(10,2) unsigned")
    assert (d["precision"], d["scale"], d["unsigned"]) == (10, 2, True)
    assert ec.parse_type("enum('a','it''s')")["values"] == ["a", "it's"]
    assert ec.kind_of(ec.parse_type("tinyint(1)")) == "boolean"
    assert ec.int_limits(ec.parse_type("tinyint unsigned")) == (0, 255)
    assert ec.int_limits(ec.parse_type("int")) == (-2 ** 31, 2 ** 31 - 1)


def test_edge_cases_follow_type_name_key_and_skip_secrets():
    from core import edge_cases as ec
    from test_investigation import fake_schema
    s = fake_schema()
    cases = ec.for_table(s, "users")
    by_col = {}
    for c in cases:
        by_col.setdefault(c["column"], []).append(c["case"])
    assert "password" not in by_col
    assert "Duplicate value" in by_col["email"] and "Missing @" in by_col["email"]
    assert "9 digits" in by_col["mobile"] and "Exactly 15 characters" in by_col["mobile"]
    assert "Leap day" in by_col["created_at"]
    ts = [c["case"] for c in ec.for_table(s, "payments") if c["column"] == "paid_at"]
    assert "After 2038" in ts
    assert any(c["category"] == "Links" for c in ec.for_table(s, "payments") if c["column"] == "user_id")


def test_cross_column_cases_for_date_pairs_and_soft_delete():
    from core import edge_cases as ec
    import schema as schema_mod
    cols = [("t", "id", "int", "PRI", "auto_increment"), ("t", "start_date", "date", "", ""),
            ("t", "end_date", "date", "", ""), ("t", "status", "enum('on','off')", "", ""),
            ("t", "deleted_at", "datetime", "", "")]
    raw = {"fetched_at": 0, "tables": [{"name": "t", "rows_est": 5}],
           "columns": [{"tbl": a, "col": b, "type": c, "nullable": "YES", "key": d, "default": None, "extra": e,
                        "comment": "", "pos": i} for i, (a, b, c, d, e) in enumerate(cols)], "fks": []}
    s = schema_mod.Schema(raw, "fake")
    cases = ec.for_table(s, "t")
    names = {c["case"] for c in cases}
    assert {"end_date before start_date", "Soft-deleted row", "Active status on a deleted row", "Value “on”"} <= names
    assert [c["case"] for c in cases if c["column"] == "id"] == ["Generated id"]


def test_coverage_query_is_read_only_counts_only_and_parses():
    import db
    from core import edge_cases as ec
    from test_investigation import fake_schema
    s = fake_schema()
    sql, used = ec.coverage_sql(s, "users")
    db.check_read_only(sql)
    privacy.check_sql(sql)
    assert "password" not in sql and "password" not in used
    assert "MIN(`mobile`)" not in sql and "MAX(`email`)" not in sql       # personal columns: counts only
    assert "ORDER BY `id` DESC LIMIT 5000" in sql
    total, rows = ec.coverage_table({"rows__sampled": 10, "email__nulls": 2, "email__longest": 30}, ["email"], s, "users")
    assert total == 10 and rows[0] == {"column": "email", "NULL": 2, "longest": 30, "limit": 255}


def test_test_data_insert_is_labelled_escaped_and_blocked_by_the_guard():
    import db
    from core import test_data as td
    from test_investigation import fake_schema
    s = fake_schema()
    rows, notes = td.sample_rows(s, "users", "valid", 2)
    assert [r["id"] for r in rows] == [1, 2]                           # PK without auto_increment gets distinct values
    assert rows[0]["email"].endswith("@example.com") and rows[0]["password"] == "CHANGE_ME"
    assert any("password" in n for n in notes)
    sql = td.insert_sql("users", rows + [{"email": "o'brien\\x@example.com"}])
    assert sql.startswith("-- NOT EXECUTED") and "'o''brien\\\\x@example.com'" in sql
    with pytest.raises(db.ReadOnlyError):
        db.check_read_only(sql)
    longest, _ = td.sample_rows(s, "users", "longest")
    assert len(longest[0]["mobile"]) == 15
    linked, notes = td.sample_rows(s, "payments")
    assert linked[0]["user_id"] == ":existing_users_id" and any("user_id" in n for n in notes)


def test_find_existing_queries_are_read_only_and_sample_big_tables():
    import db
    from core import test_data as td
    from test_investigation import fake_schema
    s = fake_schema()
    for table in ("users", "payments", "big_log"):
        for q in td.find_existing(s, table):
            db.check_read_only(q["sql"])
            privacy.check_sql(q["sql"])
    titles = [q["title"] for q in td.find_existing(s, "payments")]
    assert "user_id points to a missing users" in titles and any(t.startswith("One example for each status") for t in titles)
    big = td.find_existing(s, "big_log")
    assert all("LIMIT 50000" in q["sql"] or "LIMIT 5000" in q["sql"] or q["title"] == "Newest rows" for q in big)


def test_ai_test_data_ideas_drop_write_sql_and_unknown_columns(monkeypatch):
    import ai
    from test_investigation import fake_schema
    s = fake_schema()
    sent = {}

    def fake_call(prompt, system, **kw):
        sent["prompt"] = prompt
        return {"scenarios": [
            {"name": "paid", "why": "x", "row": {"status": "paid", "nope": 1}, "find_sql": "SELECT * FROM payments LIMIT 20"},
            {"name": "evil", "why": "y", "row": {}, "find_sql": "DELETE FROM payments"}]}
    monkeypatch.setattr(ai, "_call", fake_call)
    out = ai.test_data_ideas("refund after 9876543210 paid", "payments", s)
    assert out[0]["row"] == {"status": "paid"} and out[0]["find_sql"]
    assert out[1]["find_sql"] == ""
    assert "payments" in sent["prompt"]


# ------------------------------------------------------------------ pages without a database (fake schema)
@pytest.fixture()
def offline_ui():
    """AppTest runs the page script in THIS process, so its patches on `ui` must be undone afterwards."""
    import ui
    saved = {name: getattr(ui, name) for name in ("get_schema", "require_connection", "active_connection")}
    yield
    for name, fn in saved.items():
        setattr(ui, name, fn)


def _offline_page(tmp_path, view):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = tmp_path / f"offline_{view}.py"
    path.write_text(
        "import sys\n"
        f"sys.path.insert(0, {root!r}); sys.path.insert(0, {os.path.join(root, 'tests')!r})\n"
        "import auth, store, ui\n"
        f"auth.APP_DB = {str(tmp_path / 'app.sqlite3')!r}\n"
        "auth.init(); store.init()\n"
        "from test_investigation import fake_schema\n"
        "ui.get_schema = lambda *a, **k: fake_schema()\n"
        "ui.require_connection = lambda: True\n"
        "ui.active_connection = lambda: 'fake'\n"
        f"from views import {view}\n"
        f"{view}.render()\n")
    return str(path)


def test_search_and_test_ideas_pages_render_offline(app_db, tmp_path, offline_ui):
    from streamlit.testing.v1 import AppTest
    owner = app_db["owner"]
    user = {k: owner[k] for k in ("id", "username", "full_name", "email", "role", "status")}
    auth.save_query(owner["id"], "Payment check", "SELECT * FROM payments", tags="payments")
    at = AppTest.from_file(_offline_page(tmp_path, "search"), default_timeout=30)
    at.session_state["user"] = user
    at.run()
    at.text_input(key="gs_term").set_value("payment").run()
    assert not at.exception
    heads = [h.value for h in at.subheader]
    assert any("Table" in h for h in heads) and any("Saved query" in h for h in heads)

    at = AppTest.from_file(_offline_page(tmp_path, "test_ideas"), default_timeout=30)
    at.session_state["user"] = user
    at.run()
    at.selectbox(key="ti_table").set_value("users").run()
    assert not at.exception
    assert any("Duplicate value" in str(df.value.to_dict()) for df in at.dataframe)


# ------------------------------------------------------------------ schema graph
def _chain_schema():
    import schema as schema_mod
    cols = [("orders", "id", "PRI"), ("orders", "user_id", "MUL"), ("users", "id", "PRI"), ("users", "password", ""),
            ("users", "city_id", ""), ("cities", "id", "PRI"), ("cities", "name", ""), ("refunds", "id", "PRI"),
            ("refunds", "order_id", "MUL"), ("island", "id", "PRI")]
    raw = {"fetched_at": 0, "tables": [{"name": t, "rows_est": 10} for t in dict.fromkeys(c[0] for c in cols)],
           "columns": [{"tbl": t, "col": c, "type": "int", "nullable": "NO", "key": k, "default": None, "extra": "",
                        "comment": "", "pos": i} for i, (t, c, k) in enumerate(cols)],
           "fks": [{"tbl": "refunds", "col": "order_id", "ref_tbl": "orders", "ref_col": "id"}]}
    return schema_mod.Schema(raw, "fake")


def test_neighbourhood_depth_and_guess_toggle():
    from core import schema_graph as g
    s = _chain_schema()
    tables, edges, cut = g.neighbourhood(s, "orders", 1)
    assert set(tables) == {"orders", "users", "refunds"} and not cut
    tables, _, _ = g.neighbourhood(s, "orders", 2)
    assert "cities" in tables and "island" not in tables
    tables, edges, _ = g.neighbourhood(s, "orders", 2, include_guesses=False)
    assert set(tables) == {"orders", "refunds"} and edges[0][4] == "foreign key"
    assert '"orders" [label="orders' in g.dot(*g.neighbourhood(s, "orders", 1)[:2], "orders")
    _, _, cut = g.neighbourhood(s, "orders", 2, max_tables=2)
    assert cut


def test_join_path_and_sql_both_directions_without_secrets():
    import db
    from core import params
    from core import schema_graph as g
    s = _chain_schema()
    path = g.join_path(s, "refunds", "cities")
    assert [p[1] for p in path] == ["orders", "users", "cities"]
    sql = g.join_sql(s, "refunds", path)
    assert "JOIN `orders` AS t1 ON t1.`id` = t0.`order_id`" in sql
    assert "JOIN `cities` AS t3 ON t3.`id` = t2.`city_id`" in sql
    assert "password" not in sql and params.names(sql) == ["id"]
    db.check_read_only(sql)
    back = g.join_sql(s, "cities", g.join_path(s, "cities", "users"))
    assert "JOIN `users` AS t1 ON t1.`city_id` = t0.`id`" in back
    assert g.join_path(s, "orders", "island") is None and g.join_path(s, "orders", "orders") == []


# ------------------------------------------------------------------ database health
def test_health_findings_from_structure_only():
    import schema as schema_mod
    from core import health
    cols = [("big", "id", "bigint", "PRI", "NO"), ("big", "user_id", "int", "", "YES"), ("users", "id", "bigint", "PRI", "NO"),
            ("heap", "a", "int", "", "YES"), ("orders_bak_20240101", "id", "int", "PRI", "NO"),
            ("wide", "id", "int", "PRI", "NO")] + [("wide", f"c{i}", "text", "", "YES") for i in range(90)]
    est = {"big": 5_000_000, "users": 10, "heap": 0, "orders_bak_20240101": 3, "wide": 1}
    raw = {"fetched_at": 0, "tables": [{"name": t, "rows_est": n, "kind": "BASE TABLE"} for t, n in est.items()],
           "columns": [{"tbl": t, "col": c, "type": ty, "nullable": nl, "key": k, "default": None, "extra": "",
                        "comment": "", "pos": i} for i, (t, c, ty, k, nl) in enumerate(cols)], "fks": []}
    s = schema_mod.Schema(raw, "fake")
    df = health.findings(s)
    hit = {(r.check, r.table, r.column) for r in df.itertuples()}
    assert ("no_primary_key", "heap", "") in hit and ("no_index", "heap", "") in hit and ("empty", "heap", "") in hit
    assert ("type_mismatch", "big", "user_id") in hit and ("large_unindexed_links", "big", "user_id") in hit
    assert ("large", "big", "") in hit and ("leftover", "orders_bak_20240101", "") in hit
    assert ("wide", "wide", "") in hit and ("mostly_nullable", "wide", "") in hit
    assert not any(t == "users" and c != "large" for c, t, _ in hit if c not in ("empty",))
    summ = health.summary(s, df)
    assert summ["tables"] == 5 and summ["by_check"]["no_primary_key"] == 1


def test_health_and_graph_pages_render_offline(app_db, tmp_path, offline_ui):
    from streamlit.testing.v1 import AppTest
    owner = app_db["owner"]
    at = AppTest.from_file(_offline_page(tmp_path, "health"), default_timeout=30)
    at.session_state["user"] = {k: owner[k] for k in ("id", "username", "full_name", "email", "role", "status")}
    at.run()
    assert not at.exception
    assert any(m.label == "Tables" for m in at.metric)


# ------------------------------------------------------------------ record snapshots
def test_record_snapshot_stores_masked_values_and_detects_hidden_changes(app_db):
    import json as _json
    import datetime as _dt
    from core import snapshots
    owner = app_db["owner"]["id"]
    key = store.snapshot_key()
    assert key == store.snapshot_key() and len(key) == 32
    before_row = {"id": 7, "mobile": "9876543210", "email": "first.last@gmail.com", "api_token": "abc",
                  "status": 1, "updated_at": _dt.datetime(2026, 9, 17, 10, 0)}
    after_row = dict(before_row, mobile="9876500010", status=2, api_token="xyz")
    snap = snapshots.capture("users", "id", 7, before_row, key, {("leads", "user_id", "7"): 3})
    sid = store.save_record_snapshot(owner, "staging", "before", snap)
    stored = _json.dumps(store.record_snapshots("staging", "users", "id", snap["key_digest"])[0]["data"])
    for secret in ("9876543210", "first.last@", "abc"):
        assert secret not in stored
    now = snapshots.capture("users", "id", 7, after_row, key, {("leads", "user_id", "7"): 5})
    fields, links = snapshots.compare(snap, now)
    change = dict(zip(fields["field"], fields["change"]))
    assert change == {"id": "same", "mobile": "changed", "email": "same", "api_token": "hidden", "status": "changed",
                      "updated_at": "same"}
    assert links.iloc[0].to_dict() == {"linked": "leads.user_id", "before": 3, "after": 5, "difference": 2}
    other = snapshots.capture("users", "id", 8, before_row, key)
    assert store.record_snapshots("staging", "users", "id", other["key_digest"]) == []
    store.delete_record_snapshot(sid, app_db["guest"]["id"])          # not the owner: kept
    assert len(store.record_snapshots("staging", "users", "id", snap["key_digest"])) == 1
    store.delete_record_snapshot(sid, owner)
    assert store.record_snapshots("staging", "users", "id", snap["key_digest"]) == []


# ------------------------------------------------------------------ plain-English SQL summary (no AI)
@pytest.mark.parametrize("sql, expect", [
    ("SELECT * FROM `users` WHERE id = :id", ["every column from users", "values you fill in"]),
    ("SELECT c.id, COUNT(*) n FROM campaigns c LEFT JOIN leads l ON l.cid=c.id WHERE l.id IS NULL GROUP BY c.id "
     "ORDER BY n DESC LIMIT 20", ["joined with leads", "no match", "IS NULL", "Groups rows by c.id", "At most 20 rows"]),
    ("select count(*) from t where a=1 and b=2", ["single summary row (COUNT)", "2 conditions"]),
    ("SELECT * FROM (SELECT * FROM t ORDER BY id DESC LIMIT 5000) AS s LIMIT 20", ["from a sub-query", "At most 20 rows"]),
    ("UPDATE t SET a=1", []),
])
def test_sql_summary_without_ai(sql, expect):
    from core import sql_explain
    out = sql_explain.summary(sql)
    assert all(e in out for e in expect) and (out if expect else out == "")


# ------------------------------------------------------------------ environment compare
def _frame(rows):
    return pd.DataFrame(rows)


def test_compare_results_finds_missing_rows_and_differing_fields():
    from core import env_compare as ecmp
    a = _frame([{"id": 1, "status": "paid", "mobile": "9876543210"}, {"id": 2, "status": "new", "mobile": "9000000000"},
                {"id": 3, "status": "new", "mobile": None}])
    b = _frame([{"id": 1, "status": "paid", "mobile": "9876543210"}, {"id": 2, "status": "refunded", "mobile": "9000000000"},
                {"id": 4, "status": "new", "mobile": None}])
    out = ecmp.compare_results(a, b, "id")
    assert list(out["only_a"]["id"]) == [3] and list(out["only_b"]["id"]) == [4]
    diff = out["different"]
    assert len(diff) == 1 and diff.iloc[0]["field"] == "status"
    assert (diff.iloc[0]["id"], diff.iloc[0]["in A"], diff.iloc[0]["in B"]) == ("2", "new", "refunded")
    assert out["summary"]["shared_keys"] == 2
    same = ecmp.compare_results(a, a.copy(), "id")
    assert same["different"].empty and same["only_a"].empty and same["only_b"].empty


def test_compare_results_masks_values_and_reports_column_differences():
    from core import env_compare as ecmp
    a = _frame([{"id": 1, "mobile": "9876543210", "api_token": "abc"}])
    b = _frame([{"id": 1, "mobile": "9111111111", "note": "x"}])
    out = ecmp.compare_results(a, b, "id")
    assert out["summary"]["columns_only_a"] == ["api_token"] and out["summary"]["columns_only_b"] == ["note"]
    diff = out["different"]
    assert set(diff["field"]) == {"mobile"}
    assert "9876543210" not in str(diff.iloc[0]["in A"]) and "9111111111" not in str(diff.iloc[0]["in B"])
    assert "api_token" not in set(diff["field"])                      # secret columns are never compared on screen


def test_key_candidates_prefer_id_columns_and_skip_secrets():
    from core import env_compare as ecmp
    df = _frame([{"name": "a", "id": 1, "password": "p1", "dup": 1}, {"name": "b", "id": 2, "password": "p2", "dup": 1}])
    assert ecmp.key_candidates(df) == ["id", "name"]                  # id first, secret and non-unique columns dropped


def test_row_count_table_marks_missing_and_changed_tables():
    from core import env_compare as ecmp
    table = ecmp.count_table({"a": 10, "b": 5, "c": None, "d": 7}, {"a": 10, "b": 8, "c": 3, "d": None}, ["a", "b", "c", "d"])
    by = {r.table: (r.status, getattr(r, "difference")) for r in table.itertuples()}
    assert by["a"][0] == "same" and by["b"] == ("different", 3)
    assert by["c"][0] == "missing in A" and by["d"][0] == "missing in B"
    assert table.iloc[0]["table"] == "d"                                # missing rows are shown first
    assert table[table["table"] == "b"].iloc[0]["% change"] == 60.0


def test_record_diff_between_environments(app_db):
    from core import env_compare as ecmp
    key = store.snapshot_key()
    row_a = {"id": 5, "status": 1, "mobile": "9876543210", "api_token": "a"}
    row_b = {"id": 5, "status": 2, "mobile": "9876543210", "api_token": "b"}
    fields, missing = ecmp.record_diff("users", "id", 5, row_a, row_b, key)
    assert missing is None
    change = dict(zip(fields["field"], fields["change"]))
    assert change == {"id": "same", "status": "changed", "mobile": "same", "api_token": "hidden"}
    fields, missing = ecmp.record_diff("users", "id", 5, None, row_b, key)
    assert missing == "left" and set(fields["change"]) == {"missing on the left"}
    assert "9876543210" not in fields.to_csv()


HAS_DB = os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".db.json")) \
    or bool(os.environ.get("QA_DB_HOST"))


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_environment_compare_against_the_same_database_twice(app_db, monkeypatch):
    """The same database under two names must report no differences — the honest baseline for this feature."""
    import db
    from core import env_compare as ecmp
    base = db.load_config()
    two = [dict(base, name="Env A"), dict(base, name="Env B")]
    monkeypatch.setattr(db, "load_connections", lambda: two)
    assert db.connection_names() == ["Env A", "Env B"]

    tables, _, _ = db.run("SELECT TABLE_NAME AS t FROM information_schema.tables WHERE table_schema = DATABASE() "
                          "AND TABLE_ROWS BETWEEN 1 AND 5000 ORDER BY TABLE_NAME LIMIT 1", name="Env A")
    table = tables.iloc[0]["t"]
    a, _, _ = db.run(ecmp.count_sql(table), name="Env A")
    b, _, _ = db.run(ecmp.count_sql(table), name="Env B")
    counts = ecmp.count_table({table: int(a.iloc[0]["rows"])}, {table: int(b.iloc[0]["rows"])}, [table])
    assert list(counts["status"]) == ["same"] and counts.iloc[0]["difference"] == 0

    sql = f"SELECT * FROM `{table}` LIMIT 50"
    df_a, _, _ = db.run(sql, name="Env A")
    df_b, _, _ = db.run(sql, name="Env B")
    keys = ecmp.key_candidates(df_a)
    if keys:
        out = ecmp.compare_results(df_a, df_b, keys[0])
        assert out["different"].empty and out["only_a"].empty and out["only_b"].empty
        column = keys[0]
        value = df_a.iloc[0][column]
        rec_sql = ecmp.record_sql(table, column)
        row_a, _, _ = db.run(rec_sql, params=[str(value)], name="Env A")
        row_b, _, _ = db.run(rec_sql, params=[str(value)], name="Env B")
        fields, missing = ecmp.record_diff(table, column, value, row_a.iloc[0].to_dict(), row_b.iloc[0].to_dict(),
                                           store.snapshot_key())
        assert missing is None and not (fields["change"] == "changed").any()

    missing_table = ecmp.count_table({table: 5}, {}, [table])
    assert missing_table.iloc[0]["status"] == "missing in B"


# ------------------------------------------------------------------ scheduled checks (headless runner)
def _check(cid, **kw):
    from core import checks as cc
    base = {"id": cid, "title": cid.title(), "sql": "SELECT 1", "schedule": "daily"}
    return cc.normalise(base | kw) | {"schedule": (base | kw)["schedule"]}


def test_scheduler_selects_by_schedule_tag_id_and_severity():
    import scheduler
    checks = [_check("a", tags=["payments"], severity="high"), _check("b", schedule="weekly", tags=["leads"]),
              _check("c", severity="low")]
    assert [c["id"] for c in scheduler.select(checks, schedule="daily")] == ["a", "c"]
    assert [c["id"] for c in scheduler.select(checks, tags=["leads"])] == ["b"]
    assert [c["id"] for c in scheduler.select(checks, ids=["c"])] == ["c"]
    assert [c["id"] for c in scheduler.select(checks, severities=["high"])] == ["a"]
    assert len(scheduler.select(checks)) == 3


def test_scheduler_due_only_respects_the_schedule_window():
    import datetime as dt
    import scheduler
    now = dt.datetime(2026, 9, 18, 9, 0, 0)
    daily, weekly, manual = _check("d"), _check("w", schedule="weekly"), _check("m", schedule="manual")
    history = {"d": {"latest": {"ran_at": (now - dt.timedelta(hours=2)).isoformat()}},
               "w": {"latest": {"ran_at": (now - dt.timedelta(days=8)).isoformat()}},
               "m": {"latest": {"ran_at": now.isoformat()}}}
    assert not scheduler.is_due(daily, history, now)
    assert scheduler.is_due(weekly, history, now)
    assert scheduler.is_due(manual, history, now)                     # manual has no window: always allowed
    assert scheduler.is_due(daily, {}, now) and scheduler.is_due(daily, {"d": {"latest": {"ran_at": "nonsense"}}}, now)


def test_scheduler_records_runs_evaluates_and_never_reports_rows(app_db, monkeypatch, tmp_path):
    import scheduler
    frames = {"ok": pd.DataFrame(), "bad": pd.DataFrame([{"id": 1, "mobile": "9876543210"}])}

    def fake_run(sql, params=None, max_rows=None, name=None, cfg=None):
        if "boom" in sql:
            raise RuntimeError("Table 'x' doesn't exist")
        return (frames["bad"] if "bad" in sql else frames["ok"]), 0.5, False
    monkeypatch.setattr(scheduler.db, "run", fake_run)
    checks = [_check("clean", sql="SELECT ok"), _check("dirty", sql="SELECT bad", alerts=["rows > 0"]),
              _check("broken", sql="SELECT boom", alerts=["query failed"])]
    results = scheduler.run_checks(checks, "staging")
    by = {r["id"]: r for r in results}
    assert by["clean"]["status"] == "passed" and by["clean"]["rows"] == 0
    assert by["dirty"]["status"] == "failed" and by["dirty"]["alerts"] == ["1 rows > 0"]
    assert by["broken"]["status"] == "error" and by["broken"]["alerts"] == ["query failed"]
    assert store.check_history("staging", "dirty")[0]["result_rows"] == 1      # counts are recorded, not rows

    summary = scheduler.summarise(results, "staging", "shop")
    report = scheduler.text_report(summary, results)
    payload = json.dumps({"summary": summary, "report": report})
    assert "9876543210" not in payload and "mobile" not in payload
    assert summary["failed"] == ["dirty"] and summary["errored"] == ["broken"]
    assert len(summary["alerts"]) == 2

    # second run: the count did not change, so "rows increased" must not fire
    again = {r["id"]: r for r in scheduler.run_checks(
        [_check("dirty", sql="SELECT bad", alerts=["rows increased"])], "staging")}
    assert again["dirty"]["previous_rows"] == 1 and again["dirty"]["alerts"] == []


def test_scheduler_notifiers_write_files_and_stay_quiet_when_all_is_well(app_db, tmp_path, monkeypatch):
    import scheduler
    results = [{"id": "a", "title": "A", "area": "x", "severity": "high", "status": "passed", "rows": 0,
                "previous_rows": 0, "change": 0, "seconds": 0.1, "error": None, "alerts": []}]
    summary = scheduler.summarise(results, "staging", "shop")
    posted = []
    monkeypatch.setitem(scheduler.NOTIFIERS, "webhook", lambda s, r, o: posted.append(s) or "200")
    options = {"log": str(tmp_path / "checks.log"), "json": str(tmp_path / "checks.json")}
    scheduler.notify(["log", "json", "webhook"], summary, results, options)
    assert posted == []                                               # nothing failed → no webhook
    assert "nothing failed" in open(options["log"]).read()
    assert json.load(open(options["json"]))["summary"]["checks"] == 1
    results[0].update(status="failed", rows=3, alerts=["3 rows > 0"])
    scheduler.notify(["webhook"], scheduler.summarise(results, "staging", "shop"), results, options)
    assert len(posted) == 1


def test_scheduler_local_notify_hook_is_called_and_its_errors_are_contained(app_db, tmp_path, monkeypatch):
    import scheduler
    monkeypatch.setattr(scheduler.packs, "LOCAL_DIR", str(tmp_path))
    (tmp_path / "notify.py").write_text(
        "import json\n"
        "def send(summary, results):\n"
        f"    open({str(tmp_path / 'called.json')!r}, 'w').write(json.dumps(summary['checks']))\n")
    results = [{"id": "a", "title": "A", "area": "x", "severity": "high", "status": "failed", "rows": 2,
                "previous_rows": 0, "change": 2, "seconds": 0.1, "error": None, "alerts": []}]
    sent = scheduler.notify([], scheduler.summarise(results, "staging", "db"), results, {})
    assert ("local/notify.py", "sent") in sent and json.load(open(tmp_path / "called.json")) == 1
    (tmp_path / "notify.py").write_text("def send(summary, results):\n    raise RuntimeError('boom')\n")
    assert scheduler.notify([], scheduler.summarise(results, "staging", "db"), results, {}) == []   # contained


# ------------------------------------------------------------------ .env file and connection setup
def test_env_file_parsing_quotes_comments_and_export():
    import envfile
    values = envfile.parse('\n'.join([
        "# a comment", "", "QA_DB_HOST=db.example.com  # inline comment",
        "export QA_DB_PORT=3307", 'QA_DB_PASSWORD="p@ss word#1"', "QA_SETUP_CODE='quoted'",
        "EMPTY=", "not a line", "  SPACED = value  "]))
    assert values == {"QA_DB_HOST": "db.example.com", "QA_DB_PORT": "3307",
                      "QA_DB_PASSWORD": "p@ss word#1", "QA_SETUP_CODE": "quoted", "EMPTY": "", "SPACED": "value"}


def test_env_file_never_overwrites_real_environment(tmp_path, monkeypatch):
    import envfile
    path = tmp_path / ".env"
    path.write_text("QA_DB_HOST=from-file\nQA_DB_NAME=shop\n")
    monkeypatch.setenv("QA_DB_HOST", "from-shell")
    monkeypatch.delenv("QA_DB_NAME", raising=False)
    applied = envfile.load(str(path))
    assert applied == ["QA_DB_NAME"] and os.environ["QA_DB_HOST"] == "from-shell"
    assert os.environ["QA_DB_NAME"] == "shop"
    assert envfile.load(str(tmp_path / "missing.env")) == []
    envfile.load(str(path), override=True)
    assert os.environ["QA_DB_HOST"] == "from-file"


@pytest.mark.parametrize("url, expect", [
    ("mysql://qa:p%40ss@db.example.com:3307/shop",
     {"host": "db.example.com", "port": 3307, "user": "qa", "password": "p@ss", "database": "shop"}),
    ("mysql+pymysql://u:p@h/d?charset=utf8", {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "d"}),
    ("mariadb://u@h:3306/d", {"host": "h", "port": 3306, "user": "u", "password": "", "database": "d"}),
])
def test_connection_url_is_parsed(url, expect):
    import db
    assert db.parse_url(url) == expect


@pytest.mark.parametrize("bad", ["", "not a url::", "mysql://host-only", "mysql://user@host"])
def test_bad_connection_url_explains_the_format(bad):
    import db
    with pytest.raises(ValueError) as e:
        db.parse_url(bad)
    assert "mysql://" in str(e.value) or "Paste" in str(e.value)


def test_connection_form_is_validated_and_labelled(monkeypatch):
    import db
    cfg = db.clean_config({"name": "  ", "host": " h ", "port": "3306", "user": " u ", "database": " d ", "password": "p"})
    assert cfg == {"name": "d @ h", "host": "h", "port": 3306, "user": "u", "database": "d", "password": "p"}
    for bad, word in (({"host": "h", "user": "u"}, "database"), ({"database": "d", "user": "u"}, "host"),
                      ({"host": "h", "database": "d"}, "username")):
        with pytest.raises(ValueError) as e:
            db.clean_config(bad)
        assert word in str(e.value)
    with pytest.raises(ValueError):
        db.clean_config({"host": "h", "database": "d", "user": "u", "port": "99999"})
    with pytest.raises(ValueError):
        db.clean_config({"host": "h", "database": "d", "user": "u", "port": "abc"})


def test_environment_connections_are_not_editable_or_saved(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "CONFIG_PATH", str(tmp_path / ".db.json"))
    for key, value in (("QA_DB_HOST", "h"), ("QA_DB_PORT", "3306"), ("QA_DB_USER", "u"),
                       ("QA_DB_PASSWORD", "p"), ("QA_DB_NAME", "shop")):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("QA_DB_LABEL", "From env")
    own = {"name": "Mine", "host": "h2", "port": 3306, "user": "u2", "password": "p2", "database": "d2"}
    db.save_connections(db.load_connections() + [own])
    names = db.connection_names()
    assert names == ["From env", "Mine"]
    assert db.is_from_env("From env") and not db.is_from_env("Mine")
    assert [c["name"] for c in json.load(open(db.CONFIG_PATH))["connections"]] == ["Mine"]   # env one is not written


def test_connection_ports_are_always_integers(tmp_path, monkeypatch):
    """A port from the environment is a string; mixing it with file ports broke the connections table."""
    import db
    path = tmp_path / ".db.json"
    path.write_text(json.dumps({"connections": [{"name": "File", "host": "h", "port": "3307", "user": "u",
                                                 "password": "p", "database": "d"}]}))
    monkeypatch.setattr(db, "CONFIG_PATH", str(path))
    for key, value in (("QA_DB_HOST", "h"), ("QA_DB_PORT", "3306"), ("QA_DB_USER", "u"),
                       ("QA_DB_PASSWORD", "p"), ("QA_DB_NAME", "shop")):
        monkeypatch.setenv(key, value)
    ports = [c["port"] for c in db.load_connections()]
    assert ports == [3306, 3307] and all(isinstance(p, int) for p in ports)
    assert pd.DataFrame([{"port": p} for p in ports])["port"].dtype.kind == "i"
