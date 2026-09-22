import pytest


@pytest.fixture(autouse=True)
def _reset_process_flow_batcher():
    """M3-D: handlers registered without a batcher add to the process-global
    FLOW_BATCHER; empty it around every test so nothing leaks between them."""
    from pipeline.flow.batcher import FLOW_BATCHER

    FLOW_BATCHER.drain()
    yield
    FLOW_BATCHER.drain()
