"""Build-time smoke test for the stactools runtime image (spec §6).

Registry-driven: for every entry, import the package module (the string
both readers derive from ``package``), import its adapter, and check the
installed distribution's version equals the registry's pin — stronger than
the text pin check, because it sees what pip actually resolved. Any failure
fails the image build, not a run.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
import warnings

from stac_higher_stactools.registry import load_registry, registry_path


def main() -> int:
    warnings.simplefilter("ignore")
    where = registry_path()
    entries = load_registry(where)
    print(f"registry: {where} ({len(entries)} entries)")
    failures = 0
    for entry in entries:
        problems: list[str] = []
        for module in (entry.module, entry.adapter_module):
            try:
                importlib.import_module(module)
            except Exception as exc:  # reported, then the build fails
                problems.append(f"import {module}: {type(exc).__name__}: {exc}")
        try:
            installed = importlib.metadata.version(entry.package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != entry.version:
            problems.append(f"installed {installed!r}, registry pins {entry.version!r}")
        status = "ok" if not problems else "FAIL"
        print(f"  {status:4} {entry.id:28} {entry.package}=={entry.version} -> {entry.module}")
        for problem in problems:
            print(f"       {problem}")
        failures += bool(problems)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
