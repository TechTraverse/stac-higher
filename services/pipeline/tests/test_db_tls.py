"""libpq's own TLS settings apply to every DATABASE_URL connection (R-2): no code reads them."""

import re
import socket
import threading
import time

import psycopg
import pytest

from pipeline.config import Settings
from pipeline.db.pool import pool_name


@pytest.fixture
def tls_wanting_server():
    """Loopback server that answers the SSLRequest with ``S`` and then goes quiet."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(10)

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                got = b""
                while len(got) < 8:
                    chunk = conn.recv(8 - len(got))
                    if not chunk:
                        return
                    got += chunk
                conn.sendall(b"S")
                time.sleep(1)
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield srv.getsockname()[1]
    finally:
        srv.close()
        thread.join(timeout=5)


async def test_database_url_connections_verify_tls_from_the_libpq_environment(
    tls_wanting_server, monkeypatch
):
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "/nonexistent/rds-ca-bundle.pem")
    url = f"postgresql://u:p@127.0.0.1:{tls_wanting_server}/db"
    settings = Settings.from_env({"DATABASE_URL": url})
    with pytest.raises(psycopg.OperationalError, match=re.escape("/nonexistent/rds-ca-bundle.pem")):
        await psycopg.AsyncConnection.connect(settings.database_url)


async def test_tls_parameters_in_the_dsn_work_without_env(tls_wanting_server, monkeypatch):
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    url = (
        f"postgresql://u:p@127.0.0.1:{tls_wanting_server}/db"
        "?sslmode=verify-full&sslrootcert=/nonexistent/x.pem"
    )
    with pytest.raises(psycopg.OperationalError, match=re.escape("/nonexistent/x.pem")):
        await psycopg.AsyncConnection.connect(Settings.from_env({"DATABASE_URL": url}).database_url)


def test_pool_name_drops_tls_parameters():
    url = (
        "postgresql://u:p@db.example:5432/postgis"
        "?sslmode=verify-full&sslrootcert=/etc/rds/ca-bundle.pem"
    )
    assert pool_name(url) == "db.example:5432/postgis"
