import contextlib
import json
import os
import signal

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def safelight_home(tmp_path, monkeypatch):
    """Isolated Safelight home; any hub started during the test runs on a free port."""
    home = tmp_path / "safelight-home"
    monkeypatch.setenv("SAFELIGHT_HOME", str(home))
    monkeypatch.setenv("SAFELIGHT_PORT", "0")
    yield home
    config = home / "hub.json"
    with contextlib.suppress(OSError, ValueError, KeyError, ProcessLookupError):
        pid = json.loads(config.read_text())["pid"]
        if pid != os.getpid():
            os.kill(pid, signal.SIGTERM)
