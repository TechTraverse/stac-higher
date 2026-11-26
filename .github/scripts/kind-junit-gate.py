"""The kind leg's gate on its own JUnit XML (K-6, plan K6-R16).

Usage: python3 -I kind-junit-gate.py <junit.xml> <min-tests> [<summary.md>]

Fails unless at least <min-tests> tests ran, none failed or errored, and
nothing was skipped (an xfail is not a skip: the lead may mark a test
`xfail(strict=True)` while its fix/ PR lands). Prints every failure's first
lines, and appends a table to <summary.md> (the job summary) when given."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(argv: list[str]) -> int:
    path, minimum = argv[1], int(argv[2])
    summary = argv[3] if len(argv) > 3 else None
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    rows: list[tuple[str, str, str]] = []
    counts = {"passed": 0, "failed": 0, "skipped": 0, "xfail": 0}
    for case in cases:
        name = f"{case.get('classname', '').rsplit('.', 1)[-1]}::{case.get('name')}"
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            counts["failed"] += 1
            text = (failure.get("message") or failure.text or "").strip()
            rows.append(("FAILED", name, text.splitlines()[0][:400] if text else ""))
        elif skipped is not None and skipped.get("type") == "pytest.xfail":
            counts["xfail"] += 1
            rows.append(("xfail", name, (skipped.get("message") or "")[:400]))
        elif skipped is not None:
            counts["skipped"] += 1
            rows.append(("SKIPPED", name, (skipped.get("message") or "")[:400]))
        else:
            counts["passed"] += 1
    problems = []
    if len(cases) < minimum:
        problems.append(f"{len(cases)} tests ran; expected at least {minimum} (KIND_MIN_TESTS)")
    if counts["skipped"]:
        problems.append(f"{counts['skipped']} skipped: the kind suite must never skip")
    if counts["failed"]:
        problems.append(f"{counts['failed']} failed")
    line = (f"kind suite: {len(cases)} tests — {counts['passed']} passed, "
            f"{counts['failed']} failed, {counts['skipped']} skipped, {counts['xfail']} xfail")
    print(line)
    for status, name, text in rows:
        print(f"  {status:8} {name}: {text}")
    if summary:
        with open(summary, "a") as out:
            out.write(f"### {line}\n\n")
            if rows:
                out.write("| result | test | first line |\n|---|---|---|\n")
                for status, name, text in rows:
                    cell = text.replace("|", "\\|").replace("\n", " ")
                    out.write(f"| {status} | `{name}` | {cell} |\n")
            for problem in problems:
                out.write(f"\n**{problem}**\n")
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
