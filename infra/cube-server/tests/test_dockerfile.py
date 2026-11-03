"""The image must ship the package source it was built from.

``uv sync --no-editable`` builds the project into a wheel, and uv caches a
local project's wheel keyed on ``pyproject.toml`` alone. Behind the Dockerfile's
``/root/.cache/uv`` cache mount, a rebuild after a code change then installs the
OLD cached wheel: CI (no persistent cache) never sees it, a laptop does. Found
in the Z-7 live check, where the stack ran pre-review code.
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"


def _project_sync_lines() -> list[str]:
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    return [
        line
        for line in text.splitlines()
        if "uv sync" in line and "--no-install-project" not in line
    ]


def test_project_install_rebuilds_the_package_every_time():
    lines = _project_sync_lines()
    assert lines, "no project-installing `uv sync` in the Dockerfile"
    for line in lines:
        assert "--reinstall-package stac-higher-cube-server" in line, line
