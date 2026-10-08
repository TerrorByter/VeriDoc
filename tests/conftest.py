"""
tests/conftest.py
-----------------
Keeps the offline suite offline whatever .env says. Runs before any gldb
import, and load_dotenv never overrides variables already set, so the
pipeline uses the stub extractor with no OCR and no AI team (so no test can
reach Bedrock through gldb.ai_team.team.CONFIGURED_TEAM). The opt-in live
tests (RUN_LIVE=1) call the AWS clients directly and do not use these flags.
"""

import os
import tempfile

os.environ.update({
    "EXTRACTION_MODE": "stub", "OCR_ENABLED": "false", "AGENTS_ENABLED": "false",
    "PROMPT_SCREEN_ENABLED": "false",
    # Fake clients still meter usage: keep it out of the real log
    "USAGE_LOG_PATH": os.path.join(tempfile.mkdtemp(), "usage.jsonl"),
})
