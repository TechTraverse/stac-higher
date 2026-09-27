"""The kind-2 bootstrap (C-2, container-images spec §3.1).

``inline_python_on_image`` runs the revision's code on a USER image that
carries no platform package. The platform's runner (the platform runtime
image's ``entrypoint.py``) travels the same way the code does, in the
environment, because the executor never mounts anything (ADR 0013):

- ``STAC_HIGHER_RUNNER_B64`` is base64 of the runner's source.
- The executor sets ``Entrypoint: ["python3", "-c", BOOTSTRAP]``. The one
  line pops the runner from the environment, decodes it and executes it as
  ``__main__``; the runner then pops ``STAC_HIGHER_PROCESS_CODE_B64`` and
  behaves exactly as on the platform image (exit codes 0/1/2).

The runner source ships as ``runtime_entrypoint.py.txt`` beside this
module, a byte-identical copy that ``tests/test_runner_bootstrap.py`` pins to
``services/process-runtime/entrypoint.py`` by sha256. It is a data file on
purpose: the pipeline never imports it.

Author requirement (``docs/processes.md``): ``python3`` >= 3.10 on PATH.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

RUNNER_ENV_VAR = "STAC_HIGHER_RUNNER_B64"
RUNNER_SOURCE_PATH = Path(__file__).with_name("runtime_entrypoint.py.txt")

#: One line, no shell. The filename given to compile() is the platform
#: image's path, so a traceback reads the same on both images.
BOOTSTRAP = (
    "import base64,os;"
    "s=base64.b64decode(os.environ.pop('STAC_HIGHER_RUNNER_B64')).decode('utf-8');"
    "exec(compile(s,'/opt/stac-higher/entrypoint.py','exec'),{'__name__':'__main__'})"
)
BOOTSTRAP_ENTRYPOINT = ("python3", "-c", BOOTSTRAP)


@lru_cache(maxsize=1)
def runner_source_b64() -> str:
    """Base64 of the runner, read once per process."""
    return base64.b64encode(RUNNER_SOURCE_PATH.read_bytes()).decode("ascii")
