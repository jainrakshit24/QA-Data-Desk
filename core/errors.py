"""Turn database and guard errors into plain explanations: what happened, likely reasons, what to try."""
import re

GENERIC_TRY = ["Check the SQL used (shown next to the result)."]


def explain(code, message):
    """Returns dict: message, reasons, tries. `code` is the MySQL error number or a label."""
    msg = str(message or "")
    m = lambda pattern: (re.search(pattern, msg) or [None, None])[1]  # noqa: E731

    if code == 1054:
        col = m(r"Unknown column '([^']+)'")
        return _e(f"The column `{col}` was not found." if col else "A column was not found.",
                  ["The column name is misspelled or was renamed", "The wrong table (or alias) is used",
                   "The column was removed from the database, and the saved table list is out of date"],
                  ["Check the column list on Find data → Columns", "Admin → Databases → Refresh table list"])
    if code == 1146:
        tbl = m(r"Table '([^']+)'")
        return _e(f"The table `{tbl}` does not exist." if tbl else "A table does not exist.",
                  ["The table name is misspelled", "It was dropped or renamed", "You are connected to a different database"],
                  ["Search for it on Find data", "Check the Database selector in the sidebar",
                   "Schema compare → compare with an older snapshot to see if it was removed"])
    if code == 1064:
        near = m(r"near '([^']{0,60})")
        return _e("The SQL has a syntax error" + (f" near “{near.strip()}”." if near else "."),
                  ["A missing comma, bracket or quote", "A keyword used as a name without backticks",
                   "A feature from another database (e.g. TOP instead of LIMIT)"],
                  ["Look just before the quoted text", "Wrap unusual names in `backticks`",
                   "Ask AI → “Explain simply” or “Ask AI to fix this error”"])
    if code == 3024:
        return _e("The query ran longer than 60 seconds and was stopped.",
                  ["It scans a very large table", "The filter uses a column without an index",
                   "A function wraps a filtered column (e.g. DATE(created))"],
                  ["Filter on a column marked 🔑", "Narrow a date range", "SQL editor → Analyze to see what is slow",
                   "Add a LIMIT"])
    if code in (2002, 2003, 2005, 2006, 2013):
        return _e("Could not reach the database.",
                  ["VPN is disconnected or your IP is not allowed", "The database server is down or restarting",
                   "The network dropped the connection"],
                  ["Check VPN / network access", "Admin → Databases → Test the connection", "Try again in a minute"])
    if code in (1044, 1045, 1142, 1143):
        return _e("The database user is not allowed to do this.",
                  ["The configured user has no access to that table or database", "The password changed"],
                  ["Ask an admin to check the connection on Admin → Databases"])
    if code == 1792:
        return _e("The database refused a write. This app only reads data.", ["The query tried to change data"],
                  ["Rewrite it as a SELECT"])
    if code == 1242:
        return _e("A subquery returned more than one row where only one is allowed.",
                  ["`= (SELECT …)` matched several rows"], ["Use IN (SELECT …) or add LIMIT 1 to the subquery"])
    if code == 1052:
        col = m(r"Column '([^']+)'")
        return _e(f"The column `{col}` is ambiguous." if col else "A column name is ambiguous.",
                  ["Two joined tables both have a column with that name"], ["Prefix it with the table alias, e.g. u.id"])
    if code in (1055, 1140):
        return _e("GROUP BY is incomplete.", ["A selected column is neither grouped nor aggregated"],
                  ["Add the column to GROUP BY, or wrap it in MIN() / MAX() / ANY_VALUE()"])
    if code in (1267, 1270, 1271):
        return _e("Text with different collations was compared.", ["Two columns use different character sets"],
                  ["Compare with COLLATE utf8mb4_general_ci on one side"])
    if code == "read_only":
        return _e(msg, ["This app only runs read queries: SELECT, SHOW, DESCRIBE, EXPLAIN and WITH"],
                  ["Remove the part that changes data or locks rows"])
    if code == "privacy":
        return _e(msg, ["Secret fields (passwords, tokens, OTPs, keys, Aadhaar/PAN…) are never shown"],
                  ["Remove that column from the query"])
    return _e(f"MySQL error {code} — {msg}" if isinstance(code, int) else msg, [], GENERIC_TRY)


def _e(message, reasons, tries):
    return {"message": message, "reasons": reasons, "tries": tries}
