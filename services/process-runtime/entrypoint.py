"""Entrypoint for the platform process-runtime image (ADR 0013, spec §4).

Decodes the revision's source from the environment, executes it, and exits
with a status the executor can read. It is deliberately thin: everything
security-relevant (limits, network, credentials, reaping) is enforced OUTSIDE
this process, by the executor, because code running here is the untrusted
party and must not be able to weaken its own boundary.

Exit codes: 0 success, 1 user code raised, 2 the platform could not hand the
run anything to execute (a platform bug, not a user error — worth telling
apart in the ledger).
"""

from __future__ import annotations

import base64
import os
import sys
import traceback

CODE_ENV_VAR = "STAC_HIGHER_PROCESS_CODE_B64"

EXIT_USER_ERROR = 1
EXIT_PLATFORM_ERROR = 2


def load_code() -> str:
    encoded = os.environ.get(CODE_ENV_VAR)
    if not encoded:
        print(f"[platform] {CODE_ENV_VAR} is not set", file=sys.stderr)
        raise SystemExit(EXIT_PLATFORM_ERROR)
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        print(f"[platform] could not decode process code: {err}", file=sys.stderr)
        raise SystemExit(EXIT_PLATFORM_ERROR) from err


def main() -> int:
    source = load_code()
    # Drop the encoded source from the environment before user code runs:
    # a process that dumps os.environ for debugging should not print its own
    # body back into the captured log.
    os.environ.pop(CODE_ENV_VAR, None)

    namespace: dict[str, object] = {"__name__": "__main__"}
    try:
        exec(compile(source, "<process>", "exec"), namespace)  # noqa: S102
    except SystemExit as err:
        return int(err.code or 0)
    except BaseException:  # noqa: BLE001 - the run's failure is its result
        # The traceback is the operator's primary debugging tool, and it is
        # captured to the run log rather than the pipeline's logs.
        traceback.print_exc()
        return EXIT_USER_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
