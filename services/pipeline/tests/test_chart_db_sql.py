"""Helm chart DB role SQL (R-6) and its psycopg runner.

The unit tests always run. The DB-gated tests auto-skip unless DATABASE_URL is
set (CI's `pipeline` job sets it against the migrated pgstac service):

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_chart_db_sql.py
"""

import importlib.util
import os
import secrets
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

REPO = Path(__file__).resolve().parents[3]
DB_DIR = REPO / "infra" / "helm" / "stac-higher" / "files" / "db"
DATABASE_URL = os.environ.get("DATABASE_URL")

needs_db = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skipping DB integration tests"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_sql", DB_DIR / "run_sql.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_sql = _load_runner()

ROLE_KEYS = ("owner", "api", "reader", "proxy", "cube")
METACHARS = "p'a\"ss\\w@rd/%"


# ---------------------------------------------------------------- unit tests


def test_settings_from_env_maps_and_lowercases():
    env = {"STAC_HIGHER_SQL_OWNER_ROLE": "o", "PATH": "x"}
    assert run_sql.settings_from_env(env) == {"stac_higher_sql.owner_role": "o"}


def test_settings_from_env_ignores_unprefixed_and_lookalikes():
    env = {"OWNER_ROLE": "a", "STAC_HIGHER_OWNER_ROLE": "b", "PGPASSWORD": "c"}
    assert run_sql.settings_from_env(env) == {}


class _FakeInfo:
    host = "db.example.com"
    port = 5432
    dbname = "app"
    user = "master"


class _FakeConn:
    info = _FakeInfo()

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.executed: list[tuple] = []
        self.commits = 0
        self.closed = False

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if self.fail_with is not None:
            raise self.fail_with
        return self

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_main_reports_failure_without_values(tmp_path, capsys):
    path = tmp_path / "boom.sql"
    path.write_text("SELECT 1;")
    conn = _FakeConn(psycopg.errors.InsufficientPrivilege("denied"))
    env = {"STAC_HIGHER_SQL_OWNER_PASSWORD": "s3cr3t"}
    assert run_sql.main([str(path)], env, lambda: conn) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "boom.sql failed" in text
    assert "42501" in text
    assert "s3cr3t" not in text
    assert conn.closed


def test_apply_passes_settings_as_parameters_then_runs_sql_and_commits():
    conn = _FakeConn()
    settings = {"stac_higher_sql.owner_password": "p'\"; DROP"}
    run_sql.apply(conn, "SELECT 42;", settings)
    assert conn.executed[0][1] == ("stac_higher_sql.owner_password", "p'\"; DROP")
    assert "set_config" in conn.executed[0][0]
    assert "DROP" not in conn.executed[0][0]
    assert conn.executed[-1] == ("SELECT 42;", None)
    assert conn.commits == 1


def test_main_success_prints_target_not_values(tmp_path, capsys):
    path = tmp_path / "ok.sql"
    path.write_text("SELECT 1;")
    conn = _FakeConn()
    env = {"STAC_HIGHER_SQL_OWNER_PASSWORD": "s3cr3t", "HOME": "/x"}
    assert run_sql.main([str(path)], env, lambda: conn) == 0
    out = capsys.readouterr().out
    assert out.strip() == "applied ok.sql to db.example.com:5432/app as master"
    assert "s3cr3t" not in out
    assert conn.closed and conn.commits == 1
    # one set_config for the single STAC_HIGHER_SQL_* variable, then the file
    assert len(conn.executed) == 2


def test_main_requires_a_path(capsys):
    assert run_sql.main([], {}, lambda: _FakeConn()) == 2


def test_sql_files_read_only_settings_and_never_name_forbidden_attributes():
    for name in ("bootstrap.sql", "pgstac-grants.sql", "app-grants.sql"):
        text = (DB_DIR / name).read_text()
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))
        assert "current_setting(" in code and "stac_higher_sql." in code
        for banned in ("SUPERUSER", "REPLICATION", "BYPASSRLS"):
            assert banned not in code.upper()
    # passwords only ever reach statements through format(%L) in DO blocks
    boot = (DB_DIR / "bootstrap.sql").read_text()
    assert "%L" in boot and "DO $" in boot


# ------------------------------------------------------------ DB-gated tests


def _admin_conn(dbname=None):
    conninfo = DATABASE_URL if dbname is None else make_conninfo(DATABASE_URL, dbname=dbname)
    return psycopg.connect(conninfo, autocommit=True)


def _conninfo_for(dbname, user=None, password=None):
    kwargs = {"dbname": dbname}
    if user:
        kwargs["user"] = user
        kwargs["password"] = password
    return make_conninfo(DATABASE_URL, **kwargs)


def _env(prefix, passwords, omit=()):
    env = {}
    for key in ROLE_KEYS:
        env[f"STAC_HIGHER_SQL_{key.upper()}_ROLE"] = f"{prefix}{key}"
        env[f"STAC_HIGHER_SQL_{key.upper()}_PASSWORD"] = passwords[key]
    for name in omit:
        env.pop(name, None)
    return env


def _drop_roles(conn, roles):
    for role in roles:
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.fixture
def sandbox():
    """A throwaway database plus prefixed role names; removed on teardown."""
    tag = secrets.token_hex(4)
    dbname = f"chart_sql_{tag}"
    prefix = f"t{tag}_"
    roles = [f"{prefix}{k}" for k in ROLE_KEYS]
    with _admin_conn() as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        yield {"db": dbname, "prefix": prefix, "roles": roles, "tag": tag}
    finally:
        with _admin_conn() as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )
            for role in roles:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _run(script, sandbox, env, dbname=None):
    """Run a chart SQL file through the real runner as the admin user."""
    target = dbname or sandbox["db"]
    return run_sql.main(
        [str(DB_DIR / script)],
        env,
        lambda: psycopg.connect(_conninfo_for(target)),
    )


def _passwords(suffix=""):
    return {k: f"pw-{k}-{secrets.token_hex(6)}{suffix}" for k in ROLE_KEYS}


@needs_db
def test_bootstrap_creates_login_roles_and_extensions(sandbox):
    env = _env(sandbox["prefix"], _passwords())
    assert _run("bootstrap.sql", sandbox, env) == 0
    owner = f"{sandbox['prefix']}owner"
    proxy = f"{sandbox['prefix']}proxy"
    cube = f"{sandbox['prefix']}cube"
    with _admin_conn(sandbox["db"]) as conn:
        rows = {
            r[0]: r[1:]
            for r in conn.execute(
                "SELECT rolname, rolcanlogin, rolcreaterole, rolsuper "
                "FROM pg_roles WHERE rolname = ANY(%s)",
                (sandbox["roles"],),
            )
        }
        assert set(rows) == set(sandbox["roles"])
        assert all(canlogin for canlogin, _, _ in rows.values())
        assert rows[owner][1] is True and rows[owner][2] is False
        for role in sandbox["roles"]:
            if role != owner:
                assert rows[role][1] is False
        ext = {r[0] for r in conn.execute("SELECT extname FROM pg_extension")}
        assert {"postgis", "btree_gist", "unaccent"} <= ext
        assert conn.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CREATE')", (owner,)
        ).fetchone()[0]
        assert conn.execute(
            "SELECT pg_has_role(%s, 'stac_higher_cube_reader', 'MEMBER')", (cube,)
        ).fetchone()[0]
        settings = conn.execute(
            "SELECT s.setconfig FROM pg_db_role_setting s "
            "JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = %s",
            (proxy,),
        ).fetchall()
        flat = [c for row in settings for c in row[0]]
        assert "default_transaction_read_only=on" in flat


@needs_db
def test_owner_can_create_schemas(sandbox):
    passwords = _passwords()
    assert _run("bootstrap.sql", sandbox, _env(sandbox["prefix"], passwords)) == 0
    owner = f"{sandbox['prefix']}owner"
    with psycopg.connect(
        _conninfo_for(sandbox["db"], owner, passwords["owner"]), autocommit=True
    ) as conn:
        conn.execute("CREATE SCHEMA pgstac_probe")


@needs_db
def test_bootstrap_is_idempotent_and_rotates_passwords(sandbox):
    old, new = _passwords(), _passwords()
    assert _run("bootstrap.sql", sandbox, _env(sandbox["prefix"], old)) == 0
    assert _run("bootstrap.sql", sandbox, _env(sandbox["prefix"], new)) == 0
    owner = f"{sandbox['prefix']}owner"
    with psycopg.connect(_conninfo_for(sandbox["db"], owner, new["owner"])) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(_conninfo_for(sandbox["db"], owner, old["owner"]))


@needs_db
def test_bootstrap_handles_metacharacter_passwords(sandbox):
    passwords = _passwords()
    passwords["owner"] = METACHARS
    assert _run("bootstrap.sql", sandbox, _env(sandbox["prefix"], passwords)) == 0
    owner = f"{sandbox['prefix']}owner"
    with psycopg.connect(_conninfo_for(sandbox["db"], owner, METACHARS)) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


@needs_db
def test_bootstrap_requires_every_setting(sandbox, capsys):
    env = _env(sandbox["prefix"], _passwords(), omit=("STAC_HIGHER_SQL_CUBE_PASSWORD",))
    assert _run("bootstrap.sql", sandbox, env) == 1
    assert "unrecognized configuration parameter" in capsys.readouterr().out
    with _admin_conn() as admin:
        # all-or-nothing: the failed run left no roles behind
        left = admin.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)", (sandbox["roles"],)
        ).fetchone()[0]
    assert left == 0


@needs_db
def test_grants_are_idempotent(sandbox):
    """Runs against the migrated CI database itself (pgstac + stac_higher)."""
    dbname = conninfo_to_dict(DATABASE_URL).get("dbname") or "postgres"
    with _admin_conn(dbname) as conn:
        ready = conn.execute(
            "SELECT (SELECT count(*) FROM pg_roles WHERE rolname IN "
            "('pgstac_ingest','pgstac_read')) = 2 "
            "AND to_regclass('stac_higher.collection_settings') IS NOT NULL"
        ).fetchone()[0]
    if not ready:
        pytest.skip("needs the migrated pgstac + stac_higher database")

    passwords = _passwords()
    env = _env(sandbox["prefix"], passwords)
    api, reader, proxy = (f"{sandbox['prefix']}{k}" for k in ("api", "reader", "proxy"))
    try:
        with _admin_conn(dbname) as conn:
            for role in (api, reader, proxy):
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(passwords["proxy"])
                    )
                )
            conn.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                    sql.Identifier(proxy)
                )
            )
        for _ in range(2):
            assert _run("pgstac-grants.sql", sandbox, env, dbname) == 0
            assert _run("app-grants.sql", sandbox, env, dbname) == 0
        with _admin_conn(dbname) as conn:
            assert conn.execute(
                "SELECT pg_has_role(%s, 'pgstac_ingest', 'MEMBER')", (api,)
            ).fetchone()[0]
            assert conn.execute(
                "SELECT pg_has_role(%s, 'pgstac_read', 'MEMBER')", (reader,)
            ).fetchone()[0]
        with psycopg.connect(_conninfo_for(dbname, proxy, passwords["proxy"])) as conn:
            conn.execute(
                "SELECT collection_id, externally_writable, archived "
                "FROM stac_higher.collection_settings"
            ).fetchall()
            conn.rollback()
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM stac_higher.collection_settings")
            conn.rollback()
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("CREATE SCHEMA t_probe")
    finally:
        with _admin_conn(dbname) as conn:
            _drop_roles(conn, (proxy, reader, api))
