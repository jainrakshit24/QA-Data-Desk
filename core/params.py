"""Named parameters in SQL — `WHERE id = :user_id` — bound safely, never pasted into the text."""
import re

_TOKEN = re.compile(
    r"'(?:[^'\\]|\\.)*'"          # single-quoted string
    r'|"(?:[^"\\]|\\.)*"'          # double-quoted string
    r"|`[^`]*`"                    # quoted identifier
    r"|/\*.*?\*/"                  # block comment
    r"|(?:--\s|#)[^\n]*"           # line comment
    r"|::"                         # not a parameter
    r"|(?<![\w:]):([A-Za-z_][A-Za-z0-9_]*)"  # :name
    r"|%",                         # literal percent (must be doubled for the driver)
    re.S,
)


def names(sql):
    """Parameter names in order of first appearance, ignoring strings and comments."""
    found = []
    for m in _TOKEN.finditer(sql or ""):
        if m.group(1) and m.group(1) not in found:
            found.append(m.group(1))
    return found


def bind(sql, values):
    """Turn `:name` into driver placeholders. Returns (sql_for_driver, params_list).

    Raises KeyError naming the first parameter without a value.
    """
    params = []

    def swap(m):
        if m.group(1):
            name = m.group(1)
            if name not in values:
                raise KeyError(name)
            params.append(values[name])
            return "%s"
        # every other %, including inside strings and comments, must be doubled for the driver
        return m.group(0).replace("%", "%%")

    out = _TOKEN.sub(swap, sql or "")
    if not params:
        # No placeholders: the driver does not interpret % at all, so keep the original text.
        return sql, []
    return out, params
