"""libpq's own TLS settings apply to the cube-server's DSN (R-2): no code reads them."""

import re
import socket
import threading
import time

import psycopg
import pytest

from stac_higher_cube_server.sinks import PgSinkSource


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


def test_pg_sink_source_verifies_tls_from_the_libpq_environment(tls_wanting_server, monkeypatch):
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "/nonexistent/rds-ca-bundle.pem")
    with pytest.raises(psycopg.OperationalError, match=re.escape("/nonexistent/rds-ca-bundle.pem")):
        PgSinkSource(f"postgresql://u:p@127.0.0.1:{tls_wanting_server}/db").enabled_sinks()
