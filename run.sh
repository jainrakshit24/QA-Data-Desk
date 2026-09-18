#!/usr/bin/env bash
# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

# Start QA Data Desk on http://localhost:8501
# First run installs the Python packages it needs (no sudo required).
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import streamlit, pandas, plotly, pymysql, yaml" 2>/dev/null; then
  echo "Installing required packages…"
  python3 -m pip install --user -q -r requirements.txt
fi

# On a fresh install, show the one-time code needed to create the admin account.
python3 - <<'PY_SETUP'
import envfile
envfile.load()
import auth
auth.init()
if auth.user_count() == 0:
    print(f"\n  First run — admin setup code: {auth.setup_code()}\n  Enter it on the sign-in page to create the admin account.\n")
PY_SETUP

PORT="${PORT:-8501}"
ADDRESS="${ADDRESS:-127.0.0.1}"   # set ADDRESS=0.0.0.0 to allow other computers to connect
echo "QA Data Desk → http://localhost:${PORT}"
exec python3 -m streamlit run app.py --server.port "$PORT" --server.address "$ADDRESS"
