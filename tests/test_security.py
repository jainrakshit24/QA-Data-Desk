# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Bypass attempts against the query guard and privacy checks. Every one of these must hold."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import privacy  # noqa: E402

ALLOWED = [
    "SELECT 1",
    "select/**/1",
    "  \n\tSELECT 1",
    "(SELECT 1)",
    "SELECT 1 UNION SELECT 2",
    "SELECT * FROM t WHERE id IN (SELECT user_id FROM u)",
    "WITH x AS (SELECT 1 a) SELECT * FROM x",
    "SHOW TABLES",
    "SHOW COLUMNS FROM users",
    "DESCRIBE users",
    "DESC users",
    "EXPLAIN SELECT * FROM users WHERE uid = 1",
    "TABLE users",
    "SELECT * FROM `delete`",                            # a table that happens to be called delete
    "SELECT 'drop table x; update' AS note",              # dangerous words inside a string
    "SELECT \"delete\" AS word",
    "SELECT created, updated, deleted_at FROM t",         # words that only contain a keyword
    "SELECT /*+ MAX_EXECUTION_TIME(1000) */ 1",           # optimizer hints are fine
    "SELECT 1 -- delete everything",
    "SELECT 1 # update",
]

BLOCKED = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "ALTER TABLE t ADD c INT",
    "TRUNCATE t",
    "CREATE TABLE t (a INT)",
    "REPLACE INTO t VALUES (1)",
    "GRANT ALL ON *.* TO x",
    "REVOKE ALL ON *.* FROM x",
    "RENAME TABLE a TO b",
    "SET GLOBAL max_connections = 1",
    "CALL some_procedure()",
    "LOAD DATA INFILE '/tmp/x' INTO TABLE t",
    "HANDLER t OPEN",
    "DO SLEEP(1)",
    "SeLeCt 1; dElEtE FROM t",
    "SELECT 1;\nDROP TABLE t",
    "SELECT 1 ; SELECT 2",
    "WITH x AS (SELECT 1) UPDATE t SET a = 1",
    "WITH x AS (SELECT 1) DELETE FROM t",
    "SELECT * FROM t WHERE id IN (SELECT 1) ; DELETE FROM t",
    "/* SELECT */ DELETE FROM t",
    "-- SELECT\nDELETE FROM t",
    "SELECT 1 INTO OUTFILE '/tmp/x'",
    "SELECT 1 INTO DUMPFILE '/tmp/x'",
    "SELECT 1 INTO @v",
    "SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */",          # MySQL executes versioned comments
    "SELECT /*! 1 */",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT load_file ('/etc/passwd')",
    "SELECT GET_LOCK('x', 10)",
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t FOR SHARE",
    "SELECT * FROM t LOCK IN SHARE MODE",
    "select * from t for\n update",
    "",
    "   ",
    ";",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_read_queries_are_allowed(sql):
    db.check_read_only(sql)


@pytest.mark.parametrize("sql", BLOCKED)
def test_writes_and_bypass_attempts_are_blocked(sql):
    with pytest.raises(db.ReadOnlyError):
        db.check_read_only(sql)


def test_auto_limit_never_turns_a_blocked_query_into_an_allowed_one():
    for sql in BLOCKED:
        out, _ = db.apply_row_limit(sql, 10)
        with pytest.raises(db.ReadOnlyError):
            db.check_read_only(out)


@pytest.mark.parametrize("sql", [
    "SELECT password FROM users",
    "SELECT u.passwd FROM users u",
    "SELECT api_key, secret FROM keys",
    "SELECT access_token FROM sessions",
    "SELECT otp FROM user_otp_log",
    "SELECT aadhaar FROM kyc",
    "SELECT pan_number FROM kyc",
    "SELECT authorization FROM request_log",
])
def test_secret_columns_cannot_be_named(sql):
    with pytest.raises(privacy.PrivacyError):
        privacy.check_sql(sql)


@pytest.mark.parametrize("col", ["password", "user_password", "api_key", "refresh_token", "otp",
                                 "aadhaar_no", "pan", "pan_card", "authorization"])
def test_secret_columns_are_hidden_in_results(col):
    import pandas as pd
    out = privacy.mask(pd.DataFrame({col: ["value"]}), is_admin=True)
    assert out[col].iloc[0] == privacy.HIDDEN


@pytest.mark.parametrize("col", ["company", "japan_office", "expand", "tokenizer_version_note"])
def test_ordinary_columns_are_not_mistaken_for_secrets(col):
    assert not privacy.is_secret_column(col)
