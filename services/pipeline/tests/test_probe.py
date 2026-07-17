"""probe layer: run_adapter_test orchestration + evaluate_test_outcome (pure)."""

from __future__ import annotations

import json

from pipeline.connections import build as build_mod
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.probe import evaluate_test_outcome, run_adapter_test
from pipeline.connections.repo import ConnectionRow

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})


def _conn(protocol="sftp", host_key=None, creds=None, config=None) -> ConnectionRow:
    creds = creds or {"username": "u", "password": "p"}
    envelope = seal(json.dumps(creds), KEY)
    return ConnectionRow(
        id="c1",
        name="n",
        protocol=protocol,
        config=config or {"host": "h"},
        credentials=envelope,
        host_key=host_key,
    )


# -- evaluate_test_outcome (pure) ------------------------------------------- #


def test_outcome_non_ssh_ok():
    o = evaluate_test_outcome("s3", None, {"ok": True, "message": "up"})
    assert o.ok and o.connection_status == "ok" and o.last_error is None
    assert o.host_key_to_pin is None


def test_outcome_failure_maps_to_error():
    o = evaluate_test_outcome("ftp", None, {"ok": False, "message": "no route"})
    assert not o.ok and o.connection_status == "error"
    assert o.last_error == "no route"


def test_outcome_ssh_first_pin():
    o = evaluate_test_outcome(
        "sftp", None, {"ok": True, "message": "ok", "host_key": "ssh-ed25519 KEYA"}
    )
    assert o.ok and o.connection_status == "ok"
    assert o.host_key_to_pin == "ssh-ed25519 KEYA"


def test_outcome_ssh_match_does_not_repin():
    o = evaluate_test_outcome(
        "sftp", "ssh-ed25519 KEYA", {"ok": True, "message": "ok", "host_key": "ssh-ed25519 KEYA"}
    )
    assert o.ok and o.host_key_to_pin is None


def test_outcome_ssh_mismatch_hard_fails():
    o = evaluate_test_outcome(
        "sftp", "ssh-ed25519 KEYA", {"ok": True, "message": "ok", "host_key": "ssh-ed25519 KEYB"}
    )
    assert not o.ok
    assert o.connection_status == "error"
    assert o.host_key_to_pin is None
    assert o.result["ok"] is False
    assert "mismatch" in o.result["message"].lower()


def test_outcome_ssh_missing_host_key_fails():
    o = evaluate_test_outcome("ssh", None, {"ok": True, "message": "ok"})
    assert not o.ok and o.connection_status == "error"


# -- run_adapter_test orchestration ----------------------------------------- #


class _StubAdapter:
    def __init__(self, result):
        self._result = result

    async def test(self):
        return self._result


async def test_run_adapter_test_decrypts_and_calls_adapter(monkeypatch):
    captured = {}

    def _fake_adapter_for(row, creds, allow_hosts=frozenset()):
        captured["creds"] = creds
        captured["allow_hosts"] = allow_hosts
        return _StubAdapter({"ok": True, "message": "reachable"})

    monkeypatch.setattr(build_mod, "adapter_for", _fake_adapter_for)
    conn = _conn(creds={"username": "alice", "password": "s3cr3t"})
    result = await run_adapter_test(conn, KEY, frozenset({"h"}))

    assert result["ok"] is True
    assert captured["creds"] == {"username": "alice", "password": "s3cr3t"}
    assert captured["allow_hosts"] == frozenset({"h"})


async def test_run_adapter_test_bad_key_reported_not_raised():
    conn = _conn()
    wrong = load_master_key(
        {"CREDENTIALS_MASTER_KEY": "//////////////////////////////////////////8="}
    )
    result = await run_adapter_test(conn, wrong, frozenset())
    assert result["ok"] is False
    assert "decryption failed" in result["message"]


async def test_run_adapter_test_no_credentials():
    conn = ConnectionRow(
        id="c", name="n", protocol="s3", config={"bucket": "b"}, credentials=None, host_key=None
    )
    result = await run_adapter_test(conn, KEY, frozenset())
    assert result["ok"] is False
    assert "no stored credentials" in result["message"]


async def test_run_adapter_test_never_leaks_credentials_on_adapter_error(monkeypatch):
    def _boom_adapter_for(row, creds, allow_hosts=frozenset()):
        class _Boom:
            async def test(self):
                raise RuntimeError("secret-should-not-appear s3cr3t")

        return _Boom()

    monkeypatch.setattr(build_mod, "adapter_for", _boom_adapter_for)
    conn = _conn(creds={"username": "u", "password": "s3cr3t"})
    result = await run_adapter_test(conn, KEY, frozenset())
    assert result["ok"] is False
    # message is the error TYPE only — no credential material echoed.
    assert "s3cr3t" not in result["message"]
    assert result["message"] == "test error: RuntimeError"
