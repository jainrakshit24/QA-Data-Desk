# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Optional Google Gemini helpers.

Only table names, column names, your question and SQL text are sent to Gemini.
Rows from the database are never sent.
"""
import json
import os
import re
import urllib.error
import urllib.request

import packs

HERE = os.path.dirname(os.path.abspath(__file__))
AI_CONFIG = os.path.join(HERE, ".ai.json")
DEFAULT_MODEL = "gemini-flash-lite-latest"   # fast (1–7s) and accurate enough for SQL
# Tried in order when the chosen model is overloaded (503) or rate-limited (429).
FALLBACK_MODELS = ["gemini-3.5-flash"]
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AIError(RuntimeError):
    pass


def settings():
    key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", "")
    if os.path.exists(AI_CONFIG):
        with open(AI_CONFIG) as f:
            saved = json.load(f)
        key = key or saved.get("api_key", "")
        model = model or saved.get("model", "")
    return {"api_key": key, "model": model or DEFAULT_MODEL}


def configured():
    return bool(settings()["api_key"])


def save_settings(api_key, model):
    current = settings()
    data = {"api_key": api_key.strip() or current["api_key"], "model": model.strip() or DEFAULT_MODEL}
    with open(AI_CONFIG, "w") as f:
        json.dump(data, f)
    os.chmod(AI_CONFIG, 0o600)


def clear_key():
    if os.path.exists(AI_CONFIG):
        os.remove(AI_CONFIG)


def domain_notes():
    return packs.ai_notes()


def _call(prompt, system, want_json=True, temperature=0.1):
    cfg = settings()
    if not cfg["api_key"]:
        raise AIError("No Gemini API key is set. An admin can add one on the Admin page.")
    models = [cfg["model"]] + [m for m in FALLBACK_MODELS if m != cfg["model"]]
    last = None
    for model in models:
        try:
            return _call_model(model, cfg["api_key"], prompt, system, want_json, temperature)
        except _Busy as e:
            last = e
    raise AIError(str(last))


class _Busy(AIError):
    """The model is overloaded or rate-limited — worth trying the next one."""


def _call_model(model, api_key, prompt, system, want_json, temperature):
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    req = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if e.code in (401, 403):
            raise AIError(f"Gemini rejected the API key ({e.code}). Check the key on the Admin page. {msg}")
        if e.code == 429:
            raise _Busy("Gemini’s free-tier limit was reached. Wait a minute and try again.")
        if e.code == 503:
            raise _Busy("Gemini is busy right now. Try again in a moment.")
        if e.code == 404:
            raise AIError(f"Model “{model}” is not available for this key. Change the model name on the Admin page.")
        raise AIError(f"Gemini returned an error ({e.code}). {msg}")
    except urllib.error.URLError as e:
        raise AIError(f"Could not reach Gemini: {e.reason}")
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        reason = data.get("promptFeedback", {}).get("blockReason") or "empty response"
        raise AIError(f"Gemini did not return an answer ({reason}).")
    if not want_json:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIError("Gemini’s answer could not be read. Try rephrasing the question.")


# ------------------------------------------------------------------ features
def suggest_tables(question, schema, limit=8):
    """Ask Gemini which tables answer a question. Returns [{'table','why'}]."""
    listing = "\n".join(f"{r.name} (~{r.rows_est} rows)" for r in schema.tables.itertuples())
    system = (
        "You help people find the right tables in a large MySQL database. "
        "Only choose tables from the provided list. Reply as JSON: "
        '{"tables": [{"table": "<exact name>", "why": "<one short sentence>"}]}'
    )
    prompt = (
        f"Domain notes:\n{domain_notes()}\n\n"
        f"Question: {question}\n\n"
        f"Pick up to {limit} tables, most useful first.\n\nAll tables:\n{listing}"
    )
    out = _call(prompt, system)
    items = out.get("tables", out) if isinstance(out, dict) else out
    return [i for i in items if isinstance(i, dict) and i.get("table") in schema.names][:limit]


SQL_RULES = """Rules:
- Write exactly ONE MySQL 8 statement that only reads data: SELECT or WITH … SELECT.
- Never write INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE or SET.
- Use only the tables and columns listed. Wrap identifiers in backticks.
- Never select password, token, secret, OTP or API-key columns.
- Add LIMIT 200 unless the query aggregates to a small result.
- Tables with millions of rows: filter on indexed columns (key PRI/MUL/UNI) where possible.
- Reply as JSON: {"sql": "...", "explanation": "2-3 short sentences in simple English", "assumptions": "anything you guessed, or empty"}"""


def write_sql(question, tables, schema):
    system = "You write safe, read-only MySQL queries.\n" + SQL_RULES
    prompt = (
        f"Domain notes:\n{domain_notes()}\n\n"
        f"Tables available (name: columns; links):\n{schema.compact(tables)}\n\n"
        f"Question: {question}"
    )
    return _normalise(_call(prompt, system))


def fix_sql(question, sql, error, tables, schema):
    system = "You fix read-only MySQL queries.\n" + SQL_RULES
    prompt = (
        f"Domain notes:\n{domain_notes()}\n\n"
        f"Tables available:\n{schema.compact(tables)}\n\n"
        f"Original question: {question or '(not given)'}\n\nQuery:\n{sql}\n\nError from MySQL:\n{error}\n\n"
        "Return a corrected query."
    )
    return _normalise(_call(prompt, system))


def explain_sql(sql, schema, tables=None):
    system = ("Explain SQL queries to a non-expert in simple English. Short bullet points: "
              "what it returns, which tables, what the filters mean. Plain text, no JSON.")
    ctx = schema.compact(tables) if tables else ""
    return _call(f"Tables:\n{ctx}\n\nQuery:\n{sql}", system, want_json=False, temperature=0.2)


def _normalise(out):
    if not isinstance(out, dict) or not out.get("sql"):
        raise AIError("Gemini did not return a query. Try rephrasing the question.")
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", out["sql"].strip()).strip().rstrip(";")
    return {"sql": sql, "explanation": out.get("explanation", ""), "assumptions": out.get("assumptions", "")}


def tables_in_sql(sql, schema):
    words = set(re.findall(r"`?([A-Za-z0-9_]+)`?", sql or ""))
    return [w for w in words if w in schema.names]


# ------------------------------------------------------------------ investigation assistant
PLAN_SYSTEM = """You are a QA investigation assistant for a MySQL database. A QA engineer describes a bug.
You help them plan how to confirm it with read-only queries. You never see any data — only table and
column names — so you must not state anything about the actual data.

Reply as JSON with exactly these keys:
{
  "understanding": "one or two sentences restating the problem",
  "stated_facts": ["only things the QA explicitly said — never add your own"],
  "possible_explanations": [{"explanation": "...", "how_to_confirm": "which step confirms or rules it out"}],
  "data_paths": ["table_a → table_b → table_c"],
  "steps": [{"title": "short", "purpose": "what this checks", "tables": ["..."],
             "sql": "one read-only MySQL SELECT", "look_for": "what result means what"}]
}

Rules for every SQL:
- Exactly one SELECT (or WITH … SELECT). Never INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, SET.
- Use only the tables and columns listed. Backtick identifiers.
- Where the identifier the QA gave belongs, write the named parameter :identifier — never a literal value.
- Never select password, token, secret, OTP, API-key, Aadhaar or PAN columns.
- Add LIMIT 200 unless the query aggregates.
- Prefer indexed columns (key PRI/UNI/MUL) on large tables.
Give 3 to 7 steps, ordered so each one builds on the previous."""


def investigation_plan(issue, identifier_hint, tables, schema):
    """issue text is scrubbed of emails/phones/secrets by the caller; the identifier VALUE is never sent."""
    prompt = (
        f"Domain notes:\n{domain_notes()}\n\n"
        f"Tables available (name: columns; links):\n{schema.compact(tables)}\n\n"
        f"Bug description: {issue}\n"
        f"Identifier provided: {identifier_hint or 'none'} (use :identifier in SQL, its value is bound locally)"
    )
    out = _call(prompt, PLAN_SYSTEM)
    if not isinstance(out, dict) or not isinstance(out.get("steps"), list):
        raise AIError("Gemini did not return an investigation plan. Try describing the issue differently.")
    steps = []
    for st_ in out["steps"][:10]:
        if not isinstance(st_, dict) or not st_.get("sql"):
            continue
        sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", str(st_["sql"]).strip()).strip().rstrip(";")
        steps.append({"title": str(st_.get("title") or "Step"), "purpose": str(st_.get("purpose") or ""),
                      "tables": [t for t in st_.get("tables") or [] if t in schema.names],
                      "sql": sql, "look_for": str(st_.get("look_for") or "")})
    return {
        "understanding": str(out.get("understanding") or ""),
        "stated_facts": [str(x) for x in out.get("stated_facts") or []][:10],
        "possible_explanations": [e for e in out.get("possible_explanations") or [] if isinstance(e, dict)][:8],
        "data_paths": [str(x) for x in out.get("data_paths") or []][:8],
        "steps": steps,
    }


TEST_DATA_SYSTEM = """You are a QA test-design assistant for a MySQL-backed product. You only see table and column
names and types — never data. Suggest test scenarios and example rows for the goal the QA gives.

Reply as JSON:
{"scenarios": [{"name": "short", "why": "what it tests", "row": {"<column>": "<example value>"},
                "find_sql": "one read-only SELECT that finds an existing row already in this state, or empty"}]}

Rules:
- Use only the listed columns. Use obviously fake values (example.com emails, 99999xxxxx phones, 'QA Test').
- Never include real-looking personal data, passwords, tokens or API keys.
- find_sql: exactly one SELECT, backticked identifiers, LIMIT 20. Never INSERT, UPDATE, DELETE or DDL.
- At most 8 scenarios."""


def test_data_ideas(goal, table, schema):
    """Scenario ideas + example rows. Nothing is executed; find_sql that is not read-only is dropped."""
    import db
    prompt = (f"Domain notes:\n{domain_notes()}\n\nTable:\n{schema.compact([table])}\n\nWhat the QA wants to test: {goal}")
    out = _call(prompt, TEST_DATA_SYSTEM, temperature=0.3)
    items = out.get("scenarios") if isinstance(out, dict) else None
    if not isinstance(items, list):
        raise AIError("Gemini did not return test scenarios. Try describing the goal differently.")
    cols = set(schema.cols_by_table.get(table, []))
    scenarios = []
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        row = {k: v for k, v in (it.get("row") or {}).items() if k in cols} if isinstance(it.get("row"), dict) else {}
        sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", str(it.get("find_sql") or "").strip()).strip().rstrip(";")
        if sql:
            try:
                db.check_read_only(sql)
            except Exception:
                sql = ""
        scenarios.append({"name": str(it.get("name") or "Scenario"), "why": str(it.get("why") or ""), "row": row,
                          "find_sql": sql})
    return scenarios
