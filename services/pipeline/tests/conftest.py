import pytest

# K-5: the Kubernetes executor tests' fake API server, token file, clock and
# executor (a module's own fixture of the same name takes precedence).
from _kube_executor_fixtures import clock, executor, fake, token_file  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_process_flow_batcher():
    """M3-D: handlers registered without a batcher add to the process-global
    FLOW_BATCHER; empty it around every test so nothing leaks between them."""
    from pipeline.flow.batcher import FLOW_BATCHER

    FLOW_BATCHER.drain()
    yield
    FLOW_BATCHER.drain()
