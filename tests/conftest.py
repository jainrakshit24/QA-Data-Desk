# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Keep tests independent of the developer's own local/ pack and app data."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="qa_desk_tests_")
os.environ["QA_LOCAL_DIR"] = os.path.join(_tmp, "local")
os.environ.setdefault("QA_APP_DB", os.path.join(_tmp, "app.sqlite3"))
os.environ.setdefault("QA_SETUP_CODE", "TESTCODE")
