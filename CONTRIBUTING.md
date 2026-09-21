# Contributing

Thanks for helping. A few things this project cares about more than most:

## The rules that must not break
1. **Read-only.** Every query goes through `db.check_read_only` and runs in a `SET SESSION TRANSACTION READ ONLY`
   session. No feature may write to a connected database — not even "safe" writes, not even behind a flag.
2. **Privacy.** Secret columns (password, token, OTP, API key…) are never selected or shown; emails and phone
   numbers are masked for non-admins; logs, evidence, reports and notifications carry counts, never values.
3. **AI sees names only.** Gemini may receive table names, column names, your question and SQL text.
   Never rows, never identifier values, and it never executes anything by itself.
4. **Nothing personal in the repo.** Credentials belong in `.env`, `.db.json` or the app — all git-ignored.
   `local/` holds anything specific to your company.

Tests exist for all four (`tests/test_security.py`, `tests/test_phase3.py`). If you change that behaviour,
a test should fail.

## Getting set up
```bash
python3 -m pip install --user -r requirements-dev.txt
cp .env.example .env          # optional — you can add databases in the app instead
./run.sh
```

## Before opening a pull request
```bash
python3 -m pytest -q          # 350+ tests, random order; database tests skip without a connection
```
- Add a test with the change. Prefer a test that fails without your fix.
- Keep the UI wording plain: the audience is QA engineers, not DBAs. No jargon in messages a user can hit.
- One feature per pull request, and say in the description what you ran to verify it.

## Where things live
`core/` is logic with no UI and is tested directly; `views/` is one file per page; `db.py`, `privacy.py`,
`auth.py`, `schema.py` and `ai.py` are the shared layers. See "Project layout" in the README.
