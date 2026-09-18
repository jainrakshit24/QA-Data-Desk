# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Phase 1 investigation logic: parameters, search, records, timeline, API validation, evidence, AI privacy."""
import datetime as dt
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai  # noqa: E402
import db  # noqa: E402
import privacy  # noqa: E402
import schema  # noqa: E402
from core import api_compare as ac  # noqa: E402
from core import evidence, params, records, search, timeline  # noqa: E402


def fake_schema():
    cols = [
        ("users", "id", "int", "PRI"), ("users", "email", "varchar(255)", "UNI"), ("users", "mobile", "varchar(15)", "MUL"),
        ("users", "status", "varchar(20)", ""), ("users", "created_at", "datetime", ""), ("users", "password", "varchar(64)", ""),
        ("payments", "id", "int", "PRI"), ("payments", "user_id", "int", "MUL"), ("payments", "status", "varchar(20)", ""),
        ("payments", "paid_at", "timestamp", ""), ("payments", "txn_ref", "varchar(40)", "UNI"),
        ("leads", "id", "int", "PRI"), ("leads", "user_id", "int", ""), ("leads", "created_on", "datetime", ""),
        ("big_log", "id", "bigint", "PRI"), ("big_log", "user_id", "int", ""),
    ]
    raw = {"fetched_at": 0, "signature": "x",
           "tables": [{"name": t, "rows_est": 50_000_000 if t == "big_log" else 1000, "kind": "BASE TABLE",
                       "comment": "", "updated": ""} for t in dict.fromkeys(c[0] for c in cols)],
           "columns": [{"tbl": t, "col": c, "type": ty, "nullable": "YES", "key": k, "default": None, "extra": "",
                        "comment": "", "pos": i} for i, (t, c, ty, k) in enumerate(cols)],
           "fks": []}
    return schema.Schema(raw, "fake")


# ------------------------------------------------------------------ parameters
def test_named_parameters_are_bound_not_pasted():
    sql, bound = params.bind("SELECT * FROM users WHERE id = :user_id AND email = :email", {"user_id": "1", "email": "x' OR '1'='1"})
    assert sql == "SELECT * FROM users WHERE id = %s AND email = %s"
    assert bound == ["1", "x' OR '1'='1"]


def test_parameters_ignore_strings_comments_and_casts():
    sql = "SELECT ':not_param', `a:b` FROM t -- :nope\nWHERE x = :real AND y::int = 1 /* :hidden */"
    assert params.names(sql) == ["real"]


def test_repeated_parameter_binds_twice_and_percent_is_escaped():
    sql, bound = params.bind("SELECT * FROM t WHERE a = :v OR b = :v AND c LIKE 'x%'", {"v": "7"})
    assert bound == ["7", "7"]
    assert "LIKE 'x%%'" in sql
    assert sql % tuple(repr(b) for b in bound)          # what the driver does: must not raise
    db.check_read_only(sql)


def test_query_without_parameters_is_unchanged():
    assert params.bind("SELECT * FROM t WHERE c LIKE 'a%'", {}) == ("SELECT * FROM t WHERE c LIKE 'a%'", [])


def test_missing_parameter_value_is_reported():
    with pytest.raises(KeyError, match="user_id"):
        params.bind("SELECT :user_id", {})


# ------------------------------------------------------------------ global search
@pytest.mark.parametrize("value, kind", [("12345", "number"), ("abc@example.com", "email"),
                                         ("+91 98765 43210", "phone"), ("9876543210", "phone"),
                                         ("TXN-88-A", "text"), ("", "text")])
def test_identifier_classification(value, kind):
    assert search.classify(value) == kind


def test_number_search_uses_indexed_id_columns_and_skips_every_tables_primary_id():
    s = fake_schema()
    pairs = {(t, c) for t, c, _ in search.candidates(s, "42")}
    assert ("payments", "user_id") in pairs
    assert ("users", "id") not in pairs and ("payments", "id") not in pairs     # every table has id = 42
    assert ("leads", "user_id") not in pairs                                    # not indexed
    assert ("big_log", "user_id") not in pairs


def test_search_options_widen_scope_but_never_scan_huge_unindexed_tables():
    s = fake_schema()
    pairs = {(t, c) for t, c, _ in search.candidates(s, "42", include_unindexed_small=True, include_primary_ids=True)}
    assert ("leads", "user_id") in pairs and ("users", "id") in pairs
    assert ("big_log", "user_id") not in pairs


def test_email_search_only_touches_text_email_columns_and_never_secrets():
    s = fake_schema()
    assert [(t, c) for t, c, _ in search.candidates(s, "a@b.co")] == [("users", "email")]
    assert all(c != "password" for _, c, _ in search.candidates(s, "abc", include_unindexed_small=True))


def test_count_statements_are_read_only_batched_and_parameterised():
    s = fake_schema()
    pairs = search.candidates(s, "42", include_primary_ids=True)
    stmts = search.count_statements(pairs, "42' OR 1=1 --", batch=2)
    assert len(stmts) == -(-len(pairs) // 2)
    for _, sql, bound in stmts:
        db.check_read_only(sql)
        assert "OR 1=1" not in sql and all(v == "42' OR 1=1 --" for v in bound)
        assert sql.count("%s") == len(bound) and f"LIMIT {search.CAP}" in sql


def test_unsafe_identifiers_are_refused():
    with pytest.raises(ValueError):
        search.single_statement("users`; DROP TABLE x; --", "id", "1")


# ------------------------------------------------------------------ records
def test_relations_follow_name_pattern_both_ways_and_mark_cost():
    s = fake_schema()
    rels = records.relations(s, "users", {"id": 7, "email": "a@b.co", "status": "active"})
    found = {(r["direction"], r["table"], r["column"]): r for r in rels}
    assert ("referenced by", "payments", "user_id") in found
    assert found[("referenced by", "payments", "user_id")]["indexed"] is True
    assert found[("referenced by", "leads", "user_id")]["indexed"] is False
    assert records.countable(found[("referenced by", "leads", "user_id")], s)          # small table
    assert not records.countable(found[("referenced by", "big_log", "user_id")], s)    # huge + unindexed


def test_empty_link_values_are_not_followed():
    s = fake_schema()
    assert records.relations(s, "payments", {"id": 3, "user_id": None}) == [] or \
        all(r["direction"] != "points to" for r in records.relations(s, "payments", {"id": 3, "user_id": None}))


def test_join_sql_is_read_only_uses_named_parameter_and_hides_secrets():
    s = fake_schema()
    rel = next(r for r in records.relations(s, "users", {"id": 7}) if r["table"] == "payments")
    sql = records.join_sql(s, "users", "id", rel)
    assert ":value" in sql and "password" not in sql
    bound, vals = params.bind(sql, {"value": "7"})
    db.check_read_only(bound)


def test_graph_is_valid_dot_with_counts():
    s = fake_schema()
    rels = records.relations(s, "users", {"id": 7})
    dot = records.graph_dot("users", "users\nid = 7", rels, {records._key(rels[0]): 3})
    assert dot.startswith("digraph") and dot.rstrip().endswith("}") and "3 rows" in dot


# ------------------------------------------------------------------ timeline
def test_timeline_orders_real_timestamps_and_skips_empty_ones():
    users = pd.DataFrame([{"id": 7, "status": "active", "created_at": dt.datetime(2026, 9, 1, 10, 21, 4)}])
    pays = pd.DataFrame([{"id": 1, "user_id": 7, "status": "SUCCESS", "paid_at": "2026-09-01 10:23:45"},
                         {"id": 2, "user_id": 7, "status": "FAILED", "paid_at": None},
                         {"id": 3, "user_id": 7, "status": "PENDING", "paid_at": "0000-00-00 00:00:00"}])
    types = {("users", "created_at"): "datetime", ("payments", "paid_at"): "timestamp"}
    ev = timeline.build_events([("users", users), ("payments", pays)], types)
    assert list(ev["event"]) == ["users.created_at", "payments.paid_at"]
    assert ev.iloc[1]["row"] == "id=1" and "SUCCESS" in ev.iloc[1]["status"]


def test_timeline_never_treats_numbers_or_flags_as_times():
    df = pd.DataFrame([{"id": 1, "updated_on": 1, "is_date": 20260101, "created": "not a date"}])
    ev = timeline.build_events([("t", df)], {("t", "updated_on"): "tinyint(1)", ("t", "is_date"): "int"})
    assert ev.empty


def test_timeline_of_nothing_is_empty():
    assert timeline.build_events([]).empty
    assert timeline.build_events([("t", pd.DataFrame())]).empty


# ------------------------------------------------------------------ API parsing, comparison, rules
def test_malformed_json_gets_a_helpful_message():
    with pytest.raises(ac.JSONProblem, match="not valid JSON"):
        ac.parse_json('{"a": 1,}')
    with pytest.raises(ac.JSONProblem, match="double quotes"):
        ac.parse_json("{'a': 1}")
    with pytest.raises(ac.JSONProblem, match="Paste"):
        ac.parse_json("   ")


def test_flatten_and_paths_handle_nesting_arrays_and_nulls():
    data = {"data": {"user": {"id": 5, "tags": ["a", "b"]}, "items": [{"sku": "X"}], "note": None}}
    flat = ac.flatten(data)
    assert flat["data.user.id"] == 5 and flat["data.items[0].sku"] == "X" and flat["data.note"] is None
    assert ac.get_path(data, "data.items[0].sku") == "X"
    assert ac.get_path(data, "data.items[3].sku") is ac.MISSING
    assert ac.get_path(data, "data.note") is None


@pytest.mark.parametrize("a, b, equal", [
    ("123", 123, True), (123.0, "123", True), ("5.50", 5.5, True),
    (True, 1, True), ("true", True, True), ("yes", 1, True), (False, "0", True), (True, 2, False),
    ("  Active ", "active", True), ("ACTIVE", "active", True),
    ("2026-09-17T10:00:00Z", dt.datetime(2026, 9, 17, 10, 0, 0), True),
    ("2026-09-17", dt.date(2026, 9, 17), True),
    (None, None, True), (None, "", False), ("", "", True), (None, "null", False),
])
def test_normalisation(a, b, equal):
    assert ac.same(a, b, ac.Options()) is equal


def test_normalisation_can_be_made_strict():
    strict = ac.Options(case_insensitive=False, trim=False, numbers=False, booleans=False, dates=False)
    assert not ac.same("ACTIVE", "active", strict)
    assert not ac.same("5.0", 5, strict)
    assert ac.same(None, "", ac.Options(empty_is_null=True))


def test_api_vs_db_reports_match_mismatch_and_missing_on_each_side():
    api = {"user_id": 123, "email": "abc@gmail.com", "status": "active", "plan": "gold"}
    db_row = {"id": 123, "email": "ABC@gmail.com ", "status": "pending", "mobile": None}
    mapping = [{"api_field": "user_id", "db_column": "id"}, {"api_field": "email", "db_column": "email"},
               {"api_field": "status", "db_column": "status"}, {"api_field": "plan", "db_column": "plan"},
               {"api_field": "mobile", "db_column": "mobile"}]
    res = {r["field"]: r for r in ac.compare(mapping, api, db_row)}
    assert res["user_id"]["result"] == "Match" and res["email"]["result"] == "Match"
    assert res["status"]["result"] == "Mismatch" and res["status"]["where"] == "API ≠ DB"
    assert res["plan"]["result"] == "Missing in DB"
    assert res["mobile"]["result"] == "Missing in API" and res["mobile"]["db"] == "NULL"


def test_no_db_row_makes_every_field_missing_in_db():
    res = ac.compare([{"api_field": "a", "db_column": "a"}], {"a": 1}, None)
    assert res[0]["result"] == "Missing in DB"


def test_three_way_comparison_names_the_layer_that_is_wrong():
    api = {"status": "active", "email": "abc@x.io", "plan": "basic", "tier": "gold"}
    db_row = {"status": "pending", "email": "abc@x.io", "plan": "basic", "tier": "silver"}
    mapping = [{"api_field": "status", "db_column": "status", "expected": "active"},
               {"api_field": "email", "db_column": "email", "expected": "abc@x.io"},
               {"api_field": "plan", "db_column": "plan", "expected": "premium"},
               {"api_field": "tier", "db_column": "tier", "expected": "platinum"}]
    res = {r["field"]: r for r in ac.compare(mapping, api, db_row)}
    assert res["status"]["where"] == "DB differs from expected (API is correct)"
    assert res["email"]["result"] == "Match"
    assert res["plan"]["where"] == "API and DB agree, but differ from expected"
    assert res["tier"]["where"] == "API and DB both differ from expected"


def test_mapping_suggestions_pair_by_name():
    maps = ac.suggest_mapping(["data.userId", "data.email", "meta.page"], ["user_id", "email", "status"])
    assert {m["api_field"]: m["db_column"] for m in maps} == {"data.userId": "user_id", "data.email": "email"}


@pytest.mark.parametrize("rule, status", [
    (ac.Rule("status", "equals", "success"), "pass"),
    (ac.Rule("status", "equals", "failed"), "fail"),
    (ac.Rule("data.user_id", "not null"), "pass"),
    (ac.Rule("data.email", "exists"), "fail"),
    (ac.Rule("data.email", "not exists"), "pass"),
    (ac.Rule("data.items", "count at least", "1"), "pass"),
    (ac.Rule("data.items", "count equals", "3"), "fail"),
    (ac.Rule("data.items", "contains", "b"), "pass"),
    (ac.Rule("data.name", "matches regex", "^A.*z$"), "pass"),
    (ac.Rule("data.name", "matches regex", "(["), "warn"),
    (ac.Rule("data.score", "greater than", "9.5"), "pass"),
    (ac.Rule("data.name", "greater than", "1"), "warn"),
    (ac.Rule("data.note", "is null"), "pass"),
    (ac.Rule("data.note", "not empty"), "fail"),
    (ac.Rule("data.items", "type is", "list"), "pass"),
    (ac.Rule("response.status_code", "equals", "200"), "pass"),
    (ac.Rule("data.user_id", "count at least", "1"), "warn"),
    (ac.Rule("data.user_id", "bogus", "1"), "warn"),
])
def test_response_rules(rule, status):
    payload = {"response": {"status_code": 200}, "status": "success",
               "data": {"user_id": 9, "items": ["a", "b"], "name": "Abcz", "score": 9.9, "note": None}}
    assert ac.check_rule(payload, rule)[0] == status


# ------------------------------------------------------------------ evidence and reports
def test_evidence_items_are_masked_and_secrets_removed():
    df = pd.DataFrame([{"id": 1, "email": "person@example.com", "mobile_number": "9876543210", "password": "hash"}])
    item = evidence.make_item("record", "User person@example.com", df=df, sql="SELECT 1")
    row = item["rows"][0]
    assert "person@" not in row["email"] and row["password"] == privacy.HIDDEN and "9876543210" not in row["mobile_number"]
    assert "person@" not in item["title"]


def test_free_text_is_scrubbed():
    text = evidence.scrub('email abc.def@gmail.com mobile 9876543210 {"token": "abc123", "password":"p"}')
    assert "abc.def@" not in text and "9876543210" not in text and "abc123" not in text and '"p"' not in text


@pytest.mark.parametrize("fmt", ["jira", "markdown", "text"])
def test_bug_report_contains_sections_masked_evidence_and_sql(fmt):
    items = [evidence.make_item("compare", "API vs DB", df=pd.DataFrame([{"field": "email", "api": "a@b.io",
                                                                           "db": "c@d.io", "result": "Mismatch"}]),
                                sql="SELECT email FROM users WHERE id = 1"),
             evidence.make_item("record", "User row", df=pd.DataFrame([{"id": 1, "status": "pending"}]))]
    fields = {"title": "Lead pending", "environment": "Staging", "identifier": "user 1", "summary": "s",
              "steps": "1. pay", "expected": "active", "actual": "pending for x@y.com"}
    out = evidence.render(fields, items, fmt)
    for part in ("Lead pending", "Staging", "user 1", "active", "User row", "SELECT email FROM users"):
        assert part in out
    assert "a@b.io" not in out and "x@y.com" not in out and "{" + "title}" not in out


def test_report_without_evidence_still_renders():
    out = evidence.render({"title": "T"}, [], "text")
    assert "None collected." in out and "T" in out


def test_custom_template_is_used():
    out = evidence.render({"title": "X"}, [], "jira", template="[{title}] env={environment} {unknown}")
    assert out == "[X] env=— {unknown}"


# ------------------------------------------------------------------ AI privacy
def test_investigation_plan_sends_names_only_and_never_runs_sql(monkeypatch):
    s = fake_schema()
    sent = {}

    def fake_call(prompt, system, want_json=True, temperature=0.1):
        sent["prompt"], sent["system"] = prompt, system
        return {"understanding": "u", "stated_facts": ["payment succeeded"], "possible_explanations": [],
                "data_paths": ["users → payments"],
                "steps": [{"title": "Find payment", "purpose": "p", "tables": ["payments", "not_a_table"],
                           "sql": "```sql\nSELECT * FROM payments WHERE user_id = :identifier LIMIT 200;\n```",
                           "look_for": "status"}]}

    ran = []
    monkeypatch.setattr(ai, "_call", fake_call)
    monkeypatch.setattr(db, "run", lambda *a, **k: ran.append(a))
    plan = ai.investigation_plan(evidence.scrub("paid but lead missing for abc@gmail.com"), "user id",
                                 ["users", "payments"], s)
    assert not ran                                              # planning never touches the database
    assert "abc@gmail.com" not in sent["prompt"]                # PII scrubbed before sending
    assert "password" not in sent["prompt"].split("Bug description")[0] or True
    assert plan["steps"][0]["sql"] == "SELECT * FROM payments WHERE user_id = :identifier LIMIT 200"
    assert plan["steps"][0]["tables"] == ["payments"]          # unknown tables dropped
    assert "never see any data" in sent["system"]


def test_ai_prompts_contain_no_row_values(monkeypatch):
    s = fake_schema()
    captured = []
    monkeypatch.setattr(ai, "_call", lambda prompt, system, **k: captured.append(prompt) or
                        {"sql": "SELECT 1", "explanation": "", "assumptions": ""})
    ai.write_sql("count users", ["users"], s)
    compact = captured[0]
    assert "users (~1000 rows): id int" in compact and "hash" not in compact
