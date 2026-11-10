import pytest


@pytest.fixture(autouse=True)
def _reset_snapshot_coalescing():
    """LabContainer(passphrase=...) enables snapshot coalescing process-wide; keep tests independent."""
    from src.db import engine

    engine.set_snapshot_min_interval(0)
    yield
    engine.set_snapshot_min_interval(0)
