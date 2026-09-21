# Configuration

Two folders decide what the app knows about your databases:

| Folder | What it is | Published? |
|---|---|---|
| `config/` | Ships with the project. Must work on **any** MySQL database. | Yes, it is in git |
| `local/` | Yours: checks, charts, links, notes and templates for your own systems. | No, git-ignored |

Both are merged at load time. An item in `local/` with the same `id` **replaces** the one from `config/`, so you can
override a shipped check without editing it. Point `QA_LOCAL_DIR` somewhere else to keep your pack in another repo.

---

## Environment (`.env`)

```bash
cp .env.example .env
```

Real environment variables always win over the file, so a container's settings are never overwritten.

| Variable | Meaning |
|---|---|
| `QA_DB_HOST` `QA_DB_PORT` `QA_DB_USER` `QA_DB_PASSWORD` `QA_DB_NAME` | One connection from the environment. All five must be set for it to appear. |
| `QA_DB_LABEL` | Its name in the sidebar (default `<database> (env)`) |
| `QA_SETUP_CODE` | The one-time code for creating the first admin |
| `QA_APP_DB` | Path to the SQLite file with accounts and history (default `app_data.sqlite3`) |
| `QA_LOCAL_DIR` | Where your pack lives (default `local/`) |
| `GEMINI_API_KEY` `GEMINI_MODEL` | AI helper. Model defaults to `gemini-flash-lite-latest`. |
| `QA_ALERT_WEBHOOK` | Where `scheduler.py --notify webhook` posts |
| `QA_ENV_FILE` | Load a different env file |
| `QA_ORG_NAME` `QA_CONTACT_EMAIL` `QA_APP_URL` `QA_APP_NAME` `QA_LEGAL_UPDATED` | Who operates this installation — used by the Privacy policy, the Terms of use and the sitemap generator |

Connections added in the app go to `.db.json` instead (mode 600, git-ignored). Both can coexist; the environment
one is marked *environment* on the Databases page and is not editable there.

---

## `checks.yaml` — the QA check library

A check is a query plus an expectation. Anything it returns is, by definition, something to look at.

```yaml
- id: paid_without_lead            # unique; a local/ item with the same id overrides config/
  area: Payments                   # groups the check on the page
  title: Successful payment without a lead
  why: Every successful payment should create a lead. Rows here are missing leads.
  severity: high                   # critical | high | medium | low | info
  expect: empty                    # empty | info | at most N | at least N | exactly N
  schedule: daily                  # hourly | daily | weekly | monthly | manual — used by scheduler.py
  tags: [payments, leads]
  owner: payments-team
  alerts:                          # optional; evaluated after every run
    - "rows > 0"
    - "rows increased"
    - "query failed"
    - "slower than 10s"
  database: shop                   # optional: only show when this database is connected
  sql: |
    SELECT p.id, p.created_at
    FROM payments p
    LEFT JOIN leads l ON l.payment_id = p.id
    WHERE p.status = 'SUCCESS' AND l.id IS NULL
    LIMIT 200
```

- `expect: empty` is the normal case — zero rows means healthy.
- `expect: info` never fails; use it for overviews.
- Every run records a **count** (never rows), so the dashboard can show the change since last time.
- Admins can add checks from **Monitor → QA checks**; they are appended to `local/checks.yaml`.

---

## `charts.yaml` — ready-made charts

```yaml
- id: campaigns_per_day            # unique; local/ overrides config/ for the same id
  title: Campaigns created per day
  kind: Line                       # Bar | Line | Pie | Scatter
  x: day                           # a column name in the result
  y: campaigns
  color: status                    # optional: split the series by this column
  note: A sudden drop usually means the create flow is broken.
  database: shop                   # optional: only show for this database
  sql: |
    SELECT DATE(created) AS day, status, COUNT(*) AS campaigns
    FROM campaigns
    WHERE created >= CURDATE() - INTERVAL 30 DAY
    GROUP BY day, status ORDER BY day
```

Results are cached for ten minutes, so switching between charts is instant.

---

## `links.yaml` — relationships the app cannot guess

The app already follows real foreign keys and the `<name>_id → <name>.id` pattern. Use this file for the rest.

```yaml
column_links:                      # this column always points here, in every table
  client_id: [clients, id]
  cid: [campaigns, id]

table_links:                       # only in this one table
  "adbuddy_leads.owner": [users, id]

aliases:                           # what your team calls a table
  campaign: adbuddy_campaigns
  lead: adbuddy_leads

topics:                            # one word that should find several tables
  payments: [payments, refunds, payment_attempts]
```

A configured link is used only when the target table and column actually exist, so a stale entry cannot break a page.

---

## `playbooks.yaml` — saved investigations

```yaml
- id: tpl_duplicates
  name: Duplicate values in a column
  objective: Find values that appear more than once where they should be unique.
  parameters:
    - {name: table,  label: Table, type: table}                    # type: value | table | column
    - {name: column, label: Column that should be unique, type: column, table_param: table}
    - {name: min_count, label: At least how many, type: value, hint: "2"}
  steps:
    - title: Values that repeat
      purpose: Each row is a value that exists more than once.
      sql: |
        SELECT {{column}} AS value, COUNT(*) AS times
        FROM {{table}}
        GROUP BY {{column}} HAVING COUNT(*) >= :min_count
        ORDER BY times DESC LIMIT 200
      look_for: Any row here breaks uniqueness.
```

Two kinds of parameter, and the difference matters:

- `:name` is a **value** — bound by the driver, never pasted into SQL.
- `{{name}}` is a **table or column name** — validated against the live schema before the query is built. An
  unknown name stops the step with a clear message instead of reaching MySQL.

---

## `consistency_rules.yaml` — invariants between tables

Rules are normally created on the Data consistency page and saved to the database, but a team can also ship them:

```yaml
- name: Every successful payment has a lead
  severity: high
  source:
    table: payments
    key: id
    filters: [{col: status, op: equals, val: SUCCESS}]
  target:
    table: leads
    key: payment_id
  expect: exists            # exists | not exists
  sample: 10000             # 0 = all rows
```

---

## `ai_context.md` — domain notes for the AI

Free text, sent with every AI question so the model understands your vocabulary:

```markdown
- A "campaign" lives in `campaigns`; its delivery units are in `campaign_delivery_unit`.
- `status`: 0 unpublished, 1 published, 2 paused, 3 draft.
- Soft deletes use `removed_at`, never a flag.
```

Keep company-specific notes in `local/ai_context.md` — `config/ai_context.md` is published with the project.

---

## `validation_rules.yaml` — saved API response rules

```yaml
- name: Campaign detail
  rules:
    - {path: "data.status.name", operator: equals, expected: Published}
    - {path: "data.id", operator: exists}
    - {path: "data.items", operator: count, expected: "5"}
    - {path: "data.email", operator: regex, expected: "^[^@]+@[^@]+$", note: must look like an address}
```

Operators: `equals`, `not equals`, `exists`, `missing`, `not null`, `contains`, `count`, `regex`, `type`,
`greater than`, `less than`, `one of`. A rule that cannot be evaluated is reported as a warning, not a pass.

---

## `templates/` — bug report formats

`config/templates/bug_jira.txt`, `bug_markdown.txt`, `bug_text.txt`. Copy one into `local/templates/` to change the
wording your team uses. Placeholders use single braces and are filled from the Bug evidence form plus the evidence you collected:
`{title}`, `{environment}`, `{identifier}`, `{summary}`, `{steps}`, `{expected}`, `{actual}`, `{generated_at}`,
`{sql}`, `{mismatches}`, `{api_evidence}`, `{db_evidence}`. Evidence is always masked, for everyone.

## `legal/privacy.md` and `legal/terms.md`

The shipped documents live in `config/legal/`. They use `{org}`, `{app}`, `{contact}`, `{url}` and `{updated}`
placeholders, filled from the environment variables above, so most installations only need `.env`. To write your
own wording, put `privacy.md` or `terms.md` in `local/legal/` — the app then shows yours instead and stops
mentioning that it is the shipped text.

---

## Privacy settings (admin UI, not a file)

**Admin → Privacy** adds to the built-in lists:

- **Secret column names** — never selected, never shown, for anyone. Built in: password, token, secret, API key,
  OTP, Aadhaar, PAN and variants.
- **Personal column names** — masked for non-admins. Built in: mobile, phone, contact, whatsapp, plus email
  addresses anywhere in a value.

They take effect immediately, everywhere: queries, results, downloads, evidence and the activity log.

---

© 2026 Rakshit Jain · MIT licensed.
