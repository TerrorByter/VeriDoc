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
from collections.abc import Iterator

os.environ.update({
    "EXTRACTION_MODE": "stub", "OCR_ENABLED": "false", "AGENTS_ENABLED": "false",
    "PROMPT_SCREEN_ENABLED": "false",
    # Fake clients still meter usage: keep it out of the real log
    "USAGE_LOG_PATH": os.path.join(tempfile.mkdtemp(), "usage.jsonl"),
})


import pytest  # noqa: E402  (after the environment is set)


@pytest.fixture(autouse=True, scope="session")
def official_records() -> None:
    """The official records service, reached in-process: the real mock_gov
    app and data, no server or network. Session-wide, so pipeline runs in
    module-scoped fixtures reach it too; a test may still replace the client
    with monkeypatch."""
    from fastapi.testclient import TestClient

    from gldb import official
    from mock_gov.app import app

    official._client = TestClient(app)


@pytest.fixture(autouse=True, scope="session")
def designations(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """The UN designations, without the network: synced once from the MAS and
    UN fixtures into a temporary file. The real file changes with every sync
    and must not decide a test; a test may still point the path elsewhere."""
    from gldb import reference, sanctions_sync
    from tests.un_fixtures import UnServer

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reference, "DESIGNATIONS_PATH", str(tmp_path_factory.mktemp("reference") / "designations.json"))
        sanctions_sync.sync(UnServer().client())
        yield
