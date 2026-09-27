"""The kind-2 bootstrap (container-images spec §3.1).

A kind-2 image carries its own Python and libraries; the platform injects
its runner and the revision's code through the environment, never a mount.
These tests run the bootstrap for real, in a subprocess, with this
interpreter standing in for the image's python3.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from pipeline.process.docker_executor import CODE_ENV_VAR, encode_code
from pipeline.process.runner_source import (
    BOOTSTRAP,
    BOOTSTRAP_ENTRYPOINT,
    RUNNER_ENV_VAR,
    RUNNER_SOURCE_PATH,
    runner_source_b64,
)

REPO = Path(__file__).resolve().parents[3]
RUNTIME_ENTRYPOINT = REPO / "services" / "process-runtime" / "entrypoint.py"


def test_the_shipped_runner_is_byte_identical_to_the_runtime_images():
    """Kind 2 must behave exactly as the platform image does (exit codes
    0/1/2, the code popped from the environment). A drifted copy would make
    the same revision behave differently on a user image."""
    ours = hashlib.sha256(RUNNER_SOURCE_PATH.read_bytes()).hexdigest()
    theirs = hashlib.sha256(RUNTIME_ENTRYPOINT.read_bytes()).hexdigest()
    assert ours == theirs, (
        "services/pipeline/src/pipeline/process/runtime_entrypoint.py.txt drifted from "
        "services/process-runtime/entrypoint.py; re-copy it with cp"
    )


def test_the_bootstrap_is_one_python3_line_naming_the_runner_variable():
    assert BOOTSTRAP_ENTRYPOINT == ("python3", "-c", BOOTSTRAP)
    assert "\n" not in BOOTSTRAP
    assert RUNNER_ENV_VAR in BOOTSTRAP


def _bootstrap(tmp_path, *, code: str | None):
    env = {"PATH": os.environ.get("PATH", ""), RUNNER_ENV_VAR: runner_source_b64()}
    if code is not None:
        env[CODE_ENV_VAR] = encode_code(code)
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        check=False,
    )


def test_the_bootstrap_runs_the_code_and_hides_both_payloads(tmp_path):
    code = (
        "import os\n"
        "print('hello from kind 2')\n"
        "print(sorted(k for k in os.environ if k.startswith('STAC_HIGHER')))\n"
    )
    done = _bootstrap(tmp_path, code=code)
    assert done.returncode == 0, done.stderr
    assert "hello from kind 2" in done.stdout
    # The runner popped itself and the runner popped the code: a process that
    # dumps its environment prints neither back into its log.
    assert "[]" in done.stdout


def test_a_raising_process_exits_1_with_its_traceback(tmp_path):
    done = _bootstrap(tmp_path, code="raise ValueError('boom')")
    assert done.returncode == 1
    assert "ValueError: boom" in done.stderr


def test_missing_code_is_the_platform_error_exit_2(tmp_path):
    done = _bootstrap(tmp_path, code=None)
    assert done.returncode == 2
