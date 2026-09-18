# Development

## Getting set up

```bash
python3 -m pip install --user -r requirements-dev.txt
cp .env.example .env          # optional — you can add databases in the app instead
./run.sh
```

The server runs with Streamlit's file watcher **off** on purpose (a 1,300-table schema makes reload storms
expensive), so **restart it after editing code**. To stop it without killing your own shell:

```bash
for pid in $(ps -eo pid,args | grep "[s]treamlit run app.py" | awk '{print $1}'); do kill $pid; done
```

Never `pkill -f "streamlit run app.py"` — the pattern matches the shell running it, and the shell kills itself.

## Layout

```
app.py              sign-in gate, page registry, sidebar
views/              one file per page: layout and interaction only
core/               logic with no Streamlit import — this is what tests target
db.py               connections, pool, read-only guard, row limits
privacy.py          secret/personal column rules, masking, log scrubbing
schema.py           table and column cache, link detection, search
auth.py store.py    SQLite: accounts, saved queries, log / check runs, rules, snapshots
ui.py               run a query, show results, errors, evidence, session-state helpers
packs.py            merges config/ (published) with local/ (yours)
ai.py               Gemini calls — names and SQL text only
scheduler.py        headless check runner for cron
config/             generic checks, charts, links, playbooks, AI notes, report templates
tests/              pytest suite
.project_memory/    internal working notes and browser e2e scripts (git-ignored)
```

Two rules keep this honest:

1. **`core/` must not import `streamlit`.** If logic needs a widget, the widget belongs in `views/`.
2. **`views/` must not call `db.run` directly.** Go through `ui.run_query` / `ui.run_many` so the guard, privacy,
   row limits, logging and friendly errors all apply.

## Tests

```bash
python3 -m pytest -q                      # random order (pytest-randomly)
python3 -m pytest -q -p no:randomly       # fixed order while debugging
python3 -m pytest -q -k "not LiveDatabase" --ignore tests/test_pages.py   # no database needed
```

| File | Covers |
|---|---|
| `test_core.py` | guard, privacy, auth, schema, config packs, plus live-database checks |
| `test_security.py` | 81+ attempts to get a write or a secret column past the guard |
| `test_sql_builder.py` | click-built filters, parameter binding, automatic `LIMIT` |
| `test_investigation.py` | identifier search, records, timeline, API compare, evidence, AI boundaries |
| `test_phase2.py` | checks, dashboard, consistency, playbooks, schema compare, storage |
| `test_phase3.py` | query library, privacy settings, errors, search, edge cases, test data, graph, health, snapshots, env compare, scheduler, `.env` |
| `test_pages.py` | every page rendered with Streamlit's `AppTest`, plus real flows |

Tests that need a database skip themselves when none is configured. If one is configured but unreachable they
**fail** with error 2003 — that is the environment, not a regression.

Useful patterns already in the suite:

- `tests/conftest.py` points `QA_LOCAL_DIR` at a temp folder, so tests never read your real `local/` pack.
- `test_phase3.py::_offline_page` renders a page with a fake schema and no database at all. It patches `ui` inside
  the AppTest script, so it is paired with the `offline_ui` fixture that restores the module afterwards —
  **AppTest runs the page script in the same process**, and an unrestored patch breaks unrelated tests in random
  order.
- `fake_schema()` in `test_investigation.py` builds a `Schema` from a dict; use it instead of touching a database.

## Browser tests

Playwright drives real Chrome against a **separate** test server, so your accounts are never touched:

```bash
QA_APP_DB=/tmp/qa_e2e/app.sqlite3 QA_SETUP_CODE=E2ESETUP \
  python3 -m streamlit run app.py --server.port 8599 --server.headless true
E2E_OUT=/tmp/qa_e2e/shots python3 -u .project_memory/e2e/phase3_run.py
```

Screenshots are written after every step — read them, because a step can pass while the screen shows something
wrong. Scripts live in `.project_memory/e2e/` (git-ignored; they contain staging-specific values).

Streamlit + Playwright gotchas that cost hours once:

- A selectbox **with a value** reports its accessible name as `Selected X. <label>` — match the end of the label,
  not the exact string.
- Filling a text input does not rerun the app: press Enter or blur, or the button you want stays disabled.
- Radio options have no accessible name — click their label text. Segmented controls are
  `[data-testid="stButtonGroup"] button`.
- An expander does **not** keep its open state across a rerun. Compute `expanded=` from session state, or the form
  collapses while the user is typing.
- After a failed step press Escape: an open dropdown overlays the page and every later click times out.
- `st.dataframe` draws on a canvas — you cannot read cell text; assert on captions, metrics or session state.

## Adding a page

1. Write the logic in `core/<thing>.py` with no Streamlit import, and unit-test it.
2. Write `views/<thing>.py` with a `render()` function.
3. Register it in `app.py`: add to the `pages` dict and to a `nav` group.
4. Add it to the parametrised render test in `tests/test_pages.py`.
5. Use `ui.require_connection()` at the top if it needs a database, and `ui.run_query` for every query.

## Adding a check, chart or link

Nothing to code — see [Configuration](configuration.md). Company-specific items go in `local/`, never in `config/`.

## House style

- Messages on screen are written for a QA engineer, not a DBA: say what happened, why, and what to try.
- Prefer a real fix over a workaround, and say so in the commit message.
- Keep company names, hostnames and credentials out of the repository. `.env`, `.db.json`, `local/` exist for that.
- Before a pull request: `python3 -m pytest -q`, and mention what you ran in the description.

---

© 2026 Rakshit Jain · MIT licensed.
