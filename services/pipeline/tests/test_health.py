"""Health endpoint with the queue check stubbed via the in-memory backend."""

from fastapi.testclient import TestClient

from pipeline.health import create_health_app
from pipeline.jobs.heartbeat import HeartbeatState
from pipeline.queue.memory import InMemoryQueue


def make_client(queue: InMemoryQueue, state: HeartbeatState) -> TestClient:
    return TestClient(create_health_app(queue, heartbeat_state=state))


def test_health_ok():
    client = make_client(InMemoryQueue(), HeartbeatState())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "pipeline"
    assert body["status"] == "ok"
    assert body["queue"] == {"backend": "memory", "reachable": True, "error": None}
    assert body["heartbeat"]["count"] == 0


def test_health_degraded_when_queue_unreachable():
    queue = InMemoryQueue(connected=False)
    client = make_client(queue, HeartbeatState())
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["queue"]["reachable"] is False
    assert "disconnected" in body["queue"]["error"]


def test_health_reports_heartbeat_state():
    state = HeartbeatState(count=7, last_timestamp=1_700_000_000, last_run_at="t")
    client = make_client(InMemoryQueue(), state)
    body = client.get("/health").json()
    assert body["heartbeat"] == {
        "count": 7,
        "last_timestamp": 1_700_000_000,
        "last_run_at": "t",
    }


async def test_heartbeat_updates_state_and_health():
    from pipeline.jobs import heartbeat

    queue = InMemoryQueue()
    state = HeartbeatState()
    heartbeat.register(queue, state=state)
    await queue.run_periodic(heartbeat.JOB_NAME, timestamp=1_700_000_000)

    body = make_client(queue, state).get("/health").json()
    assert body["heartbeat"]["count"] == 1
    assert body["heartbeat"]["last_timestamp"] == 1_700_000_000
    assert body["heartbeat"]["last_run_at"] is not None


def test_health_reports_no_pools_before_any_are_opened(monkeypatch):
    """M3-B: the block is always present, and empty is a truthful answer —
    a process that has not touched Postgres has opened no pool."""
    from pipeline.db import pool as dbpool

    monkeypatch.setattr(dbpool, "_pools", {})
    body = make_client(InMemoryQueue(), HeartbeatState()).get("/health").json()
    assert body["db_pool"] == {}


def test_health_reports_db_pool_stats(monkeypatch):
    """The pool is session-scoped and otherwise invisible from outside the
    process; this block is the operator-visible evidence it is in use
    (spec §5: observability is the mitigation, not garnish)."""
    from pipeline.db import pool as dbpool

    class StubPool:
        name = "database:5432/postgis"

        def get_stats(self):
            return {
                "pool_min": 2,
                "pool_max": 16,
                "pool_size": 4,
                "pool_available": 3,
                "requests_waiting": 0,
                "connections_num": 4,
            }

    monkeypatch.setattr(
        dbpool, "_pools", {"postgresql://username:password@database:5432/postgis": StubPool()}
    )

    body = make_client(InMemoryQueue(), HeartbeatState()).get("/health").json()
    assert body["db_pool"] == {
        "database:5432/postgis": {
            "pool_min": 2,
            "pool_max": 16,
            "pool_size": 4,
            "pool_available": 3,
            "requests_waiting": 0,
            "connections_num": 4,
        }
    }
    # The DSN's password must never reach an unauthenticated endpoint.
    assert "password" not in str(body["db_pool"])


def test_health_names_the_image_policy_file():
    body = make_client(InMemoryQueue(), HeartbeatState()).get("/health").json()
    policy = body["image_policy"]
    assert policy["ok"] is True and policy["version"] == 1 and policy["error"] is None
    assert policy["file"].endswith("default.json")


def test_a_broken_image_policy_is_reported_but_not_a_503(tmp_path):
    from fastapi.testclient import TestClient

    missing = tmp_path / "policy.json"
    app = create_health_app(
        InMemoryQueue(), heartbeat_state=HeartbeatState(), image_policy_file=missing
    )
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    policy = response.json()["image_policy"]
    assert policy == {"file": str(missing), "ok": False, "version": None, "error": policy["error"]}
    assert "policy.json" in policy["error"]
