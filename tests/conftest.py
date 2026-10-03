"""
tests/conftest.py
-----------------
Keeps the offline suite offline whatever .env says. Runs before any gldb
import, and load_dotenv never overrides variables already set, so the
pipeline uses the stub extractor with no OCR or LLM summary. The opt-in live
tests (RUN_LIVE=1) call the AWS clients directly and do not use these flags.
"""

import os

os.environ.update({"EXTRACTION_MODE": "stub", "OCR_ENABLED": "false", "EXPLAIN_ENABLED": "false"})
