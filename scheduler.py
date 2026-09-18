#!/usr/bin/env python3
# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Run QA checks without a browser, for cron.

Streamlit is a web app, not a scheduler, so this runs the same checks from the command line, records every
run in the same place the QA dashboard reads, and reports what fired. It stays read-only: checks go through
the same guard and the same READ ONLY session.

    python3 scheduler.py --list
    python3 scheduler.py --connection "Staging" --schedule daily
    python3 scheduler.py --tag payments --notify log,webhook --webhook https://hooks.example.com/… --fail-on alerts

Cron (every morning at 08:15, log kept next to the app):

    15 8 * * *  cd /home/you/qa_dashboard && /usr/bin/python3 scheduler.py --schedule daily >> logs/scheduler.log 2>&1

Notifications carry counts, never rows. Add your own notifier as `local/notify.py` with
`send(summary: dict, results: list) -> None`; it is called last and its errors never stop the run.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import envfile  # noqa: E402

envfile.load()

import db  # noqa: E402
import packs  # noqa: E402
import privacy  # noqa: E402
import store  # noqa: E402
from core import checks as core_checks  # noqa: E402

ROW_LIMIT = 5000
SCHEDULES = ["hourly", "daily", "weekly", "monthly", "manual"]
DUE_AFTER = {"hourly": 3600, "daily": 24 * 3600, "weekly": 7 * 24 * 3600, "monthly": 30 * 24 * 3600}


def load_checks(database=None):
    items = [core_checks.normalise(c) | {"schedule": str(c.get("schedule") or "manual").lower()}
             for c in packs.load_list("checks") if c.get("sql")]
    return core_checks.filter_checks(items, database=database) if database else items


def select(checks, schedule=None, tags=(), ids=(), severities=()):
    out = []
    for c in checks:
        if schedule and c.get("schedule") != schedule:
            continue
        if ids and c["id"] not in ids:
            continue
        if tags and not (set(tags) & set(c["tags"])):
            continue
        if severities and c["severity"] not in severities:
            continue
        out.append(c)
    return out


def is_due(check, history, now=None):
    """True when this check has not run within its own schedule window."""
    window = DUE_AFTER.get(check.get("schedule") or "manual")
    if not window:
        return True
    last = (history.get(check["id"]) or {}).get("latest")
    if not last:
        return True
    try:
        ran = dt.datetime.fromisoformat(last["ran_at"])
    except (ValueError, KeyError, TypeError):
        return True
    return ((now or dt.datetime.utcnow()) - ran).total_seconds() >= window


def run_one(check, connection):
    started = time.time()
    try:
        privacy.check_sql(check["sql"])
        sql, limited = db.apply_row_limit(check["sql"], ROW_LIMIT + 1)
        frame, seconds, truncated = db.run(sql, max_rows=ROW_LIMIT if limited else db.MAX_ROWS, name=connection)
        return {"df": frame, "seconds": seconds, "truncated": truncated, "error": None}
    except Exception as e:                       # reported per check; one failure never stops the batch
        return {"df": None, "seconds": round(time.time() - started, 2), "truncated": False,
                "error": f"{type(e).__name__}: {e}"}


def run_checks(checks, connection, workers=6):
    """Run the checks, record every run, and return one summary row per check (counts only, no rows)."""
    previous = store.latest_runs(connection)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        raw = list(pool.map(lambda c: (c, run_one(c, connection)), checks))
    results = []
    for check, res in raw:
        status = core_checks.evaluate(check, res)
        rows = None if status in ("error", "outdated") else core_checks.result_rows(res)
        prev = (previous.get(check["id"]) or {}).get("latest")
        previous_rows = prev["result_rows"] if prev else None
        fired = core_checks.alerts(check, status, rows, res.get("seconds"), previous_rows)
        store.record_check_run(check, connection, status, rows, round(res.get("seconds") or 0, 2),
                               res.get("error"), None)
        results.append({"id": check["id"], "title": check["title"], "area": check["area"],
                        "severity": check["severity"], "status": status, "rows": rows,
                        "previous_rows": previous_rows, "change": core_checks.change(rows, previous_rows),
                        "seconds": round(res.get("seconds") or 0, 2), "error": res.get("error"), "alerts": fired})
    return results


def summarise(results, connection, database):
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"ran_at": dt.datetime.now().replace(microsecond=0).isoformat(), "connection": connection,
            "database": database, "checks": len(results), "status_counts": counts,
            "failed": [r["id"] for r in results if r["status"] == "failed"],
            "errored": [r["id"] for r in results if r["status"] in ("error", "outdated")],
            "alerts": [{"id": r["id"], "title": r["title"], "severity": r["severity"], "alerts": r["alerts"]}
                       for r in results if r["alerts"]]}


# ------------------------------------------------------------------ notifiers
def text_report(summary, results):
    lines = [f"QA checks · {summary['database']} ({summary['connection']}) · {summary['ran_at']}",
             f"{summary['checks']} checks: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["status_counts"].items()))]
    for r in sorted(results, key=lambda r: (r["status"] != "failed", r["severity"])):
        if r["status"] in ("failed", "error", "outdated") or r["alerts"]:
            mark = core_checks.STATUS_MARK.get(r["status"], r["status"])
            count = "—" if r["rows"] is None else f"{r['rows']:,} rows"
            change = core_checks.change_text(r["change"])
            line = f"  {mark} [{r['severity']}] {r['title']} · {count}"
            if change:
                line += f" · {change}"
            if r["alerts"]:
                line += " · ALERT: " + "; ".join(r["alerts"])
            if r["error"]:
                line += f" · {r['error'][:160]}"
            lines.append(line)
    if len(lines) == 2:
        lines.append("  nothing failed and no alert fired")
    return "\n".join(lines)


def notify_print(summary, results, options):
    print(text_report(summary, results), flush=True)


def notify_log(summary, results, options):
    path = options.get("log") or os.path.join(HERE, "logs", "checks.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(text_report(summary, results) + "\n\n")
    return path


def notify_json(summary, results, options):
    path = options.get("json") or os.path.join(HERE, "logs", "checks.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, default=str)
    return path


def notify_webhook(summary, results, options):
    """POST the summary as JSON (Slack-compatible: it also carries a `text` field). Counts only, never rows."""
    url = options.get("webhook") or os.environ.get("QA_ALERT_WEBHOOK")
    if not url:
        raise ValueError("No webhook URL — pass --webhook or set QA_ALERT_WEBHOOK.")
    body = json.dumps({"text": text_report(summary, results), "summary": summary}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return f"{resp.status} {url.split('?')[0]}"


NOTIFIERS = {"print": notify_print, "log": notify_log, "json": notify_json, "webhook": notify_webhook}


def notify(names, summary, results, options, only_when_needed=True):
    """Run the chosen notifiers, then local/notify.py if it exists. A broken notifier never fails the run."""
    interesting = bool(summary["alerts"] or summary["failed"] or summary["errored"])
    sent = []
    for name in names:
        if only_when_needed and name in ("webhook",) and not interesting:
            continue
        fn = NOTIFIERS.get(name)
        if not fn:
            print(f"  unknown notifier: {name} (known: {', '.join(NOTIFIERS)})", file=sys.stderr)
            continue
        try:
            sent.append((name, fn(summary, results, options)))
        except Exception as e:
            print(f"  notifier {name} failed: {type(e).__name__}: {e}", file=sys.stderr)
    custom = os.path.join(packs.LOCAL_DIR, "notify.py")
    if os.path.exists(custom):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("qa_local_notify", custom)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.send(summary, results)
            sent.append(("local/notify.py", "sent"))
        except Exception as e:
            print(f"  local/notify.py failed: {type(e).__name__}: {e}", file=sys.stderr)
    return sent


# ------------------------------------------------------------------ command line
def build_parser():
    p = argparse.ArgumentParser(description="Run QA checks from the command line (read-only).")
    p.add_argument("--connection", help="Connection name; the first configured one by default")
    p.add_argument("--schedule", choices=SCHEDULES, help="Only checks with this schedule in their YAML")
    p.add_argument("--tag", action="append", default=[], help="Only checks with this tag (repeatable)")
    p.add_argument("--id", action="append", default=[], help="Only this check id (repeatable)")
    p.add_argument("--severity", action="append", default=[], choices=core_checks.SEVERITIES)
    p.add_argument("--due-only", action="store_true", help="Skip checks already run inside their schedule window")
    p.add_argument("--notify", default="print", help="Comma separated: print, log, json, webhook")
    p.add_argument("--webhook", help="Webhook URL (or set QA_ALERT_WEBHOOK)")
    p.add_argument("--log", help="Log file for the log notifier (default logs/checks.log)")
    p.add_argument("--json", dest="json_path", help="File for the json notifier (default logs/checks.json)")
    p.add_argument("--always-notify", action="store_true", help="Notify even when nothing failed")
    p.add_argument("--fail-on", choices=["never", "failed", "alerts", "error"], default="never",
                   help="Exit with code 1 when this happens (for cron alerting)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--list", action="store_true", help="List the checks that would run and exit")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    connection = args.connection or (db.connection_names() or [None])[0]
    if not connection:
        print("No database connection configured (see .db.json or QA_DB_*).", file=sys.stderr)
        return 2
    database = db.database_name(connection)
    store.init()
    store.apply_privacy_settings()
    checks = select(load_checks(database), args.schedule, [t.lower() for t in args.tag], args.id, args.severity)
    if args.due_only:
        history = store.latest_runs(connection)
        checks = [c for c in checks if is_due(c, history)]
    if args.list:
        print(f"{len(checks)} checks for {database} ({connection})")
        for c in checks:
            print(f"  {c['id']} · {c['severity']} · {c.get('schedule', 'manual')} · {c['title']}")
        return 0
    if not checks:
        print(f"No checks match for {database} ({connection}).")
        return 0
    results = run_checks(checks, connection, workers=args.workers)
    summary = summarise(results, connection, database)
    options = {"webhook": args.webhook, "log": args.log, "json": args.json_path}
    notify(args.notify.split(","), summary, results, options, only_when_needed=not args.always_notify)
    if args.fail_on == "failed" and summary["failed"]:
        return 1
    if args.fail_on == "alerts" and summary["alerts"]:
        return 1
    if args.fail_on == "error" and summary["errored"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
