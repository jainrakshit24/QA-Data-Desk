"""Edge case ideas for a table, worked out from column types, names, keys and links.

Suggestions only: nothing here writes to the database. The optional coverage query only counts things
(NULLs, empty strings, longest value, date range) in a sample of recent rows, so personal values never show.
"""
import re

import privacy

CATEGORIES = ["Empty / NULL", "Length", "Range", "Format", "Dates", "Choices", "Uniqueness", "Links", "Across columns"]

INT_RANGE = {"tinyint": 8, "smallint": 16, "mediumint": 24, "int": 32, "integer": 32, "bigint": 64}


def parse_type(col_type):
    """'varchar(255)' → {'base': 'varchar', 'length': 255, ...}; enum/set values; decimal precision; unsigned."""
    t = str(col_type or "").strip().lower()
    base = re.match(r"[a-z]+", t)
    info = {"base": base.group(0) if base else t, "unsigned": "unsigned" in t, "length": None,
            "precision": None, "scale": None, "values": []}
    m = re.match(r"[a-z]+\((\d+)(?:,(\d+))?\)", t)
    if m:
        if info["base"] in ("decimal", "numeric", "float", "double"):
            info["precision"], info["scale"] = int(m.group(1)), int(m.group(2) or 0)
        else:
            info["length"] = int(m.group(1))
    if info["base"] in ("enum", "set"):
        info["values"] = [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'", str(col_type))]
    return info


def kind_of(info):
    b = info["base"]
    if b == "tinyint" and info["length"] == 1 or b in ("bool", "boolean", "bit"):
        return "boolean"
    if b in INT_RANGE:
        return "integer"
    if b in ("decimal", "numeric", "float", "double", "real"):
        return "decimal"
    if b in ("date", "datetime", "timestamp", "time", "year"):
        return "date"
    if b in ("enum", "set"):
        return "choice"
    if b == "json":
        return "json"
    if b in ("char", "varchar", "tinytext", "text", "mediumtext", "longtext"):
        return "text"
    if "blob" in b or "binary" in b:
        return "binary"
    return "other"


TEXT_MAX = {"tinytext": 255, "text": 65535, "mediumtext": 16777215, "longtext": 4294967295}


def int_limits(info):
    bits = INT_RANGE.get(info["base"], 32)
    if info["unsigned"]:
        return 0, 2 ** bits - 1
    return -(2 ** (bits - 1)), 2 ** (bits - 1) - 1


NAME_RULES = [
    (r"e_?mail", "Format", [
        ("Missing @", "user.example.com", "Should be rejected with a clear message."),
        ("Upper case", "User.Name@Example.COM", "Login / duplicate checks should treat it the same as lower case."),
        ("Plus alias", "user+test@example.com", "Valid address; often wrongly rejected."),
        ("Spaces around", "  user@example.com ", "Should be trimmed before saving and comparing."),
        ("Two @", "a@@example.com", "Should be rejected."),
    ]),
    (r"(^|_)(phone|mobile|contact|msisdn|whatsapp)", "Format", [
        ("9 digits", "987654321", "Too short — should be rejected."),
        ("11 digits", "98765432101", "Too long — should be rejected."),
        ("With country code", "+919876543210", "Decide whether +91 is stripped, kept or rejected; be consistent."),
        ("Leading zero", "09876543210", "Common user input; check how it is stored and searched."),
        ("Spaces / dashes", "98765 43210", "Should be normalised or rejected, not saved as-is."),
        ("Letters", "98765abcde", "Should be rejected."),
    ]),
    (r"(^|_)(url|link|website|redirect|href)", "Format", [
        ("No scheme", "www.example.com", "Decide whether https:// is added."),
        ("javascript: link", "javascript:alert(1)", "Must be rejected — script injection."),
        ("Very long URL", "https://example.com/" + "a" * 2000, "Check truncation."),
    ]),
    (r"(^|_)(pin_?code|zip|postal)", "Format", [
        ("5 digits", "11000", "Indian PIN codes have 6 digits."),
        ("Starts with 0", "012345", "No Indian PIN starts with 0; also check it is not stored as a number."),
    ]),
    (r"(price|amount|fee|cost|salary|total|balance|discount)", "Range", [
        ("Zero", "0", "Free / zero-amount path."),
        ("Negative", "-1", "Should normally be rejected."),
        ("Many decimals", "10.999", "Check rounding."),
    ]),
    (r"(percent|percentage|ratio|score)", "Range", [
        ("Exactly 0 and 100", "0 / 100", "Boundaries."),
        ("Over 100", "100.01", "Should be rejected for percentages."),
    ]),
    (r"(^|_)(age|year|yr)($|_)", "Range", [
        ("Zero / negative", "0 / -1", "Should be rejected."),
        ("Far future", "2100", "Unrealistic value."),
    ]),
    (r"(^|_)(name|title|first_name|last_name|full_name)$", "Format", [
        ("Unicode", "ज़ोया Ōsaka", "Hindi and accented letters must save and display correctly."),
        ("Apostrophe", "O'Brien", "Quote handling in search and exports."),
        ("HTML", "<b>x</b><script>alert(1)</script>", "Must be escaped wherever it is shown."),
        ("Only spaces", "   ", "Should count as empty."),
    ]),
    (r"(^|_)(slug|code|sku|ref|reference)$", "Format", [
        ("Case difference", "ABC-1 vs abc-1", "Uniqueness and lookups — same or different?"),
        ("Special characters", "a b/c?d", "URL-safe?"),
    ]),
    (r"(^|_)(sort|order|position|priority|rank|sequence)$", "Range", [
        ("Same value twice", "1, 1", "Tie order should be stable."),
        ("Gaps / negative", "1, 5, -1", "Ordering with gaps and negatives."),
    ]),
]

DATE_PAIRS = [("start", "end"), ("from", "to"), ("created", "updated"), ("created", "modified"), ("open", "close"),
              ("valid_from", "valid_to"), ("begin", "finish")]
SOFT_DELETE = re.compile(r"(^|_)(deleted|removed|archived)(_at|_on)?$|^is_(deleted|removed|archived|active)$", re.I)
STATUS = re.compile(r"(^|_)(status|state|stage|is_active|published|is_published)$", re.I)


def _case(column, category, case, example, why):
    return {"column": column, "category": category, "case": case, "example": str(example), "why": why}


def column_cases(table, row, schema=None):
    """Edge cases for one column. row has col, type, nullable, key, default, extra."""
    col = row["col"]
    if privacy.is_secret_column(col):
        return []
    info = parse_type(row["type"])
    kind = kind_of(info)
    extra = str(row.get("extra") or "").lower()
    out = []
    add = lambda cat, case, ex, why: out.append(_case(col, cat, case, ex, why))

    if "auto_increment" in extra:
        add("Range", "Generated id", "(automatic)", "Never set by the app; check nothing assumes ids are continuous.")
        return out
    if "generated" in extra:
        return out
    nullable = str(row.get("nullable")).upper() == "YES"
    if nullable:
        add("Empty / NULL", "NULL", "NULL", "Screens, exports and APIs must not crash or show “None” / “null”.")
    elif row.get("default") is None:
        add("Empty / NULL", "Required, no default", "(left out)", "Creating without it should fail with a clear message, not a 500.")

    if kind == "text":
        max_len = info["length"] or TEXT_MAX.get(info["base"])
        add("Empty / NULL", "Empty string", "''", "Different from NULL — filters for “empty” must catch both.")
        add("Empty / NULL", "Only spaces", "'   '", "Should be trimmed or rejected.")
        if max_len and max_len <= 65535:
            add("Length", f"Exactly {max_len} characters", f"{'x' * min(max_len, 12)}… ({max_len})", "Longest allowed value — must save and display (no cut-off in UI).")
            add("Length", f"{max_len + 1} characters", f"({max_len + 1} chars)", "One too long — MySQL rejects it in strict mode; the app should validate first.")
        add("Format", "Unicode / emoji", "नमस्ते 😀", "utf8 vs utf8mb4 — emoji fail on utf8 columns.")
        add("Format", "Leading / trailing spaces", "' value '", "Search and duplicate checks should ignore them.")
    elif kind == "integer":
        lo, hi = int_limits(info)
        add("Range", "Zero", 0, "Often means “not set” by mistake.")
        add("Range", "Negative", -1, "Rejected by MySQL (unsigned)." if info["unsigned"] else "Usually invalid for ids / counts.")
        add("Range", "Largest value", hi, f"Upper limit of {row['type']}.")
        add("Range", "One above largest", hi + 1, "Out of range — must be validated before the database.")
        if not info["unsigned"]:
            add("Range", "Smallest value", lo, f"Lower limit of {row['type']}.")
        add("Format", "Non-number input", "'12abc'", "API should reject; MySQL may silently cut it to 12 in non-strict mode.")
    elif kind == "decimal":
        p, s = info["precision"], info["scale"]
        if p:
            whole = "9" * (p - s) + ("." + "9" * s if s else "")
            add("Range", "Largest value", whole, f"Limit of {row['type']}.")
            add("Range", "Too many decimals", "1." + "5" * (s + 1), f"Rounded to {s} decimals — check the displayed and charged amount match.")
        add("Range", "Zero and negative", "0 / -0.01", "Boundaries around zero.")
    elif kind == "boolean":
        add("Choices", "Other than 0/1", 2, "tinyint(1) accepts 2..127 — code checking `== 1` vs truthy behaves differently.")
    elif kind == "choice":
        for v in info["values"]:
            add("Choices", f"Value “{v}”", v, "Every allowed value should be handled on every screen and API.")
        add("Choices", "Value not in list", "'unknown'", "Rejected in strict mode, saved as '' otherwise.")
    elif kind == "date":
        b = info["base"]
        if b in ("date", "datetime", "timestamp"):
            add("Dates", "Leap day", "2028-02-29", "Valid date — date maths (+1 year) must not break.")
            add("Dates", "Invalid day", "2027-02-29", "Should be rejected.")
            add("Dates", "Zero date", "0000-00-00", "Legacy rows may contain it; many drivers crash on it.")
            add("Dates", "Month / year end", "2026-12-31 23:59:59", "Reports grouped by month or year.")
            add("Dates", "Timezone boundary", "2026-03-31 18:30:00 (UTC) = 00:00 IST", "Same instant falls on different days in UTC and IST.")
            add("Dates", "Future", "2099-01-01", "Future-dated records in lists and “expired” logic.")
        if b == "timestamp":
            add("Dates", "After 2038", "2038-01-19 03:14:08", "TIMESTAMP stops at 2038-01-19 03:14:07 UTC.")
            add("Dates", "Before 1970", "1969-12-31", "TIMESTAMP cannot store it.")
    elif kind == "json":
        add("Format", "Empty object / array", "{} / []", "Code expecting keys must cope.")
        add("Format", "Invalid JSON", "{bad", "Rejected by a JSON column; allowed in a text column.")
        add("Format", "Missing expected key", '{"other": 1}', "Readers should use defaults, not crash.")

    key = str(row.get("key") or "")
    if key in ("UNI", "PRI") and "auto_increment" not in extra:
        add("Uniqueness", "Duplicate value", "(same as an existing row)", "Should give a friendly “already exists” message, not a 500.")
        if kind == "text":
            add("Uniqueness", "Duplicate with different case / spaces", "'ABC' vs 'abc '", "Depends on collation — decide if these are the same.")

    if schema is not None:
        link = schema.link_for(table, col)
        if link:
            ref_tbl, ref_col, how = link
            add("Links", f"Points to a missing {ref_tbl}", f"{ref_col} that does not exist",
                f"Orphan ({how}) — screens joining {ref_tbl} must not crash or silently hide the row.")
            ref_cols = schema.cols_by_table.get(ref_tbl, [])
            flag = next((c for c in ref_cols if SOFT_DELETE.search(c)), None)
            if flag:
                add("Links", f"Points to a deleted / inactive {ref_tbl}", f"{ref_tbl}.{flag} set",
                    "Should it still be shown, selectable or counted?")

    for pattern, category, ideas in NAME_RULES:
        if re.search(pattern, col, re.I) and kind in ("text", "integer", "decimal", "other"):
            for case, ex, why in ideas:
                add(category, case, ex, why)
            break
    if STATUS.search(col):
        add("Choices", "Every status value", "(see coverage)", "List the values in use and test each one, including old / retired ones.")
    return out


def table_cases(schema, table):
    """Cases that involve more than one column."""
    cols = schema.cols_by_table.get(table, [])
    lower = {c.lower(): c for c in cols}
    out = []
    for a, b in DATE_PAIRS:
        first = next((c for l_, c in lower.items() if re.search(rf"(^|_){a}(_|$)", l_) and re.search(r"(date|_at|_on|time)", l_)), None)
        second = next((c for l_, c in lower.items() if re.search(rf"(^|_){b}(_|$)", l_) and re.search(r"(date|_at|_on|time)", l_)), None)
        if first and second and first != second:
            out.append(_case(f"{first}, {second}", "Across columns", f"{second} before {first}", "end before start",
                             "Should be rejected; lists and durations must not go negative."))
            out.append(_case(f"{first}, {second}", "Across columns", "Same instant", "start = end", "Zero-length period."))
    deleted = [c for c in cols if SOFT_DELETE.search(c)]
    status = [c for c in cols if STATUS.search(c)]
    for d in deleted:
        out.append(_case(d, "Across columns", "Soft-deleted row", f"{d} set",
                         "Must disappear from lists, counts, search and exports — but stay in audit / history."))
        for s_ in status:
            if s_ != d:
                out.append(_case(f"{s_}, {d}", "Across columns", "Active status on a deleted row", f"{s_}=active and {d} set",
                                 "Contradiction — which one wins?"))
    uniques = [c for c in cols if schema.key_of.get((table, c)) == "UNI"]
    if len(uniques) > 1:
        out.append(_case(", ".join(uniques), "Uniqueness", "Two unique fields clash with two different rows", "",
                         "Error message should name the right field."))
    return out


def for_table(schema, table):
    cases = []
    for r in schema.table_columns(table).to_dict("records"):
        cases.extend(column_cases(table, r, schema))
    cases.extend(table_cases(schema, table))
    seen, out = set(), []                      # a name rule can repeat an idea the type already gave
    for c in cases:
        key = (c["column"], c["case"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ------------------------------------------------------------------ what already exists (counts only)
def _q(name):
    if not re.fullmatch(r"[A-Za-z0-9_$]+", name or ""):
        raise ValueError(f"“{name}” is not a valid table or column name.")
    return f"`{name}`"


def coverage_sql(schema, table, sample=5000, max_columns=40):
    """One read-only query: per column, how many of the newest `sample` rows are NULL / empty / at the limit.

    Personal and secret columns get counts only — never MIN / MAX values.
    """
    frame = schema.table_columns(table)
    pk = next((r.col for r in frame.itertuples() if r.key == "PRI"), None)
    parts, used = ["COUNT(*) AS `rows__sampled`"], []
    for r in frame.itertuples():
        if privacy.is_secret_column(r.col) or len(used) >= max_columns:
            continue
        info = parse_type(r.type)
        kind = kind_of(info)
        if kind == "binary":
            continue
        q, n = _q(r.col), r.col
        used.append(n)
        parts.append(f"SUM({q} IS NULL) AS `{n}__nulls`")
        personal = bool(privacy.PHONE_COLUMN.search(n)) or "mail" in n.lower()
        if kind == "text":
            parts.append(f"SUM({q} = '') AS `{n}__empty`")
            parts.append(f"SUM({q} <> TRIM({q})) AS `{n}__spaces`")
            parts.append(f"MAX(CHAR_LENGTH({q})) AS `{n}__longest`")
        elif kind in ("integer", "decimal") and not personal:
            parts.append(f"MIN({q}) AS `{n}__min`")
            parts.append(f"MAX({q}) AS `{n}__max`")
            parts.append(f"SUM({q} = 0) AS `{n}__zero`")
            parts.append(f"SUM({q} < 0) AS `{n}__negative`")
        elif kind == "date" and info["base"] != "time":
            parts.append(f"MIN({q}) AS `{n}__min`")
            parts.append(f"MAX({q}) AS `{n}__max`")
        elif kind in ("choice", "boolean") or STATUS.search(n):
            parts.append(f"COUNT(DISTINCT {q}) AS `{n}__distinct`")
    order = f" ORDER BY {_q(pk)} DESC" if pk else ""
    sql = f"SELECT {', '.join(parts)} FROM (SELECT * FROM {_q(table)}{order} LIMIT {int(sample)}) AS recent"
    return sql, used


def coverage_table(result_row, columns, schema=None, table=None):
    """Turn the single coverage row into one line per column."""
    rows = []
    total = result_row.get("rows__sampled") or 0
    for c in columns:
        get = lambda k: result_row.get(f"{c}__{k}")
        line = {"column": c, "NULL": get("nulls"), "empty ''": get("empty"), "extra spaces": get("spaces"),
                "longest": get("longest"), "zero": get("zero"), "negative": get("negative"),
                "min": get("min"), "max": get("max"), "distinct": get("distinct")}
        if schema is not None and table and line["longest"] is not None:
            info = parse_type(schema.table_columns(table).set_index("col").loc[c, "type"])
            if info["length"]:
                line["limit"] = info["length"]
        rows.append({k: v for k, v in line.items() if v is not None})
    return total, rows
