# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""QA check definitions, evaluation, alert rules and change detection. No UI, no database access."""
import re

SEVERITIES = ["critical", "high", "medium", "low", "info"]
SEVERITY_MARK = {"critical": "🟥 Critical", "high": "🟧 High", "medium": "🟨 Medium", "low": "🟦 Low", "info": "⬜ Info"}
STATUS_MARK = {"failed": "❌ Failed", "passed": "✅ Passed", "info": "🔵 Info", "error": "⚠️ Error",
               "outdated": "🟠 Outdated", "not run": "⚪ Not run"}
CATEGORIES = ["Duplicates", "Orphans", "Missing data", "Invalid status", "Data mismatch", "API/DB consistency",
              "Performance", "Overview", "Other"]


def normalise(check):
    """Fill defaults so every check has the same shape."""
    c = dict(check)
    c.setdefault("title", c.get("id", "Untitled check"))
    c.setdefault("area", "Other")
    c.setdefault("expect", "empty")
    c["expect"] = str(c["expect"]).strip().lower()
    default_sev = "info" if c["expect"] == "info" else "medium"
    sev = str(c.get("severity") or default_sev).lower()
    c["severity"] = sev if sev in SEVERITIES else default_sev
    tags = c.get("tags") or []
    c["tags"] = [str(t).strip().lower() for t in (tags if isinstance(tags, list) else str(tags).split(",")) if str(t).strip()]
    c.setdefault("owner", "")
    c.setdefault("category", c.get("area") or "Other")
    c["alerts"] = [str(a).strip().lower() for a in (c.get("alerts") or [])]
    return c


def expectation_text(check):
    e = check["expect"]
    if e == "empty":
        return "0 rows expected"
    if e == "info":
        return "Information only"
    m = re.fullmatch(r"(at most|at least|exactly)\s+(\d+)(\s+rows?)?", e)
    if m:
        return f"{m.group(1)} {m.group(2)} rows expected"
    return f"Unrecognised expectation “{e}” — treated as information"


def evaluate(check, result):
    """status for one run: passed | failed | info | error | outdated | not run."""
    if not result:
        return "not run"
    if result.get("stale_schema"):
        return "outdated"
    if result.get("error"):
        return "error"
    n = result_rows(result)
    e = check["expect"]
    if e == "empty":
        return "passed" if n == 0 else "failed"
    m = re.fullmatch(r"(at most|at least|exactly)\s+(\d+)(\s+rows?)?", e)
    if m:
        limit = int(m.group(2))
        ok = {"at most": n <= limit, "at least": n >= limit, "exactly": n == limit}[m.group(1)]
        return "passed" if ok else "failed"
    return "info"


def result_rows(result):
    df = result.get("df") if result else None
    return 0 if df is None else len(df)


def change(latest_rows, previous_rows):
    """+14 / -3 / 0 / None. Counts only — individual rows are not claimed to be new."""
    if latest_rows is None or previous_rows is None:
        return None
    return int(latest_rows) - int(previous_rows)


def change_text(delta):
    if delta is None:
        return ""
    if delta > 0:
        return f"+{delta} since last run"
    if delta < 0:
        return f"{delta} since last run"
    return "no change"


ALERT_HELP = {
    "rows > N": "result has more than N rows",
    "rows increased": "result count went up since the previous run",
    "query failed": "the check errored or is outdated",
    "slower than N s": "the check took longer than N seconds",
}


def alerts(check, status, rows, seconds, previous_rows):
    """Which of the check's alert rules fire. Rules: 'rows > 10', 'rows increased', 'query failed', 'slower than 5s'."""
    fired = []
    for rule in check.get("alerts") or []:
        m = re.fullmatch(r"rows\s*>\s*(\d+)", rule)
        if m and rows is not None and rows > int(m.group(1)):
            fired.append(f"{rows} rows > {m.group(1)}")
        elif rule == "rows increased" and rows is not None and previous_rows is not None and rows > previous_rows:
            fired.append(f"rows increased {previous_rows} → {rows}")
        elif rule == "query failed" and status in ("error", "outdated"):
            fired.append("query failed")
        else:
            m = re.fullmatch(r"slower than\s*(\d+(?:\.\d+)?)\s*s(?:econds?)?", rule)
            if m and seconds is not None and seconds > float(m.group(1)):
                fired.append(f"took {seconds:.1f}s > {m.group(1)}s")
    return fired


def filter_checks(checks, database=None, severities=None, tags=None, area=None, term=""):
    out = []
    for c in checks:
        if c.get("database") not in (None, database):
            continue
        if severities and c["severity"] not in severities:
            continue
        if tags and not set(tags) & set(c["tags"]):
            continue
        if area and area != "All areas" and c.get("area") != area:
            continue
        if term and term.lower() not in " ".join([c["title"], c.get("why", ""), c.get("sql", ""),
                                                  " ".join(c["tags"]), c.get("owner", "")]).lower():
            continue
        out.append(c)
    return out
