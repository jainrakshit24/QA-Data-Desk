# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Edge cases for the no-SQL filter builder and query display — the code that turns clicks into SQL."""
import os
import sys

import pymysql
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import ui  # noqa: E402
from views.explorer import build_where  # noqa: E402

TYPES = {"id": "int", "name": "varchar(255)", "created": "datetime", "note": "text"}


def f(col, op, val=""):
    return {"uid": "x", "col": col, "op": op, "val": val}


def test_no_filters_gives_no_where():
    assert build_where([], TYPES) == ("", [])


def test_values_are_parameters_never_pasted_into_sql():
    hostile = "1'; DROP TABLE users; --"
    where, params = build_where([f("name", "equals", hostile)], TYPES)
    assert where == " WHERE `name` = %s"
    assert params == [hostile]
    assert "DROP" not in where


def test_contains_starts_ends_wrap_value_with_wildcards():
    where, params = build_where([f("name", "contains", "ab"), f("name", "starts with", "cd"),
                                 f("name", "ends with", "ef")], TYPES)
    assert where == " WHERE `name` LIKE %s AND `name` LIKE %s AND `name` LIKE %s"
    assert params == ["%ab%", "cd%", "%ef"]


def test_is_empty_on_numbers_does_not_match_zero():
    # In MySQL an int column compared with '' matches 0, so numbers must use IS NULL only.
    assert build_where([f("id", "is empty")], TYPES) == (" WHERE `id` IS NULL", [])
    assert build_where([f("name", "is empty")], TYPES) == (" WHERE (`name` IS NULL OR `name` = '')", [])
    assert build_where([f("note", "is not empty")], TYPES) == (" WHERE (`note` IS NOT NULL AND `note` <> '')", [])


def test_not_equal_keeps_null_rows():
    assert build_where([f("name", "not equal", "x")], TYPES) == (" WHERE (`name` <> %s OR `name` IS NULL)", ["x"])


@pytest.mark.parametrize("val, expected", [
    ("2026-09-01, 2026-09-30", (" WHERE `created` BETWEEN %s AND %s", ["2026-09-01", "2026-09-30"])),
    ("2026-09-01", ("", [])),                       # needs exactly two values
    ("a, b, c", ("", [])),
])
def test_between_needs_exactly_two_values(val, expected):
    assert build_where([f("created", "between", val)], TYPES) == expected


def test_one_of_builds_one_placeholder_per_value_and_skips_blanks():
    where, params = build_where([f("id", "one of", "101, 102,, 103 ")], TYPES)
    assert where == " WHERE `id` IN (%s, %s, %s)"
    assert params == ["101", "102", "103"]


def test_comparisons():
    where, params = build_where([f("id", "greater than", "5"), f("id", "at most", "9")], TYPES)
    assert where == " WHERE `id` > %s AND `id` <= %s"
    assert params == ["5", "9"]


def test_blank_values_and_unknown_columns_or_operators_are_ignored():
    filters = [f("name", "equals", "   "), f("password", "equals", "x"), f("id", "DROP", "1")]
    assert build_where(filters, TYPES) == ("", [])


def test_unicode_value_is_kept_intact():
    where, params = build_where([f("name", "equals", "दिल्ली café")], TYPES)
    assert params == ["दिल्ली café"]


def test_generated_where_is_accepted_by_the_read_only_guard():
    where, params = build_where([f("name", "contains", "update"), f("id", "one of", "1,2")], TYPES)
    db.check_read_only(f"SELECT `id` FROM `t`{where} LIMIT 10")


def test_display_sql_escapes_quotes_for_copying():
    shown = ui.display_sql("SELECT * FROM t WHERE name = %s", ["O'Brien"])
    assert shown == "SELECT * FROM t WHERE name = 'O\\'Brien'"


def test_display_sql_without_params_is_unchanged():
    assert ui.display_sql("SELECT 1  ") == "SELECT 1"


@pytest.mark.parametrize("err, fragment", [
    (pymysql.err.OperationalError(3024, "max time"), "longer than 60 seconds"),
    (pymysql.err.ProgrammingError(1146, "Table 'db.x' doesn't exist"), "The table `db.x` does not exist"),
    (pymysql.err.OperationalError(1054, "Unknown column 'status_code' in 'field list'"), "The column `status_code` was not found"),
    (pymysql.err.OperationalError(2003, "Can't connect"), "Could not reach the database"),
    (pymysql.err.OperationalError(1792, "READ ONLY"), "only reads data"),
    (db.ReadOnlyError("Only read queries"), "Only read queries"),
])
def test_database_errors_become_plain_messages(err, fragment):
    assert fragment in ui.friendly_error(err)


@pytest.mark.parametrize("sql, added", [
    ("select * from big_table;", True),
    ("WITH c AS (SELECT 1 a) SELECT * FROM c", True),
    ("SELECT a FROM t ORDER BY a DESC", True),
    ("SELECT * FROM (SELECT a FROM t LIMIT 3) x", True),     # inner LIMIT does not bound the outer query
    ("SELECT 'limit 5' AS s", True),                          # a string is not a LIMIT
    ("SELECT a FROM t LIMIT 5", False),
    ("SELECT a FROM t limit 5, 10", False),
    ("SELECT a FROM t LIMIT 5 OFFSET 10;", False),
    ("SHOW TABLES", False),
    ("DESCRIBE t", False),
])
def test_unbounded_selects_get_a_row_limit(sql, added):
    out, was_added = db.apply_row_limit(sql, 1001)
    assert was_added is added
    assert out.rstrip().endswith("LIMIT 1001") is added
    assert not out.endswith(";")


def test_row_limit_result_is_still_read_only_checked():
    out, _ = db.apply_row_limit("select * from t -- trailing comment", 11)
    assert out.endswith("\nLIMIT 11")   # on its own line, so the comment cannot swallow it
    db.check_read_only(out)
