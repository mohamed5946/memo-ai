"""Pytest fixtures: point all LONA v2 state at a temp dir so tests
never touch the real DATA_DIR."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lona2 import stores  # noqa: E402
from lona2 import logger  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, "DATA_DIR", tmp_path / "lona-test")
    (tmp_path / "lona-test").mkdir(parents=True, exist_ok=True)
    logger.set_log_file(tmp_path / "lona-test" / "test.log")
    yield
