# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Phase 2: check library, dashboard storage, query analyzer, consistency rules, table compare, playbooks, schema diff."""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import db  # noqa: E402
import store  # noqa: E402
from core import api_compare as ac  # noqa: E402
from core import checks as cc  # noqa: E402
from core import consistency, filters, performance, playbooks, schema_compare, table_compare  # noqa: E402
from test_investigation import fake_schema  # noqa: E402


# ------------------------------------------------------------------ check library
def test_checks_get_sensible_defaults():
    c = cc.normalise({"id": "x", "sql": "SELECT 1"})
    assert (c["expect"], c["severity"], c["tags"], c["area"]) == ("empty", "medium", [], "Other")
    i = cc.normalise({"id": "y", "sql": "SELECT 1", "expect": "info", "tags": "Payments, LEADS ", "severity": "bogus"})
    assert i["severity"] == "info" and i["tags"] == ["payments", "leads"]


@pytest.mark.parametrize("expect, rows, status", [
    ("empty", 0, "passed"), ("empty", 3, "failed"), ("at most 5", 5, "passed"), ("at most 5", 6, "failed"),
    ("at least 1", 0, "failed"), ("exactly 2 rows", 2, "passed"), ("info", 9, "info"), ("weird", 1, "info"),
])
def test_check_evaluation(expect, rows, status):
    c = cc.normalise({"id": "x", "sql": "s", "expect": expect})
    assert cc.evaluate(c, {"df": pd.DataFrame({"a": range(rows)}), "error": None}) == status


def test_check_evaluation_of_errors_outdated_and_not_run():
    c = cc.normalise({"id": "x", "sql": "s"})
    assert cc.evaluate(c, None) == "not run"
    assert cc.evaluate(c, {"df": None, "error": "boom"}) == "error"
    assert cc.evaluate(c, {"df": None, "error": "Table not found", "stale_schema": True}) == "outdated"


def test_change_detection_counts_only():
    assert cc.change(17, 3) == 14 and cc.change_text(14) == "+14 since last run"
    assert cc.change(3, 3) == 0 and cc.change_text(0) == "no change"
    assert cc.change(None, 3) is None and cc.change_text(None) == ""


def test_alert_rules():
    c = cc.normalise({"id": "x", "sql": "s", "alerts": ["rows > 10", "rows increased", "query failed", "slower than 5s"]})
    assert cc.alerts(c, "failed", 12, 1.0, 12) == ["12 rows > 10"]
    assert cc.alerts(c, "failed", 12, 6.5, 4) == ["12 rows > 10", "rows increased 4 → 12", "took 6.5s > 5s"]
    assert cc.alerts(c, "error", None, None, 4) == ["query failed"]
    assert cc.alerts(cc.normalise({"id": "y", "sql": "s"}), "failed", 99, 99, 0) == []


def test_filtering_by_database_severity_tags_area_and_text():
    items = [cc.normalise(x) for x in [
        {"id": "a", "sql": "SELECT 1", "title": "Orphan orders", "severity": "high", "tags": ["orders"], "area": "Orders"},
        {"id": "b", "sql": "SELECT 2", "title": "Dup users", "severity": "low", "tags": ["users"], "database": "other"},
        {"id": "c", "sql": "SELECT 3", "title": "Payment gap", "severity": "critical", "owner": "payments team"},
    ]]
    assert [c["id"] for c in cc.filter_checks(items, "main")] == ["a", "c"]
    assert [c["id"] for c in cc.filter_checks(items, "main", severities=["critical"])] == ["c"]
    assert [c["id"] for c in cc.filter_checks(items, "main", tags=["orders"])] == ["a"]
    assert [c["id"] for c in cc.filter_checks(items, "main", term="payments team")] == ["c"]


# ------------------------------------------------------------------ storage (no rows stored)
@pytest.fixture()
def app_store(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setattr(auth, "SETUP_CODE_FILE", str(tmp_path / ".setup_code"))
    monkeypatch.setenv("QA_SETUP_CODE", "CODE")
    auth.init()
    store.init()
    auth.sign_up("owner", "Owner", "o@example.com", "Password1234", code="CODE")
    auth.sign_up("guest", "Guest", "g@example.com", "Password1234")
    return {u["username"]: u["id"] for u in auth.list_users()}


def test_check_runs_keep_latest_and_previous_counts(app_store):
    c = cc.normalise({"id": "orphans", "sql": "s", "title": "Orphans", "severity": "high"})
    store.record_check_run(c, "staging", "failed", 3, 0.2, None, app_store["owner"])
    store.record_check_run(c, "staging", "failed", 17, 0.3, None, app_store["owner"])
    store.record_check_run(c, "prod", "passed", 0, 0.1, None, app_store["owner"])
    runs = store.latest_runs("staging")["orphans"]
    assert runs["latest"]["result_rows"] == 17 and runs["previous"]["result_rows"] == 3
    assert "prod" not in {r["connection"] for r in store.check_history("staging")}
    with auth.conn() as con:
        columns = [r[1] for r in con.execute("PRAGMA table_info(check_runs)")]
    assert not {"rows", "data", "result", "df"} & set(columns)       # counts only, never rows


def test_rules_and_playbooks_respect_ownership_and_sharing(app_store):
    owner, guest = app_store["owner"], app_store["guest"]
    rid = store.save_rule(owner, "Rule A", {"x": 1}, shared=False)
    pid = store.save_playbook(owner, "Private PB", {"steps": []}, shared=False)
    store.save_playbook(owner, "Team PB", {"steps": []}, shared=True)
    assert [r["name"] for r in store.rules(guest)] == []
    assert [p["name"] for p in store.playbooks(guest)] == ["Team PB"]
    store.delete_playbook(pid, guest)                           # not the owner: nothing happens
    assert len(store.playbooks(owner)) == 2
    store.save_rule(owner, "Rule A renamed", {"x": 2}, shared=True, rule_id=rid)
    assert store.rules(guest)[0]["name"] == "Rule A renamed" and store.rules(guest)[0]["spec"] == {"x": 2}


def test_schema_snapshots_store_structure_only(app_store):
    s = fake_schema()
    store.save_snapshot(app_store["owner"], "before", "staging", "main", s)
    snap = store.snapshots()[0]
    data = store.snapshot_data(snap["id"])
    assert snap["tables"] == 4 and set(data) == {"tables", "columns", "fks"}
    assert {"tbl", "col", "type"} <= set(data["columns"][0])


# ------------------------------------------------------------------ query analyzer
FULL_SCAN = {"query_block": {"cost_info": {"query_cost": "2327589.56"}, "table": {
    "table_name": "leads", "access_type": "ALL", "rows_examined_per_scan": 17914123, "filtered": "100.00"}}}
CONST = {"query_block": {"cost_info": {"query_cost": "1.00"}, "table": {
    "table_name": "users", "access_type": "const", "key": "email", "rows_examined_per_scan": 1, "using_index": True}}}
JOIN = {"query_block": {"cost_info": {"query_cost": "500536"}, "ordering_operation": {
    "using_temporary_table": True, "using_filesort": True, "grouping_operation": {"nested_loop": [
        {"table": {"table_name": "c", "access_type": "index", "key": "PRIMARY", "rows_examined_per_scan": 10303}},
        {"table": {"table_name": "l", "access_type": "ref", "key": "idx", "rows_examined_per_scan": 441}}]}}}}


def test_plan_parsing_reads_every_table_and_flag():
    p = performance.parse_plan(json.dumps(JOIN))
    assert [t["table"] for t in p["tables"]] == ["c", "l"]
    assert p["filesort"] and p["temporary"] and p["query_cost"] == 500536
    assert p["tables"][1]["how"] == "index lookup"


def test_full_scan_on_huge_table_is_high_and_explains_the_function_on_a_column():
    sql = "SELECT * FROM leads WHERE DATE(created) = '2026-09-01'"
    warnings, suggestions = performance.assess(performance.parse_plan(FULL_SCAN), sql)
    assert warnings[0][0] == "high" and "17,914,123" in warnings[0][1]
    assert any("DATE(" in w[1] for w in warnings) and any("SELECT *" in w[1] for w in warnings)
    assert suggestions and "indexed column" in suggestions[0]


def test_single_row_lookup_has_no_serious_warnings():
    warnings, suggestions = performance.assess(performance.parse_plan(CONST), "SELECT uid FROM users WHERE email = 'x'")
    assert not [w for w in warnings if w[0] in ("high", "medium")]
    assert not any(w[1].startswith("No LIMIT") for w in warnings)
    assert suggestions[0].startswith("No serious problems")


def test_sql_observations_ignore_text_inside_strings():
    assert not performance.sql_observations("SELECT id FROM t WHERE note = 'DATE(x) = 1 OR LIKE ''%a''' LIMIT 5")
    notes = [n[1] for n in performance.sql_observations("SELECT id FROM t WHERE name LIKE '%abc' LIMIT 5")]
    assert any("wildcard" in n for n in notes)


# ------------------------------------------------------------------ consistency rules
def rule(expect="exists", sample=0, value="SUCCESS"):
    return {"source": {"table": "payments", "key": "user_id", "filters": [{"col": "status", "op": "equals", "val": value}]},
            "target": {"table": "leads", "key": "user_id", "filters": []}, "expect": expect, "sample": sample,
            "source_pk": "id"}


def test_rule_validation_explains_what_is_missing():
    s = fake_schema()
    assert consistency.validate(rule(), s) == []
    bad = rule()
    bad["target"]["key"] = "nope"
    bad["expect"] = "maybe"
    assert len(consistency.validate(bad, s)) == 2


@pytest.mark.parametrize("sample", [0, 500])
def test_rule_statements_are_read_only_with_values_bound(sample):
    s = fake_schema()
    st = consistency.statements(rule(sample=sample, value="x' OR 1=1 --"), s)
    for key, (sql, params) in st.items():
        db.check_read_only(sql)
        assert "OR 1=1" not in sql and sql.count("%s") == len(params)
    assert "NOT EXISTS" in st["failed"][0] and "password" not in st["failing_rows"][0]
    if sample:
        assert "LIMIT 500) AS s" in st["checked"][0]


def test_must_not_exist_rule_flips_the_condition():
    sql = consistency.statements(rule(expect="not exists"), fake_schema())["failed"][0]
    assert " EXISTS (" in sql and "NOT EXISTS" not in sql


def test_rule_sentence_and_check_export_escape_values():
    s = fake_schema()
    r = rule(value="SUCCESS' ; DROP TABLE x; --")
    assert consistency.sentence(r).startswith("Every `payments` row where status equals SUCCESS")
    check = consistency.as_check(r, s, "Paid without lead")
    db.check_read_only(check["sql"])
    assert "\\'" in check["sql"] and check["expect"] == "empty" and check["severity"] == "high"


# ------------------------------------------------------------------ compare tables
def test_table_compare_finds_mismatch_missing_and_duplicates_with_normalisation():
    spec = {"source": {"table": "users", "key": "id"}, "target": {"table": "leads", "key": "user_id"},
            "fields": [{"source": "email", "target": "email"}, {"source": "status", "target": "status"}]}
    src = pd.DataFrame([{"id": 1, "email": "A@x.io", "status": "active"},
                        {"id": 2, "email": "b@x.io", "status": "active"},
                        {"id": 3, "email": "c@x.io", "status": "active"}])
    tgt = pd.DataFrame([{"user_id": "1", "email": " a@x.io", "status": "ACTIVE"},
                        {"user_id": 2, "email": "b@x.io", "status": "pending"},
                        {"user_id": 2, "email": "b@x.io", "status": "active"}])
    summary, details = table_compare.compare(spec, src, tgt)
    assert summary == {"compared": 3, "matching": 1, "mismatched": 1, "missing_in_target": 1, "duplicate_in_target": 1}
    assert set(details["status"]) == {"Mismatch", "Missing in target", "Duplicate in target"}
    strict = ac.Options(case_insensitive=False, trim=False)
    assert table_compare.compare(spec, src, tgt, strict)[0]["matching"] == 0


def test_table_compare_sql_is_read_only_batched_and_bound():
    s = fake_schema()
    spec = {"source": {"table": "users", "key": "id", "filters": [{"col": "status", "op": "equals", "val": "a"}]},
            "target": {"table": "payments", "key": "user_id", "filters": []},
            "fields": [{"source": "status", "target": "status"}]}
    sql, params = table_compare.source_sql(spec, s, 100)
    db.check_read_only(sql)
    assert params == ["a"] and "LIMIT 100" in sql
    batches = table_compare.target_sql_batches(spec, s, list(range(1200)))
    assert len(batches) == 3 and all(q.count("%s") == len(p) for q, p in batches)
    rev, rev_params = table_compare.reverse_sql(spec, s, 100)
    db.check_read_only(rev)


# ------------------------------------------------------------------ playbooks
PB = {"name": "Dups", "parameters": [{"name": "table", "type": "table"}, {"name": "column", "type": "column", "table_param": "table"},
                                     {"name": "value", "type": "value"}],
      "steps": [{"title": "Count", "sql": "SELECT {{column}}, COUNT(*) FROM {{table}} WHERE {{column}} = :value GROUP BY {{column}}"}]}


def test_playbook_rendering_binds_values_and_checks_names_against_the_schema():
    s = fake_schema()
    sql, params, missing, err = playbooks.render_sql(PB["steps"][0]["sql"], {"table": "users", "column": "email", "value": "a'b"}, s, PB)
    assert err is None and missing == [] and params == ["a'b"]
    assert sql.startswith("SELECT `email`, COUNT(*) FROM `users` WHERE `email` = %s")


@pytest.mark.parametrize("values, message", [
    ({"table": "users`; DROP TABLE x; --", "column": "email", "value": "1"}, "not a valid table"),
    ({"table": "nope", "column": "email", "value": "1"}, "does not exist"),
    ({"table": "users", "column": "user_id", "value": "1"}, "does not exist in table"),
])
def test_playbook_identifier_injection_and_unknown_names_are_refused(values, message):
    assert message in playbooks.render_sql(PB["steps"][0]["sql"], values, fake_schema(), PB)[3]


def test_playbook_reports_missing_values_before_running():
    assert playbooks.render_sql(PB["steps"][0]["sql"], {"table": "users"}, fake_schema(), PB)[2] == ["column", "value"]


def test_playbook_definition_problems():
    bad = {"name": "", "parameters": [{"name": "t", "type": "value"}],
           "steps": [{"sql": "SELECT * FROM {{t}} WHERE a = :undeclared"}, {"sql": "DELETE FROM x"}]}
    msgs = " ".join(playbooks.problems(bad))
    assert "name" in msgs and ":undeclared" in msgs and "declared as a value" in msgs and "DELETE" in msgs
    assert playbooks.problems(PB) == []


def test_shipped_templates_are_valid():
    import yaml
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "playbooks.yaml")) as f:
        for spec in yaml.safe_load(f):
            assert playbooks.problems(spec) == [], spec["name"]


def test_ai_plan_becomes_a_playbook_with_identifier_parameter():
    plan = {"understanding": "u", "steps": [{"title": "t", "purpose": "p", "sql": "SELECT 1 FROM x WHERE id = :identifier",
                                              "look_for": "l"}]}
    spec = playbooks.from_ai_plan(plan, "Payment done but lead missing", "user id")
    assert spec["parameters"][0] == {"name": "identifier", "label": "user id", "type": "value", "table_param": "", "hint": ""}
    assert playbooks.problems(spec) == []


# ------------------------------------------------------------------ schema compare
def test_schema_diff_reports_every_kind_of_change():
    s = fake_schema()
    left = schema_compare.from_schema(s)
    right = json.loads(json.dumps(left))
    right["tables"] = [t for t in right["tables"] if t["name"] != "big_log"] + [{"name": "new_table", "rows_est": 5}]
    right["columns"] = [c for c in right["columns"] if not (c["tbl"] == "users" and c["col"] == "mobile")]
    for c in right["columns"]:
        if (c["tbl"], c["col"]) == ("users", "email"):
            c["type"], c["nullable"], c["key"] = "varchar(100)", "NO", ""
    right["columns"].append({"tbl": "users", "col": "phone", "type": "varchar(20)", "nullable": "YES", "key": "", "default": "", "extra": ""})
    right["fks"] = [{"tbl": "payments", "col": "user_id", "ref_tbl": "users", "ref_col": "id"}]
    d = schema_compare.diff(left, right)
    s_ = schema_compare.summary(d)
    assert s_["Missing table"] == 1 and s_["Extra table"] == 1 and s_["Missing column"] == 1 and s_["Extra column"] == 1
    assert s_["Type differs"] == 1 and s_["Nullable differs"] == 1 and s_["Index differs"] == 1 and s_["Foreign key extra"] == 1
    report = schema_compare.text_report(d, "before", "after")
    assert "users.mobile" in report and "MISSING TABLE" in report and "big_log" in report


def test_identical_schemas_have_no_differences_and_row_count_changes_are_separate():
    left = schema_compare.from_schema(fake_schema())
    right = json.loads(json.dumps(left))
    assert schema_compare.diff(left, right).empty
    right["tables"][0]["rows_est"] = 100_000
    counts = schema_compare.row_counts(left, right)
    assert list(counts["table"]) == [left["tables"][0]["name"]]


def test_filter_description_and_identifier_guard():
    assert filters.describe([{"col": "status", "op": "equals", "val": "paid"}, {"col": "x", "op": "is empty"}]) == \
        "status equals paid and x is empty"
    with pytest.raises(ValueError):
        filters.ident("a b")



def test_unreachable_database_fails_fast_after_the_first_timeout(monkeypatch):
    import time
    import pymysql
    calls = []

    def boom(**kwargs):
        calls.append(kwargs["host"])
        raise pymysql.err.OperationalError(2003, "Can't connect (timed out)")

    monkeypatch.setattr(pymysql, "connect", boom)
    monkeypatch.setattr(db, "_unreachable_until", {})
    cfg = {"host": "unreachable.example", "port": 3306, "user": "u", "password": "p", "database": "d"}
    with pytest.raises(pymysql.err.OperationalError):
        db.connect(cfg=cfg)
    started = time.time()
    with pytest.raises(pymysql.err.OperationalError, match="unreachable a moment ago"):
        db.connect(cfg=cfg)
    assert time.time() - started < 0.5 and len(calls) == 1          # second attempt did not dial again
    monkeypatch.setitem(db._unreachable_until, ("unreachable.example", 3306), time.time() - 1)
    with pytest.raises(pymysql.err.OperationalError, match="timed out"):
        db.connect(cfg=cfg)
    assert len(calls) == 2                                           # retried once the back-off expired
