"""Test-wide fixtures. Isolates logger state between tests so file handles don't leak."""
from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_inventory_sync_logger():
    yield
    # Close and detach handlers so tmp_path directories can be cleaned up on Windows.
    root = logging.getLogger("inventory_sync")
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
