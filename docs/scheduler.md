# Scheduled checks

Streamlit is a web app, not a scheduler. `scheduler.py` runs the same checks from the command line, records every
run in the same place the QA dashboard reads, and tells you when something fires. It never imports Streamlit, so it
is safe in cron, CI or a container.

## Quick start

```bash
python3 scheduler.py --list                              # what would run, and on which database
python3 scheduler.py --schedule daily --notify log       # run them, append logs/checks.log
python3 scheduler.py --tag payments --fail-on failed     # exit 1 if any check failed
```

## Cron

```cron
# every morning at 08:15
15 8 * * *  cd /home/you/qa_dashboard && /usr/bin/python3 scheduler.py --schedule daily --due-only \
            --notify log,webhook >> logs/scheduler.log 2>&1

# hourly checks, but only the ones that are actually due
0 * * * *   cd /home/you/qa_dashboard && /usr/bin/python3 scheduler.py --schedule hourly --due-only \
            --notify webhook >> logs/scheduler.log 2>&1
```

Use the full path to Python (cron has a minimal `PATH`), and `cd` into the project first so `.env`, `local/` and the
SQLite file are found. Credentials come from `.env` — never put them in the crontab.

## Options

| Option | What it does |
|---|---|
| `--connection NAME` | Which database. Default: the first configured one. |
| `--schedule hourly\|daily\|weekly\|monthly\|manual` | Only checks carrying that `schedule:` in YAML |
| `--tag TAG` (repeatable) | Only checks with this tag |
| `--id CHECK_ID` (repeatable) | Only these checks |
| `--severity LEVEL` (repeatable) | Only this severity |
| `--due-only` | Skip checks already run inside their own window — safe to run cron more often than needed |
| `--notify print,log,json,webhook` | Where results go (default `print`) |
| `--webhook URL` | Overrides `QA_ALERT_WEBHOOK` |
| `--log PATH` / `--json PATH` | Override `logs/checks.log` / `logs/checks.json` |
| `--always-notify` | Notify even when nothing failed (the webhook is quiet by default) |
| `--fail-on never\|failed\|alerts\|error` | Exit code 1 so cron or CI can react |
| `--workers N` | Checks run in parallel; default 6 |
| `--list` | Print what would run and exit |

## What "due" means

A check's `schedule:` is also its window: `hourly` = 1 h, `daily` = 24 h, `weekly` = 7 d, `monthly` = 30 d.
`--due-only` compares that window with the last recorded run for the same connection. A check with
`schedule: manual` has no window and always runs when selected.

## Notifiers

- **print** — human-readable summary on stdout (what cron mails you).
- **log** — appends the same text to `logs/checks.log`.
- **json** — writes `logs/checks.json` with the full summary and per-check results, for another tool to read.
- **webhook** — POSTs JSON to `QA_ALERT_WEBHOOK`. The body carries a `text` field (so Slack-compatible endpoints
  render it) plus a structured `summary`. **It stays silent unless something failed, errored or an alert fired**,
  unless you pass `--always-notify`.

Example output:

```
QA checks · shop (Staging) · 2026-09-18T08:15:03
26 checks: failed 4, passed 21, info 1
  ❌ Failed [high] Successful payment without a lead · 37 rows · +12 since last run · ALERT: rows increased 25 → 37
  ❌ Failed [medium] Campaigns whose client does not exist · 378 rows
  ⚠️ Error [medium] Orphan criteria rows · ProgrammingError: Table 'shop.rp_criteria' doesn't exist
```

Notifications carry check names, statuses, counts and alert text. **Never rows, never values.**

## Your own notifier

Drop a `local/notify.py` (git-ignored) with a `send` function:

```python
def send(summary, results):
    """summary: dict with ran_at, connection, database, checks, status_counts, failed, errored, alerts
       results: one dict per check — id, title, area, severity, status, rows, previous_rows, change,
                seconds, error, alerts"""
    if not summary["alerts"]:
        return
    # e.g. create a Jira ticket, send an email, page someone
```

It is called after the built-in notifiers, and an exception inside it is reported but never fails the run.

## Exit codes

`0` success · `1` the condition named by `--fail-on` happened · `2` no database connection configured.

---

© 2026 Rakshit Jain · MIT licensed.
