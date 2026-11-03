"""Apply one chart SQL file inside a single transaction.

Used by the db-bootstrap hook Job. Connection settings come from libpq's PG*
environment (PGHOST, PGUSER, PGPASSWORD, PGSSLMODE, PGSSLROOTCERT, ...). Every
STAC_HIGHER_SQL_<NAME> variable becomes the transaction-local setting
`stac_higher_sql.<name>`, which the SQL reads with current_setting(). Setting
values (role names, passwords) travel as bind parameters and are never printed.
Passwords are turned into client-side SCRAM-SHA-256 verifiers first, so the
plaintext never reaches the server (or its logs); Postgres accepts a verifier
in ALTER/CREATE ROLE ... PASSWORD and the plaintext still logs in.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import psycopg

ENV_PREFIX = "STAC_HIGHER_SQL_"
GUC_PREFIX = "stac_higher_sql."


def settings_from_env(env: Mapping[str, str]) -> dict[str, str]:
    return {
        GUC_PREFIX + key[len(ENV_PREFIX) :].lower(): value
        for key, value in env.items()
        if key.startswith(ENV_PREFIX) and len(key) > len(ENV_PREFIX)
    }


class SettingsError(Exception):
    """A bad setting set; the message names keys only, never values."""


PASSWORD_SUFFIX = "_password"
ROLE_SUFFIX = "_role"


def hash_passwords(conn: psycopg.Connection, settings: dict[str, str]) -> dict[str, str]:
    """Replace every `<x>_password` value with a SCRAM-SHA-256 verifier for the
    paired `<x>_role` (the role is the encrypt_password user argument)."""
    out = dict(settings)
    for name, password in settings.items():
        if not (name.startswith(GUC_PREFIX) and name.endswith(PASSWORD_SUFFIX)):
            continue
        role_key = name[: -len(PASSWORD_SUFFIX)] + ROLE_SUFFIX
        if role_key not in settings:
            raise SettingsError(f"{name} has no paired {role_key}")
        if not password:
            raise SettingsError(f"{name} is empty")
        out[name] = conn.pgconn.encrypt_password(
            password.encode(), settings[role_key].encode(), b"scram-sha-256"
        ).decode()
    return out


def apply(conn: psycopg.Connection, sql: str, settings: dict[str, str]) -> None:
    for name, value in hash_passwords(conn, settings).items():
        conn.execute("SELECT set_config(%s, %s, true)", (name, value))
    # No parameters: psycopg sends the whole file as one multi-statement query.
    conn.execute(sql)
    conn.commit()


def _default_connect() -> psycopg.Connection:
    return psycopg.connect("")


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] = os.environ,
    connect: Callable[[], psycopg.Connection] = _default_connect,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: run_sql.py <file.sql>", file=sys.stderr)
        return 2
    path = Path(args[0])
    name = path.name
    sql = path.read_text()
    conn = None
    try:
        conn = connect()
        apply(conn, sql, settings_from_env(env))
        info = conn.info
        print(f"applied {name} to {info.host}:{info.port}/{info.dbname} as {info.user}")
        return 0
    except SettingsError as exc:
        print(f"{name} failed: {exc}")
        return 1
    except psycopg.Error as exc:
        if exc.sqlstate is None:
            # libpq connection failure: no SQLSTATE; its message never holds the password.
            print(f"{name} failed: {exc}")
        else:
            detail = exc.diag.message_primary or type(exc).__name__
            print(f"{name} failed: {exc.sqlstate} {detail}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
