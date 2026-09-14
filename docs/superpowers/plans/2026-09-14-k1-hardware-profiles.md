# K-1 · Hardware-profile contract + runtime `hardware` block Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hardware becomes a named **profile** plus CPU/memory/GPU counts within its bounds — a JSON profile document both runtimes read, a `hardware {profile, cpu, gpu_count}` block on every process runtime (absent ⇒ `standard`), bounds enforced at the app's write gate AND independently at launch (an unknown profile or an out-of-bounds number is a `dead` run), `RunSpec` carrying `cpu`/`gpu_count`/`profile`/`priority`, and `GET /api/processes/hardware-profiles` for the UI. No executor behaviour changes.

**Architecture:** `tests/contract-fixtures/hardware-profiles.json` is the golden contract (a sample profile set + parser cases + bounds cases both suites run); `infra/hardware-profiles/local.json` is the deployment default set, reached by each image through a second named build context (`hardware`) exactly like `builtin-extractors.json` reaches them through `fixtures`, and published as `PROCESS_HARDWARE_PROFILES_FILE`. The pipeline gets `process/hardware.py` (a `builtin.py`-shaped loader: env path, checkout fallback, lenient reader) and `check_hardware_bounds()` beside `check_network_cap()` in `launch.py`; `parse_process_runtime` reads the block shape-only and flattens it onto `ProcessRuntime`. The app gets `lib/processes/hardware.ts` (Zod reader, request-time file read cached per process lifetime, a pure `hardwareBoundsError()` the deploy route calls the way it calls `networkLevelWithinCap()`), `runtimeLimits.hardware` with the same `.default(() => …)` idiom as `retry`/`network`, and the route.

**Tech Stack:** Astro 7 + React 19 (routes only — no UI this slice), Zod 4, vitest; Python 3.12 dataclasses, pytest; Docker named build contexts (compose `additional_contexts`, CI `build-contexts`).

**Spec:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §3 (profiles: §3.2 document, §3.3 app exposure, §3.4 backend block), §4 (runtime contract change), §13 decisions 1–2, §14 K-1; ADR 0019 (`docs/decisions/0019-process-compute-kubernetes-kueue.md`); `TODO.md` K queue, K-1 bullet.

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/k1-hardware -b ai/k1-hardware ai/main`, then `npm install` at the worktree root (app tasks) — pipeline tasks also run from it.
- **Gates:** app tasks: `npm run verify` from the worktree root before each commit; pipeline tasks: `uv run pytest -q` and `uv run ruff check .` from `services/pipeline/`; a task that touches both runs both. Teammates never run e2e, the dev server, or Docker (Docker image builds included — the lead builds after merge).
- **The profile document (spec §3.2), verbatim shape:** `{ "version": 1, "profiles": [ { id, label, description, tier ("cpu" | "cpu-large" | "gpu"), accelerator (null | {vendor, model, memory_gb}), cpu {min, max, default} (cores, fractional ok), memory_mb {min, max, default}, gpu_count (null | {min, max, default}), max_queue_wait_seconds, image (null | string), backend {…} } ] }`. Rules: exactly one profile has `"id": "standard"`; ids are unique and match `^[a-z0-9]+(?:-[a-z0-9]+)*$`; bounds are inclusive; `min <= default <= max`; `gpu_count` is non-null iff `accelerator` is non-null; the app IGNORES `backend` (reads it as an opaque object, never returns it); the pipeline keeps it opaque (`dict`) until K-3/K-5 consume it.
- **The runtime block (spec §4), verbatim:** `"hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }`. **Lenient reader on both sides:** an absent block parses as `{profile: "standard", cpu: 1, gpu_count: 0}` — the constant `1` is `standard.cpu.default` in every shipped set and the fixture pins that equality (`DEFAULT_HARDWARE_CPU` on both sides). `memory_mb` and `timeout_seconds` stay top-level; the profile bounds `memory_mb` too.
- **Dual enforcement (spec §4, the `PROCESS_NETWORK_MAX` pattern):** the deploy route rejects with **400 `{ error }`** (the route's own convention — NOT `{ error, code }`, which is authz-only) naming the bound; the pipeline re-validates at launch and a violation is a `dead` run whose `error` names the bound (`HardwareProfileError`, beside `NetworkCapExceeded`). Bounds messages, one format both sides use: `hardware.profile 'X' is not a hardware profile of this deployment` / `hardware.cpu N is outside profile 'P' bounds MIN–MAX` / `hardware.gpu_count N is outside profile 'P' bounds MIN–MAX` / `hardware.gpu_count must be 0: profile 'P' has no accelerator` / `memory_mb N is outside profile 'P' bounds MIN–MAX`.
- **`RunSpec`** gains `cpu: float = 1.0`, `gpu_count: int = 0`, `profile: HardwareProfile | None = None`, `priority: str = "triggered"` (`"interactive"` for a test run — `QueuedRun.is_test`; `"triggered"` otherwise). Defaults keep every existing `RunSpec(...)` construction valid. **No executor change:** `DockerExecutor` does not read the new fields (K-3 does).
- **`GET /api/processes/hardware-profiles`** — member+ (any authenticated identity; GETs are ungated by `matchGatedRoute`, so the in-route `locals.auth?.authenticated` check is the whole gate), response `{ "backend": "docker", "profiles": [ …profiles minus `backend` ] }`. `"docker"` is a constant this slice (K-5 makes it `PROCESS_EXECUTOR`).
- **Cross-runtime contract rule (AGENTS.md):** the new shape gets `tests/contract-fixtures/hardware-profiles.json`, consumed by BOTH suites; `process-runtime.json` gains `hardware` in both variants' `defaults` and new `cases`.
- **No new dependency; never edit `components/ui/`;** app imports shared code from `@stac-higher/shared`; the app reads the profile file with `node:fs` `readFileSync` (Node SSR — this is the first request-time file read in `app/src`; cache per process lifetime keyed by the path). Structured logging in the pipeline: data in `extra={...}`, no reserved LogRecord keys. ADR 0001: no DDL (K-3 owns migration 029).
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 0: Precondition

- [ ] `git log --oneline ai/main -25 | grep -i 'v-4\|V-4'` shows the V-4 merge (the K queue starts after the V queue), and `grep -n 'is_test' services/pipeline/src/pipeline/process/repo.py` prints the `QueuedRun` field. If not, STOP and report.

---

### Task 1: The contract — fixture, the local profile set, the pipeline reader

**Files:**
- Create: `tests/contract-fixtures/hardware-profiles.json`
- Modify: `tests/contract-fixtures/README.md` — a new `## Additional fixture styles (K queue, K-1)` section at the end
- Create: `infra/hardware-profiles/local.json`
- Create: `services/pipeline/src/pipeline/process/hardware.py`
- Modify: `services/pipeline/src/pipeline/config.py` — docstring bullet, constant, `Settings` field, `from_env` kwarg (anchor: the `PROCESS_NETWORK_MAX` lines)
- Test: `services/pipeline/tests/test_process_hardware.py` (new), `services/pipeline/tests/test_contract_fixtures.py` (append), `services/pipeline/tests/test_config.py` (append)

**Interfaces:**
- Produces (pipeline):
  ```python
  # pipeline/process/hardware.py
  class HardwareProfileError(ValueError): ...
  @dataclass(frozen=True)
  class Bounds: min: float; max: float; default: float
  @dataclass(frozen=True)
  class HardwareProfile:
      id: str; label: str; description: str; tier: str
      accelerator: dict[str, Any] | None
      cpu: Bounds; memory_mb: Bounds; gpu_count: Bounds | None
      max_queue_wait_seconds: int; image: str | None
      backend: dict[str, Any]            # opaque until K-3/K-5
  @dataclass(frozen=True)
  class HardwareProfileSet:
      version: int; profiles: tuple[HardwareProfile, ...]
      def get(self, profile_id: str) -> HardwareProfile | None
      @property
      def standard(self) -> HardwareProfile
  DEFAULT_HARDWARE_PROFILE = "standard"
  DEFAULT_HARDWARE_CPU = 1.0
  DEFAULT_HARDWARE_GPU_COUNT = 0
  PROFILES_ENV_VAR = "PROCESS_HARDWARE_PROFILES_FILE"
  def parse_hardware_profiles(document: Any) -> HardwareProfileSet
  def hardware_profiles_path(env: dict[str, str] | None = None) -> Path   # env var, else <checkout>/infra/hardware-profiles/local.json
  def load_hardware_profiles(path: Path | None = None) -> HardwareProfileSet
  def check_hardware_bounds(*, profiles: HardwareProfileSet, profile_id: str, cpu: float, gpu_count: int, memory_mb: int) -> HardwareProfile   # returns the resolved profile or raises HardwareProfileError
  # pipeline/config.py
  DEFAULT_PROCESS_HARDWARE_PROFILES_FILE: str | None = None
  Settings.process_hardware_profiles_file: str | None   # env PROCESS_HARDWARE_PROFILES_FILE; None ⇒ checkout fallback
  ```
- Produces (fixture): `tests/contract-fixtures/hardware-profiles.json` with top-level `document` (the sample set below), `cases[]` (`{name, document, app, pipeline}` over the profile-set parsers) and `bounds_cases[]` (`{name, hardware: {profile, cpu, gpu_count}, memory_mb, app, pipeline, reason}` evaluated against `document`).

- [ ] **Step 1: The fixture.** Create `tests/contract-fixtures/hardware-profiles.json`:

```json
{
  "style": "document",
  "$comment": "K-1 (process-compute spec §3.2/§4): the hardware-profile document both runtimes read, plus the bounds both write gates enforce. `document` is a SAMPLE deployment set (a CPU profile and a GPU profile) used by `bounds_cases`; the shipped sets live in infra/hardware-profiles/. `cases` exercise the profile-set parsers (app: app/src/lib/processes/hardware.ts; pipeline: pipeline/process/hardware.py); `bounds_cases` exercise hardwareBoundsError() / check_hardware_bounds() against `document`. app/pipeline are accept|reject.",
  "document": {
    "version": 1,
    "profiles": [
      {
        "id": "standard",
        "label": "Standard",
        "description": "General purpose. Metadata extraction, small rasters.",
        "tier": "cpu",
        "accelerator": null,
        "cpu": { "min": 0.25, "max": 4, "default": 1 },
        "memory_mb": { "min": 128, "max": 16384, "default": 512 },
        "gpu_count": null,
        "max_queue_wait_seconds": 1800,
        "image": null,
        "backend": { "docker": { "device_requests": [], "capacity": 2 } }
      },
      {
        "id": "gpu-l4",
        "label": "GPU — NVIDIA L4 (24 GB)",
        "description": "Single L4. Inference, cupy/numba kernels, medium models.",
        "tier": "gpu",
        "accelerator": { "vendor": "nvidia", "model": "l4", "memory_gb": 24 },
        "cpu": { "min": 2, "max": 16, "default": 4 },
        "memory_mb": { "min": 4096, "max": 65536, "default": 16384 },
        "gpu_count": { "min": 1, "max": 1, "default": 1 },
        "max_queue_wait_seconds": 7200,
        "image": "process-runtime-cuda",
        "backend": {
          "kubernetes": {
            "queue": "process-runs",
            "node_selector": { "stac-higher.io/flavor": "gpu-l4" },
            "tolerations": [ { "key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule" } ],
            "runtime_class": null,
            "extended_resources": { "nvidia.com/gpu": "gpu_count" }
          },
          "docker": { "device_requests": [ { "Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]] } ], "capacity": 1 }
        }
      }
    ]
  },
  "cases": [
    { "name": "the sample document", "document": "$document", "app": "accept", "pipeline": "accept" },
    { "name": "a set with only standard", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "Standard", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "accept", "pipeline": "accept" },
    { "name": "no standard profile", "document": { "version": 1, "profiles": [ { "id": "big", "label": "Big", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "reject", "pipeline": "reject" },
    { "name": "two profiles with the same id", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "A", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} }, { "id": "standard", "label": "B", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "reject", "pipeline": "reject" },
    { "name": "default outside its bounds", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "Standard", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 2, "default": 4 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "reject", "pipeline": "reject" },
    { "name": "gpu_count without an accelerator", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "Standard", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": { "min": 1, "max": 1, "default": 1 }, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "reject", "pipeline": "reject" },
    { "name": "an accelerator without gpu_count", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "Standard", "description": "", "tier": "cpu", "accelerator": { "vendor": "nvidia", "model": "l4", "memory_gb": 24 }, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "reject", "pipeline": "reject" },
    { "name": "an unknown tier", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "Standard", "description": "", "tier": "quantum", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {} } ] }, "app": "reject", "pipeline": "reject" },
    { "name": "an unknown version", "document": { "version": 2, "profiles": [] }, "app": "reject", "pipeline": "reject" },
    { "name": "an extra key on a profile (lenient pipeline, strict app)", "document": { "version": 1, "profiles": [ { "id": "standard", "label": "Standard", "description": "", "tier": "cpu", "accelerator": null, "cpu": { "min": 1, "max": 1, "default": 1 }, "memory_mb": { "min": 128, "max": 512, "default": 512 }, "gpu_count": null, "max_queue_wait_seconds": 60, "image": null, "backend": {}, "colour": "blue" } ] }, "app": "reject", "pipeline": "accept" }
  ],
  "bounds_cases": [
    { "name": "standard at its defaults", "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }, "memory_mb": 512, "app": "accept", "pipeline": "accept", "reason": "" },
    { "name": "standard at both cpu bounds", "hardware": { "profile": "standard", "cpu": 0.25, "gpu_count": 0 }, "memory_mb": 16384, "app": "accept", "pipeline": "accept", "reason": "bounds are inclusive" },
    { "name": "cpu above standard's max", "hardware": { "profile": "standard", "cpu": 4.5, "gpu_count": 0 }, "memory_mb": 512, "app": "reject", "pipeline": "reject", "reason": "hardware.cpu 4.5 is outside profile 'standard' bounds 0.25–4" },
    { "name": "cpu below standard's min", "hardware": { "profile": "standard", "cpu": 0.1, "gpu_count": 0 }, "memory_mb": 512, "app": "reject", "pipeline": "reject", "reason": "hardware.cpu 0.1 is outside profile 'standard' bounds 0.25–4" },
    { "name": "memory above standard's max", "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }, "memory_mb": 32768, "app": "reject", "pipeline": "reject", "reason": "memory_mb 32768 is outside profile 'standard' bounds 128–16384" },
    { "name": "a gpu on a cpu profile", "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 1 }, "memory_mb": 512, "app": "reject", "pipeline": "reject", "reason": "hardware.gpu_count must be 0: profile 'standard' has no accelerator" },
    { "name": "gpu-l4 at its defaults", "hardware": { "profile": "gpu-l4", "cpu": 4, "gpu_count": 1 }, "memory_mb": 16384, "app": "accept", "pipeline": "accept", "reason": "" },
    { "name": "gpu-l4 without a gpu", "hardware": { "profile": "gpu-l4", "cpu": 4, "gpu_count": 0 }, "memory_mb": 16384, "app": "reject", "pipeline": "reject", "reason": "hardware.gpu_count 0 is outside profile 'gpu-l4' bounds 1–1" },
    { "name": "gpu-l4 with two gpus", "hardware": { "profile": "gpu-l4", "cpu": 4, "gpu_count": 2 }, "memory_mb": 16384, "app": "reject", "pipeline": "reject", "reason": "hardware.gpu_count 2 is outside profile 'gpu-l4' bounds 1–1" },
    { "name": "gpu-l4 below its memory floor", "hardware": { "profile": "gpu-l4", "cpu": 4, "gpu_count": 1 }, "memory_mb": 512, "app": "reject", "pipeline": "reject", "reason": "memory_mb 512 is outside profile 'gpu-l4' bounds 4096–65536" },
    { "name": "an unknown profile", "hardware": { "profile": "tpu-v5", "cpu": 1, "gpu_count": 0 }, "memory_mb": 512, "app": "reject", "pipeline": "reject", "reason": "hardware.profile 'tpu-v5' is not a hardware profile of this deployment" }
  ]
}
```
(`"document": "$document"` in a case means "use the top-level `document`" — both loaders substitute it; the README section documents the convention.) Note the one asymmetric case: the app's Zod reader is `.strict()` (a typo in a deployment file fails loudly at the route), the pipeline's reader is lenient (an image built before a key was added must not brick) — the same direction as `builtin-extractors.json`.

README section (append to `tests/contract-fixtures/README.md`):
```markdown
## Additional fixture styles (K queue, K-1)

- `hardware-profiles.json` — style `document`. `document` is a sample
  hardware-profile set (process-compute spec §3.2); `cases[]` are
  `{ name, document, app, pipeline }` over the profile-set parsers
  (`app/src/lib/processes/hardware.ts` `hardwareProfileSetSchema`, strict;
  `pipeline/process/hardware.py` `parse_hardware_profiles`, lenient — the
  established direction), where `"document": "$document"` means the
  top-level sample; `bounds_cases[]` are `{ name, hardware, memory_mb, app,
  pipeline, reason }` evaluated against the sample by
  `hardwareBoundsError()` / `check_hardware_bounds()` — `reason` is the
  message both sides produce (empty on accept). The deployment sets are
  NOT fixtures: `infra/hardware-profiles/{local,kind,eks}.json`.
```

- [ ] **Step 2: The local deployment set.** Create `infra/hardware-profiles/local.json` (Docker Desktop has no GPU, so two CPU profiles; `cpu-large` has capacity 1 so K-3's `pending_capacity` path is exercisable locally):

```json
{
  "version": 1,
  "profiles": [
    {
      "id": "standard",
      "label": "Standard",
      "description": "General purpose. Metadata extraction, small rasters.",
      "tier": "cpu",
      "accelerator": null,
      "cpu": { "min": 0.25, "max": 4, "default": 1 },
      "memory_mb": { "min": 128, "max": 16384, "default": 512 },
      "gpu_count": null,
      "max_queue_wait_seconds": 1800,
      "image": null,
      "backend": { "docker": { "device_requests": [], "capacity": 2 } }
    },
    {
      "id": "cpu-large",
      "label": "CPU — large",
      "description": "Many cores and memory. Mosaics, reprojection, big rasters.",
      "tier": "cpu-large",
      "accelerator": null,
      "cpu": { "min": 2, "max": 8, "default": 4 },
      "memory_mb": { "min": 4096, "max": 32768, "default": 8192 },
      "gpu_count": null,
      "max_queue_wait_seconds": 3600,
      "image": null,
      "backend": { "docker": { "device_requests": [], "capacity": 1 } }
    }
  ]
}
```

- [ ] **Step 3: Failing tests.** Create `services/pipeline/tests/test_process_hardware.py`:

```python
"""Hardware profiles (K-1, process-compute spec §3): the reader, the checkout
fallback, and the bounds check both write gates share."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.process.hardware import (
    DEFAULT_HARDWARE_CPU,
    DEFAULT_HARDWARE_GPU_COUNT,
    DEFAULT_HARDWARE_PROFILE,
    PROFILES_ENV_VAR,
    HardwareProfileError,
    check_hardware_bounds,
    hardware_profiles_path,
    load_hardware_profiles,
    parse_hardware_profiles,
)

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures" / "hardware-profiles.json"
LOCAL_SET = Path(__file__).resolve().parents[3] / "infra" / "hardware-profiles" / "local.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_the_sample_document():
    profiles = parse_hardware_profiles(_fixture()["document"])
    assert profiles.version == 1
    assert [p.id for p in profiles.profiles] == ["standard", "gpu-l4"]
    standard = profiles.standard
    assert standard.cpu.default == DEFAULT_HARDWARE_CPU == 1.0
    assert standard.memory_mb.default == 512
    assert standard.gpu_count is None and standard.accelerator is None
    gpu = profiles.get("gpu-l4")
    assert gpu is not None and gpu.gpu_count is not None and gpu.gpu_count.max == 1
    assert gpu.accelerator == {"vendor": "nvidia", "model": "l4", "memory_gb": 24}
    assert gpu.image == "process-runtime-cuda"
    # backend stays opaque — K-3/K-5 read it
    assert gpu.backend["kubernetes"]["queue"] == "process-runs"
    assert profiles.get("nope") is None


def test_default_constants_match_the_shipped_standard_profile():
    """Both readers default an absent `hardware` block to these; the shipped
    sets must agree or a stored revision without the block would launch
    outside the bounds the UI shows."""
    for path in (FIXTURE, LOCAL_SET):
        doc = json.loads(path.read_text())
        doc = doc["document"] if "document" in doc else doc
        standard = parse_hardware_profiles(doc).standard
        assert standard.id == DEFAULT_HARDWARE_PROFILE == "standard"
        assert standard.cpu.default == DEFAULT_HARDWARE_CPU
        assert DEFAULT_HARDWARE_GPU_COUNT == 0 and standard.gpu_count is None


def test_the_local_set_parses():
    profiles = load_hardware_profiles(LOCAL_SET)
    assert [p.id for p in profiles.profiles] == ["standard", "cpu-large"]
    assert profiles.get("cpu-large").backend["docker"]["capacity"] == 1


def test_path_prefers_the_env_var_then_the_checkout(tmp_path):
    custom = tmp_path / "profiles.json"
    assert hardware_profiles_path({PROFILES_ENV_VAR: str(custom)}) == custom
    assert hardware_profiles_path({}) == LOCAL_SET


def test_load_reports_an_unreadable_file(tmp_path):
    with pytest.raises(HardwareProfileError, match="could not read"):
        load_hardware_profiles(tmp_path / "missing.json")


def test_bounds_returns_the_resolved_profile():
    profiles = parse_hardware_profiles(_fixture()["document"])
    profile = check_hardware_bounds(
        profiles=profiles, profile_id="gpu-l4", cpu=4, gpu_count=1, memory_mb=16384
    )
    assert profile.id == "gpu-l4"


@pytest.mark.parametrize(
    "case", json.loads(FIXTURE.read_text())["bounds_cases"], ids=lambda c: c["name"]
)
def test_bounds_cases_from_the_fixture(case):
    profiles = parse_hardware_profiles(_fixture()["document"])
    kwargs = dict(
        profiles=profiles,
        profile_id=case["hardware"]["profile"],
        cpu=case["hardware"]["cpu"],
        gpu_count=case["hardware"]["gpu_count"],
        memory_mb=case["memory_mb"],
    )
    if case["pipeline"] == "accept":
        check_hardware_bounds(**kwargs)
    else:
        with pytest.raises(HardwareProfileError) as err:
            check_hardware_bounds(**kwargs)
        assert str(err.value) == case["reason"]
```

Append to `tests/test_contract_fixtures.py` (match the file's `_load` helper and its `_check` idiom):
```python
HARDWARE_PROFILES = _load("hardware-profiles.json")


def _hardware_document(case: dict) -> dict:
    return HARDWARE_PROFILES["document"] if case["document"] == "$document" else case["document"]


@pytest.mark.parametrize("case", HARDWARE_PROFILES["cases"], ids=lambda c: c["name"])
def test_hardware_profile_document_cases(case):
    """K-1: the profile-set reader. The lenient/strict asymmetry (an extra key
    is pipeline: accept / app: reject) is the builtin-extractors direction."""
    from pipeline.process.hardware import HardwareProfileError, parse_hardware_profiles

    document = _hardware_document(case)
    if case["pipeline"] == "accept":
        parse_hardware_profiles(document)
    else:
        with pytest.raises(HardwareProfileError):
            parse_hardware_profiles(document)
```
Append to `tests/test_config.py`:
```python
def test_hardware_profiles_file_setting():
    """K-1: unset means the repo checkout's infra/hardware-profiles/local.json."""
    assert Settings.from_env(env={}).process_hardware_profiles_file is None
    assert (
        Settings.from_env(env={"PROCESS_HARDWARE_PROFILES_FILE": "/app/share/hp.json"}).process_hardware_profiles_file
        == "/app/share/hp.json"
    )
```

- [ ] **Step 4:** `uv run pytest tests/test_process_hardware.py tests/test_contract_fixtures.py tests/test_config.py -q` → FAIL (ModuleNotFoundError / KeyError on the fixture / AttributeError).

- [ ] **Step 5: Implement `pipeline/process/hardware.py`:**

```python
"""Hardware profiles (K-1, process-compute spec §3) — the Python half.

A deployment describes the hardware a run may ask for as named PROFILES —
a CPU/memory range, an optional accelerator with a GPU-count range, a
queue-wait promise and a base image — in one JSON document both runtimes
read. The app validates it strictly at its write gate and serves it to the
UI; this reader is LENIENT in the established direction (an image built
before a key was added must not brick), keeps ``backend`` opaque until the
executors consume it (K-3 Docker, K-5 Kubernetes), and re-checks a run's
``hardware`` block against the bounds at launch, independently of the app.

**Packaging:** the deployment set reaches the image as ``COPY --from=hardware``
out of a named build context pointing at ``infra/hardware-profiles``
(compose ``additional_contexts``, CI ``build-contexts``) and the image
publishes the copy's path in ``PROCESS_HARDWARE_PROFILES_FILE``; unset (dev,
pytest) means the repo checkout's ``local.json``. One file, no vendored copy.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIERS = ("cpu", "cpu-large", "gpu")
DEFAULT_HARDWARE_PROFILE = "standard"
#: What an absent `hardware` block means on BOTH sides — must equal the
#: shipped sets' `standard.cpu.default` (tests pin it).
DEFAULT_HARDWARE_CPU = 1.0
DEFAULT_HARDWARE_GPU_COUNT = 0
PROFILES_ENV_VAR = "PROCESS_HARDWARE_PROFILES_FILE"
_CHECKOUT_PROFILES = Path(__file__).resolve().parents[5] / "infra" / "hardware-profiles" / "local.json"
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class HardwareProfileError(ValueError):
    """A profile document the reader cannot use, or a run outside its profile's bounds."""


@dataclass(frozen=True)
class Bounds:
    min: float
    max: float
    default: float


@dataclass(frozen=True)
class HardwareProfile:
    id: str
    label: str
    description: str
    tier: str
    accelerator: dict[str, Any] | None
    cpu: Bounds
    memory_mb: Bounds
    gpu_count: Bounds | None
    max_queue_wait_seconds: int
    image: str | None
    #: Pipeline-only, opaque here: K-3 reads `backend["docker"]`, K-5 `backend["kubernetes"]`.
    backend: dict[str, Any]


@dataclass(frozen=True)
class HardwareProfileSet:
    version: int
    profiles: tuple[HardwareProfile, ...]

    def get(self, profile_id: str) -> HardwareProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    @property
    def standard(self) -> HardwareProfile:
        profile = self.get(DEFAULT_HARDWARE_PROFILE)
        assert profile is not None  # parse_hardware_profiles guarantees it
        return profile


def _number(raw: Any, what: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise HardwareProfileError(f"{what} must be a number, got {raw!r}")
    return float(raw)


def _bounds(raw: Any, what: str) -> Bounds:
    if not isinstance(raw, dict):
        raise HardwareProfileError(f"{what} must be an object with min/max/default, got {raw!r}")
    b = Bounds(_number(raw.get("min"), f"{what}.min"), _number(raw.get("max"), f"{what}.max"),
               _number(raw.get("default"), f"{what}.default"))
    if not (b.min <= b.default <= b.max):
        raise HardwareProfileError(f"{what} must satisfy min <= default <= max, got {raw!r}")
    return b


def _profile(raw: Any) -> HardwareProfile:
    if not isinstance(raw, dict):
        raise HardwareProfileError(f"profile must be an object, got {raw!r}")
    profile_id = raw.get("id")
    if not isinstance(profile_id, str) or not _ID_RE.match(profile_id):
        raise HardwareProfileError(f"profile id must match {_ID_RE.pattern}, got {profile_id!r}")
    what = f"profile {profile_id!r}"
    tier = raw.get("tier")
    if tier not in TIERS:
        raise HardwareProfileError(f"{what}: tier must be one of {TIERS}, got {tier!r}")
    accelerator = raw.get("accelerator")
    if accelerator is not None and not isinstance(accelerator, dict):
        raise HardwareProfileError(f"{what}: accelerator must be null or an object")
    gpu_raw = raw.get("gpu_count")
    gpu_count = None if gpu_raw is None else _bounds(gpu_raw, f"{what}.gpu_count")
    if (accelerator is None) != (gpu_count is None):
        raise HardwareProfileError(f"{what}: gpu_count is required with an accelerator and forbidden without one")
    wait = raw.get("max_queue_wait_seconds")
    if isinstance(wait, bool) or not isinstance(wait, int) or wait < 0:
        raise HardwareProfileError(f"{what}: max_queue_wait_seconds must be a non-negative integer")
    image = raw.get("image")
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise HardwareProfileError(f"{what}: image must be null or a non-empty string")
    backend = raw.get("backend") or {}
    if not isinstance(backend, dict):
        raise HardwareProfileError(f"{what}: backend must be an object")
    return HardwareProfile(
        id=profile_id,
        label=str(raw.get("label") or profile_id),
        description=str(raw.get("description") or ""),
        tier=tier,
        accelerator=dict(accelerator) if accelerator else None,
        cpu=_bounds(raw.get("cpu"), f"{what}.cpu"),
        memory_mb=_bounds(raw.get("memory_mb"), f"{what}.memory_mb"),
        gpu_count=gpu_count,
        max_queue_wait_seconds=wait,
        image=image,
        backend=dict(backend),
    )


def parse_hardware_profiles(document: Any) -> HardwareProfileSet:
    """Read a profile document. Lenient: unknown keys are ignored; anything
    present but unusable, a missing `standard`, or a duplicate id raises."""
    if not isinstance(document, dict) or document.get("version") != 1:
        raise HardwareProfileError("hardware profiles: version must be 1")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise HardwareProfileError("hardware profiles: profiles must be a non-empty list")
    profiles = tuple(_profile(p) for p in raw_profiles)
    ids = [p.id for p in profiles]
    if len(set(ids)) != len(ids):
        raise HardwareProfileError(f"hardware profiles: duplicate ids {sorted({i for i in ids if ids.count(i) > 1})}")
    if DEFAULT_HARDWARE_PROFILE not in ids:
        raise HardwareProfileError(f"hardware profiles: exactly one profile must be {DEFAULT_HARDWARE_PROFILE!r}")
    return HardwareProfileSet(version=1, profiles=profiles)


def hardware_profiles_path(env: dict[str, str] | None = None) -> Path:
    """`PROCESS_HARDWARE_PROFILES_FILE` when set (the image), else the checkout's local set."""
    override = (os.environ if env is None else env).get(PROFILES_ENV_VAR)
    if override:
        return Path(override)
    return _CHECKOUT_PROFILES


def load_hardware_profiles(path: Path | None = None) -> HardwareProfileSet:
    where = path or hardware_profiles_path()
    try:
        document = json.loads(where.read_text())
    except (OSError, ValueError) as exc:
        raise HardwareProfileError(f"could not read the hardware profiles at {where}: {exc}") from exc
    return parse_hardware_profiles(document)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def check_hardware_bounds(
    *, profiles: HardwareProfileSet, profile_id: str, cpu: float, gpu_count: int, memory_mb: int
) -> HardwareProfile:
    """The launch-time half of the dual enforcement (spec §4). Returns the
    resolved profile; raises with the SAME message text the app's write gate
    produces (the fixture's `bounds_cases[].reason` pins both)."""
    profile = profiles.get(profile_id)
    if profile is None:
        raise HardwareProfileError(f"hardware.profile {profile_id!r} is not a hardware profile of this deployment")
    if not (profile.cpu.min <= cpu <= profile.cpu.max):
        raise HardwareProfileError(
            f"hardware.cpu {_fmt(cpu)} is outside profile {profile_id!r} bounds {_fmt(profile.cpu.min)}–{_fmt(profile.cpu.max)}"
        )
    if profile.gpu_count is None:
        if gpu_count != 0:
            raise HardwareProfileError(
                f"hardware.gpu_count must be 0: profile {profile_id!r} has no accelerator"
            )
    elif not (profile.gpu_count.min <= gpu_count <= profile.gpu_count.max):
        raise HardwareProfileError(
            f"hardware.gpu_count {gpu_count} is outside profile {profile_id!r} bounds "
            f"{_fmt(profile.gpu_count.min)}–{_fmt(profile.gpu_count.max)}"
        )
    if not (profile.memory_mb.min <= memory_mb <= profile.memory_mb.max):
        raise HardwareProfileError(
            f"memory_mb {memory_mb} is outside profile {profile_id!r} bounds "
            f"{_fmt(profile.memory_mb.min)}–{_fmt(profile.memory_mb.max)}"
        )
    return profile
```
(The `'standard'` repr in the messages uses Python's `!r` on a str → single quotes, matching the fixture's `reason` strings; the en dash is U+2013 — copy it from the fixture.)

`config.py`: docstring bullet after the `PROCESS_NETWORK_MAX` bullet (find it; if the docstring has none, add after the `ASSET_HREF_BASE` bullet): ``- ``PROCESS_HARDWARE_PROFILES_FILE`` — path of the hardware-profile document (K-1, spec §3). Unset means the repo checkout's ``infra/hardware-profiles/local.json``; the image sets it to its copy.`` Constant beside `DEFAULT_PROCESS_NETWORK_MAX`: `DEFAULT_PROCESS_HARDWARE_PROFILES_FILE: str | None = None` with a two-line comment; field after `process_network_max`: `process_hardware_profiles_file: str | None = DEFAULT_PROCESS_HARDWARE_PROFILES_FILE`; `from_env`: `process_hardware_profiles_file=env.get("PROCESS_HARDWARE_PROFILES_FILE") or None,`.

- [ ] **Step 6:** the three focused files → PASS; `uv run pytest -q`; `uv run ruff check .`.

- [ ] **Step 7: Commit** — `feat(process): hardware-profile contract — fixture, infra/hardware-profiles/local.json, the pipeline reader + bounds check (K-1)`

---

### Task 2: The app reader, the bounds check, and `GET /api/processes/hardware-profiles`

**Files:**
- Create: `app/src/lib/processes/hardware.ts`
- Create: `app/src/pages/api/processes/hardware-profiles.ts`
- Test: `app/src/__tests__/processes-hardware.test.ts` (new), `app/src/__tests__/contract-fixtures.test.ts` (append a `hardware profiles contract` block), `app/src/__tests__/api-processes.test.ts` (append two route tests)

**Interfaces:**
- Consumes: the fixture and `infra/hardware-profiles/local.json` (Task 1).
- Produces:
  ```ts
  // app/src/lib/processes/hardware.ts
  export const HARDWARE_TIERS = ["cpu", "cpu-large", "gpu"] as const;
  export const DEFAULT_HARDWARE_PROFILE = "standard";
  export const DEFAULT_HARDWARE_CPU = 1;
  export const DEFAULT_HARDWARE_GPU_COUNT = 0;
  export const hardwareBoundsSchema, hardwareProfileSchema, hardwareProfileSetSchema;   // strict Zod
  export type HardwareProfile, HardwareProfileSet, PublicHardwareProfile (= HardwareProfile minus backend);
  export function parseHardwareProfiles(document: unknown): HardwareProfileSet;          // throws ZodError-derived Error with a message
  export function hardwareProfilesPath(env = process.env): string;                       // env var, else <checkout>/infra/hardware-profiles/local.json
  export function loadHardwareProfiles(env = process.env): HardwareProfileSet;           // readFileSync + parse, cached per path
  export function resetHardwareProfilesCache(): void;                                    // tests
  export function findProfile(set: HardwareProfileSet, id: string): HardwareProfile | undefined;
  export function hardwareBoundsError(hardware: { profile: string; cpu: number; gpu_count: number }, memoryMb: number, set: HardwareProfileSet): string | null;
  export function publicProfiles(set: HardwareProfileSet): PublicHardwareProfile[];
  ```
  Route response: `200 { backend: "docker", profiles: PublicHardwareProfile[] }`; `401 authzError` when unauthenticated; `500 { error }` when the file cannot be read/parsed.

- [ ] **Step 1: Failing tests.** Create `app/src/__tests__/processes-hardware.test.ts`:

```ts
/**
 * K-1: the hardware-profile reader and the write-gate bounds check
 * (process-compute spec §3, §4). The pipeline runs the same fixture cases.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_HARDWARE_CPU,
  DEFAULT_HARDWARE_PROFILE,
  hardwareBoundsError,
  hardwareProfilesPath,
  loadHardwareProfiles,
  parseHardwareProfiles,
  publicProfiles,
  resetHardwareProfilesCache,
} from "@/lib/processes/hardware";

const fixturePath = fileURLToPath(
  new URL("../../../tests/contract-fixtures/hardware-profiles.json", import.meta.url),
);
const localPath = fileURLToPath(
  new URL("../../../infra/hardware-profiles/local.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));

afterEach(() => resetHardwareProfilesCache());

describe("parseHardwareProfiles", () => {
  it("reads the sample document and keeps backend opaque", () => {
    const set = parseHardwareProfiles(fixture.document);
    expect(set.profiles.map((p) => p.id)).toEqual(["standard", "gpu-l4"]);
    expect(set.profiles[0].cpu.default).toBe(DEFAULT_HARDWARE_CPU);
    expect(publicProfiles(set).every((p) => !("backend" in p))).toBe(true);
  });

  it("the shipped local set agrees with the defaults an absent block gets", () => {
    const set = parseHardwareProfiles(JSON.parse(readFileSync(localPath, "utf8")));
    const standard = set.profiles.find((p) => p.id === DEFAULT_HARDWARE_PROFILE);
    expect(standard?.cpu.default).toBe(DEFAULT_HARDWARE_CPU);
    expect(standard?.gpu_count).toBeNull();
  });
});

describe("loadHardwareProfiles", () => {
  it("prefers PROCESS_HARDWARE_PROFILES_FILE, then the checkout's local set", () => {
    expect(hardwareProfilesPath({ PROCESS_HARDWARE_PROFILES_FILE: "/tmp/x.json" })).toBe("/tmp/x.json");
    expect(hardwareProfilesPath({})).toBe(localPath);
  });

  it("caches per path", () => {
    const a = loadHardwareProfiles({});
    const b = loadHardwareProfiles({});
    expect(b).toBe(a);
    resetHardwareProfilesCache();
    expect(loadHardwareProfiles({})).not.toBe(a);
  });

  it("names the file when it cannot be read", () => {
    expect(() => loadHardwareProfiles({ PROCESS_HARDWARE_PROFILES_FILE: "/nonexistent/hp.json" })).toThrow(
      /nonexistent\/hp\.json/,
    );
  });
});

describe("hardwareBoundsError — the fixture's bounds_cases", () => {
  const set = parseHardwareProfiles(fixture.document);
  it.each(fixture.bounds_cases)("$name", (c: { hardware: { profile: string; cpu: number; gpu_count: number }; memory_mb: number; app: string; reason: string }) => {
    const error = hardwareBoundsError(c.hardware, c.memory_mb, set);
    if (c.app === "accept") expect(error).toBeNull();
    else expect(error).toBe(c.reason);
  });
});
```

Append to `app/src/__tests__/contract-fixtures.test.ts` (after the process-runtime block; reuse `loadFixture`):
```ts
describe("hardware profiles contract (tests/contract-fixtures/hardware-profiles.json)", () => {
  const fixture = loadFixture("hardware-profiles.json") as unknown as {
    document: unknown;
    cases: { name: string; document: unknown; app: "accept" | "reject" }[];
  };
  it.each(fixture.cases)("$name", ({ document, app }) => {
    const doc = document === "$document" ? fixture.document : document;
    expect(hardwareProfileSetSchema.safeParse(doc).success).toBe(app === "accept");
  });
});
```
(import `hardwareProfileSetSchema` from `@/lib/processes/hardware`.)

Append to `app/src/__tests__/api-processes.test.ts` (import `GET as hardwareProfilesRoute` from `@/pages/api/processes/hardware-profiles`; the file's `call`/`member`/`anon` helpers):
```ts
describe("GET /api/processes/hardware-profiles (K-1)", () => {
  it("lists the deployment's profiles without their backend blocks, member+", async () => {
    const res = await call(hardwareProfilesRoute, member);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.backend).toBe("docker");
    expect(body.profiles.map((p: { id: string }) => p.id)).toContain("standard");
    expect(body.profiles.every((p: object) => !("backend" in p))).toBe(true);
  });

  it("requires authentication", async () => {
    const res = await call(hardwareProfilesRoute, anon);
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 2:** `cd app && npx vitest run src/__tests__/processes-hardware.test.ts src/__tests__/contract-fixtures.test.ts src/__tests__/api-processes.test.ts` → FAIL (module not found).

- [ ] **Step 3: Implement `app/src/lib/processes/hardware.ts`:**

```ts
/**
 * Hardware profiles (K-1, process-compute spec §3, ADR 0019).
 *
 * A deployment describes the hardware a run may ask for as named PROFILES in
 * one JSON document both runtimes read. This side validates it STRICTLY (a
 * typo in a deployment file fails at the route, not in a run), serves it to
 * the UI minus each profile's pipeline-only `backend` block, and enforces a
 * revision's `hardware` block against the bounds at the write gate — the
 * pipeline re-checks independently at launch (`pipeline/process/hardware.py`),
 * the `PROCESS_NETWORK_MAX` pattern.
 *
 * The file is read at request time and cached per process lifetime: it is a
 * per-deployment path (`PROCESS_HARDWARE_PROFILES_FILE`), not a document baked
 * into the bundle — unlike `builtin-extractors.json`, which every deployment
 * shares.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { z } from "zod";

export const HARDWARE_TIERS = ["cpu", "cpu-large", "gpu"] as const;
export const DEFAULT_HARDWARE_PROFILE = "standard";
/** What an absent `hardware` block means on BOTH sides — equals the shipped
 * sets' `standard.cpu.default` (tests pin it). */
export const DEFAULT_HARDWARE_CPU = 1;
export const DEFAULT_HARDWARE_GPU_COUNT = 0;

const PROFILE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const hardwareBoundsSchema = z
  .object({ min: z.number(), max: z.number(), default: z.number() })
  .strict()
  .refine((b) => b.min <= b.default && b.default <= b.max, {
    message: "bounds must satisfy min <= default <= max",
  });

export const hardwareProfileSchema = z
  .object({
    id: z.string().regex(PROFILE_ID),
    label: z.string().min(1),
    description: z.string(),
    tier: z.enum(HARDWARE_TIERS),
    accelerator: z
      .object({ vendor: z.string().min(1), model: z.string().min(1), memory_gb: z.number().positive() })
      .strict()
      .nullable(),
    cpu: hardwareBoundsSchema,
    memory_mb: hardwareBoundsSchema,
    gpu_count: hardwareBoundsSchema.nullable(),
    max_queue_wait_seconds: z.number().int().min(0),
    image: z.string().min(1).nullable(),
    // Pipeline-only; carried opaquely and never returned by the API.
    backend: z.record(z.string(), z.unknown()),
  })
  .strict()
  .refine((p) => (p.accelerator === null) === (p.gpu_count === null), {
    message: "gpu_count is required with an accelerator and forbidden without one",
    path: ["gpu_count"],
  });

export const hardwareProfileSetSchema = z
  .object({ version: z.literal(1), profiles: z.array(hardwareProfileSchema).min(1) })
  .strict()
  .superRefine((set, ctx) => {
    const ids = set.profiles.map((p) => p.id);
    if (new Set(ids).size !== ids.length) {
      ctx.addIssue({ code: "custom", path: ["profiles"], message: "profile ids must be unique" });
    }
    if (!ids.includes(DEFAULT_HARDWARE_PROFILE)) {
      ctx.addIssue({
        code: "custom",
        path: ["profiles"],
        message: `exactly one profile must be '${DEFAULT_HARDWARE_PROFILE}'`,
      });
    }
  });

export type HardwareProfile = z.infer<typeof hardwareProfileSchema>;
export type HardwareProfileSet = z.infer<typeof hardwareProfileSetSchema>;
export type PublicHardwareProfile = Omit<HardwareProfile, "backend">;

export function parseHardwareProfiles(document: unknown): HardwareProfileSet {
  const parsed = hardwareProfileSetSchema.safeParse(document);
  if (!parsed.success) {
    throw new Error(`hardware profiles: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`);
  }
  return parsed.data;
}

const CHECKOUT_LOCAL_SET = fileURLToPath(
  new URL("../../../../infra/hardware-profiles/local.json", import.meta.url),
);

export function hardwareProfilesPath(env: Record<string, string | undefined> = process.env): string {
  const override = env.PROCESS_HARDWARE_PROFILES_FILE?.trim();
  return override ? override : CHECKOUT_LOCAL_SET;
}

let cache: { path: string; set: HardwareProfileSet } | null = null;

/** Read and validate the deployment's profile set, cached per process
 * lifetime (the document is deployment config, not operator data — spec §13.1). */
export function loadHardwareProfiles(
  env: Record<string, string | undefined> = process.env,
): HardwareProfileSet {
  const path = hardwareProfilesPath(env);
  if (cache && cache.path === path) return cache.set;
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    throw new Error(`could not read the hardware profiles at ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
  const set = parseHardwareProfiles(JSON.parse(text));
  cache = { path, set };
  return set;
}

export function resetHardwareProfilesCache(): void {
  cache = null;
}

export function findProfile(set: HardwareProfileSet, id: string): HardwareProfile | undefined {
  return set.profiles.find((p) => p.id === id);
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : String(n);
}

/** The write-gate half of the dual enforcement (spec §4). Returns the message
 * to refuse with, or null when the block is within its profile's bounds — the
 * SAME text `check_hardware_bounds` raises in the pipeline (the fixture's
 * `bounds_cases[].reason` pins both). */
export function hardwareBoundsError(
  hardware: { profile: string; cpu: number; gpu_count: number },
  memoryMb: number,
  set: HardwareProfileSet,
): string | null {
  const profile = findProfile(set, hardware.profile);
  if (!profile) return `hardware.profile '${hardware.profile}' is not a hardware profile of this deployment`;
  if (hardware.cpu < profile.cpu.min || hardware.cpu > profile.cpu.max) {
    return `hardware.cpu ${fmt(hardware.cpu)} is outside profile '${profile.id}' bounds ${fmt(profile.cpu.min)}–${fmt(profile.cpu.max)}`;
  }
  if (profile.gpu_count === null) {
    if (hardware.gpu_count !== 0) return `hardware.gpu_count must be 0: profile '${profile.id}' has no accelerator`;
  } else if (hardware.gpu_count < profile.gpu_count.min || hardware.gpu_count > profile.gpu_count.max) {
    return `hardware.gpu_count ${hardware.gpu_count} is outside profile '${profile.id}' bounds ${fmt(profile.gpu_count.min)}–${fmt(profile.gpu_count.max)}`;
  }
  if (memoryMb < profile.memory_mb.min || memoryMb > profile.memory_mb.max) {
    return `memory_mb ${memoryMb} is outside profile '${profile.id}' bounds ${fmt(profile.memory_mb.min)}–${fmt(profile.memory_mb.max)}`;
  }
  return null;
}

export function publicProfiles(set: HardwareProfileSet): PublicHardwareProfile[] {
  return set.profiles.map(({ backend: _backend, ...rest }) => rest);
}
```
(JS prints `0.25` and `4.5` the way Python's `str(0.25)` does; the fixture's `reason` strings are the arbiter — if a case's number formats differently, fix `fmt` on the side that differs, never the fixture.)

Route `app/src/pages/api/processes/hardware-profiles.ts` (pattern: `api/processes/index.ts` GET):
```ts
/**
 * GET /api/processes/hardware-profiles — the deployment's hardware profiles
 * for the picker (K-1, spec §3.3): member+, each profile minus its
 * pipeline-only `backend` block, plus which executor backend runs them so the
 * UI can word its wait states. Reads are ungated by the route guard; the
 * in-route check is the whole gate.
 */
import type { APIRoute } from "astro";

import { authzError } from "@/lib/authz/guard";
import { jsonResponse } from "@/lib/http/response";
import { loadHardwareProfiles, publicProfiles } from "@/lib/processes/hardware";

/** K-5 replaces the constant with `PROCESS_EXECUTOR`. */
const EXECUTOR_BACKEND = "docker";

export const GET: APIRoute = async ({ locals }) => {
  const auth = locals.auth;
  if (!auth?.authenticated) {
    return authzError(401, "unauthenticated", "Authentication required to list hardware profiles");
  }
  try {
    return jsonResponse(200, { backend: EXECUTOR_BACKEND, profiles: publicProfiles(loadHardwareProfiles()) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return jsonResponse(500, { error: message });
  }
};
```
(Check the exact import paths of `authzError` and `jsonResponse` against `api/processes/index.ts`.)

- [ ] **Step 4:** the three files → PASS; `npm run verify` from the worktree root (the `astro check` hook may flag the `fmt` unused-branch — simplify to `String(n)` if so).

- [ ] **Step 5: Commit** — `feat(processes): hardware-profile reader + write-gate bounds check + GET /api/processes/hardware-profiles (K-1)`

---

### Task 3: The runtime `hardware` block — schema, write gate, pipeline parser, fixture cases

**Files:**
- Modify: `app/src/lib/processes/schemas.ts` — `hardwareSchema` + `runtimeLimits.hardware` (anchor: the `runtime_image` line)
- Modify: `app/src/pages/api/processes/[id]/revisions.ts` — the bounds check after the network-cap check (lines 108–115)
- Modify: `services/pipeline/src/pipeline/process/config.py` — `ProcessRuntime` gains `hardware_profile`, `hardware_cpu`, `hardware_gpu_count`; `_parse_hardware`; `parse_process_runtime` wires it
- Modify: `tests/contract-fixtures/process-runtime.json` — both variants' `defaults` gain `hardware`; new `cases`
- Test: `app/src/__tests__/api-processes.test.ts` (append), `app/src/__tests__/contract-fixtures.test.ts` (the existing `describeUnion` covers the new cases — no change unless the union helper needs the new defaults), `services/pipeline/tests/test_contract_fixtures.py` (extend `test_process_runtime_defaults_match_golden`), `services/pipeline/tests/test_process_config.py` (append — find the file that tests `parse_process_runtime`; the map names `test_process_executor.py` for launch checks, the runtime parser tests are wherever `parse_process_runtime(` is exercised — grep)

**Interfaces:**
- Consumes: `hardwareBoundsError`, `loadHardwareProfiles` (Task 2); `DEFAULT_HARDWARE_*` on both sides (Tasks 1–2).
- Produces:
  ```ts
  // schemas.ts
  export const hardwareSchema = z.object({ profile: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/), cpu: z.number().positive(), gpu_count: z.number().int().min(0).default(0) }).strict();
  // runtimeLimits.hardware: hardwareSchema.default(() => ({ profile: "standard", cpu: 1, gpu_count: 0 }))
  export type ProcessHardware = z.infer<typeof hardwareSchema>;
  ```
  ```python
  # process/config.py
  ProcessRuntime.hardware_profile: str = DEFAULT_HARDWARE_PROFILE
  ProcessRuntime.hardware_cpu: float = DEFAULT_HARDWARE_CPU
  ProcessRuntime.hardware_gpu_count: int = DEFAULT_HARDWARE_GPU_COUNT
  def _parse_hardware(raw: Any) -> tuple[str, float, int]    # shape-only: profile non-blank id, cpu > 0, gpu_count int >= 0; None ⇒ the defaults
  ```
  The profile-set-aware bounds check is NOT in the parser (it has no settings) — Task 4 adds it at launch.

- [ ] **Step 1: Failing tests.**

`tests/contract-fixtures/process-runtime.json`: in `variants.inline_python.defaults` AND `variants.container.defaults`, append `"hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }` (after `runtime_image`). Append these `cases` (keep the file's style; `container` cases stay `app: reject` for the existing reason, so use `inline_python`):
```json
{ "name": "hardware block absent reads as standard (K-1: every stored revision lacks it)", "config": { "kind": "inline_python" }, "app": "accept", "pipeline": "accept" },
{ "name": "hardware standard explicitly", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 2, "gpu_count": 0 } }, "app": "accept", "pipeline": "accept" },
{ "name": "hardware gpu_count omitted defaults to 0", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 1 } }, "app": "accept", "pipeline": "accept" },
{ "name": "hardware with a fractional cpu", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 0.5, "gpu_count": 0 } }, "app": "accept", "pipeline": "accept" },
{ "name": "hardware cpu zero", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 0, "gpu_count": 0 } }, "app": "reject", "pipeline": "reject" },
{ "name": "hardware cpu negative", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": -1, "gpu_count": 0 } }, "app": "reject", "pipeline": "reject" },
{ "name": "hardware gpu_count fractional", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0.5 } }, "app": "reject", "pipeline": "reject" },
{ "name": "hardware gpu_count negative", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 1, "gpu_count": -1 } }, "app": "reject", "pipeline": "reject" },
{ "name": "hardware profile blank", "config": { "kind": "inline_python", "hardware": { "profile": "", "cpu": 1, "gpu_count": 0 } }, "app": "reject", "pipeline": "reject" },
{ "name": "hardware profile not an id", "config": { "kind": "inline_python", "hardware": { "profile": "GPU L4", "cpu": 1, "gpu_count": 0 } }, "app": "reject", "pipeline": "reject" },
{ "name": "hardware with an unknown key", "config": { "kind": "inline_python", "hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0, "tpu": 1 } }, "app": "reject", "pipeline": "accept" },
{ "name": "hardware naming a profile the SHAPE cannot know (bounds are the write gate's, not the schema's)", "config": { "kind": "inline_python", "hardware": { "profile": "tpu-v5", "cpu": 1, "gpu_count": 0 } }, "app": "accept", "pipeline": "accept" }
```
(The last case documents the division of labour: the schema/parser accept any well-formed id; the route and the launch check refuse unknown profiles — the fixture's `bounds_cases` cover that.)

`services/pipeline/tests/test_contract_fixtures.py` — extend `test_process_runtime_defaults_match_golden` with:
```python
        assert runtime.hardware_profile == golden["hardware"]["profile"]
        assert runtime.hardware_cpu == golden["hardware"]["cpu"]
        assert runtime.hardware_gpu_count == golden["hardware"]["gpu_count"]
```
Pipeline parser tests (append to the file that already tests `parse_process_runtime` directly — grep `parse_process_runtime(` under `services/pipeline/tests/`):
```python
def test_runtime_hardware_absent_reads_as_standard():
    rt = parse_process_runtime({"kind": "inline_python"})
    assert (rt.hardware_profile, rt.hardware_cpu, rt.hardware_gpu_count) == ("standard", 1.0, 0)


def test_runtime_hardware_is_flattened():
    rt = parse_process_runtime(
        {"kind": "inline_python", "hardware": {"profile": "gpu-l4", "cpu": 2.5, "gpu_count": 1}}
    )
    assert (rt.hardware_profile, rt.hardware_cpu, rt.hardware_gpu_count) == ("gpu-l4", 2.5, 1)


def test_runtime_hardware_rejects_a_boolean_cpu():
    with pytest.raises(ProcessConfigError, match="hardware.cpu"):
        parse_process_runtime({"kind": "inline_python", "hardware": {"profile": "standard", "cpu": True}})
```
App route tests — append to `api-processes.test.ts`'s deploy block (find the existing "deploys an explicitly isolated network profile" test and mirror its `deployRevision` mock + body):
```ts
  it("refuses a deploy whose hardware is outside its profile's bounds (K-1, 400 naming the bound)", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: {
        runtime: { kind: "inline_python", hardware: { profile: "standard", cpu: 64, gpu_count: 0 } },
        code: "print('hi')",
        env: [],
      },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe(
      "hardware.cpu 64 is outside profile 'standard' bounds 0.25–4",
    );
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("refuses a deploy naming a profile this deployment does not have", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: {
        runtime: { kind: "inline_python", hardware: { profile: "tpu-v5", cpu: 1, gpu_count: 0 } },
        code: "print('hi')",
        env: [],
      },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe(
      "hardware.profile 'tpu-v5' is not a hardware profile of this deployment",
    );
  });

  it("stores the default hardware block when a deploy omits it", async () => {
    vi.mocked(deployRevision).mockResolvedValue(revision());   // reuse the file's revision fixture helper
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { kind: "inline_python" }, code: "print('hi')", env: [] },
    });
    expect(res.status).toBe(201);
    expect(vi.mocked(deployRevision).mock.calls[0][0].runtime.hardware).toEqual({
      profile: "standard",
      cpu: 1,
      gpu_count: 0,
    });
  });
```
(Read the file's existing deploy tests for the exact body shape, the `revision()` helper name, and whether `env: []` is required.)

- [ ] **Step 2:** run the four test files (two vitest, two pytest) → FAIL.

- [ ] **Step 3: Implement.**

`schemas.ts` — beside `PROCESS_RUNTIME_IMAGE_ALIASES`:
```ts
/**
 * K-1 (process-compute spec §4): the hardware a run asks for — a profile id
 * this deployment defines plus counts within the profile's bounds. The SHAPE
 * lives here; the bounds and the profile's existence are checked by the deploy
 * route (`hardwareBoundsError`) and again by the pipeline at launch, because
 * the profile set is deployment config, not part of the schema.
 */
export const hardwareSchema = z
  .object({
    profile: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, "hardware.profile must be a profile id"),
    cpu: z.number().positive(),
    gpu_count: z.number().int().min(0).default(0),
  })
  .strict();
export type ProcessHardware = z.infer<typeof hardwareSchema>;
```
and in `runtimeLimits`, after `runtime_image`:
```ts
  // Every stored revision predates this block: absent means `standard` at its
  // default cpu (1 — the shipped sets pin it), no GPU. The pipeline reader
  // defaults the same way.
  hardware: hardwareSchema.default(() => ({ profile: "standard", cpu: 1, gpu_count: 0 })),
```
`revisions.ts` — after the network-cap block:
```ts
    // K-1 (spec §4): the profile must exist here and the numbers must sit
    // inside its bounds; the pipeline re-checks at launch. Refused at deploy
    // time so the operator hears it at the form.
    const hardwareError = hardwareBoundsError(
      data.runtime.hardware,
      data.runtime.memory_mb,
      loadHardwareProfiles(),
    );
    if (hardwareError) return jsonResponse(400, { error: hardwareError });
```
(imports from `@/lib/processes/hardware`). The route test file mocks nothing for this — `loadHardwareProfiles()` reads the checkout's `local.json` (no `PROCESS_HARDWARE_PROFILES_FILE` in the vitest env), whose `standard` bounds are 0.25–4 — that is why the test above uses cpu 64.

`config.py` — imports from `pipeline.process.hardware` (`DEFAULT_HARDWARE_CPU`, `DEFAULT_HARDWARE_GPU_COUNT`, `DEFAULT_HARDWARE_PROFILE`; `hardware.py` imports nothing from `config.py`, so no cycle); fields after `runtime_image`:
```python
    #: K-1 (process-compute spec §4) — the `hardware` block, flattened like
    #: `network`: the profile id and the counts; bounds are checked at launch
    #: against the deployment's profile set (`check_hardware_bounds`).
    hardware_profile: str = DEFAULT_HARDWARE_PROFILE
    hardware_cpu: float = DEFAULT_HARDWARE_CPU
    hardware_gpu_count: int = DEFAULT_HARDWARE_GPU_COUNT
```
parser (beside `_parse_network`):
```python
_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _parse_hardware(raw: Any) -> tuple[str, float, int]:
    """Shape only — the profile set is not known here; `check_hardware_bounds`
    at launch does the rest. Absent reads as the defaults (every stored
    revision predates the block)."""
    if raw is None:
        return DEFAULT_HARDWARE_PROFILE, DEFAULT_HARDWARE_CPU, DEFAULT_HARDWARE_GPU_COUNT
    doc = _obj(raw, "runtime.hardware")
    profile = doc.get("profile")
    if not isinstance(profile, str) or not _PROFILE_ID_RE.match(profile):
        raise ProcessConfigError(f"runtime.hardware.profile must be a profile id, got {profile!r}")
    cpu_raw = doc.get("cpu")
    if isinstance(cpu_raw, bool) or not isinstance(cpu_raw, (int, float)) or cpu_raw <= 0:
        raise ProcessConfigError(f"runtime.hardware.cpu must be a positive number, got {cpu_raw!r}")
    gpu_count = _int_in_range(
        doc.get("gpu_count"), "runtime.hardware.gpu_count", default=DEFAULT_HARDWARE_GPU_COUNT, minimum=0
    )
    gpu_raw = doc.get("gpu_count")
    if gpu_raw is not None and isinstance(gpu_raw, float) and not gpu_raw.is_integer():
        raise ProcessConfigError(f"runtime.hardware.gpu_count must be an integer, got {gpu_raw!r}")
    return profile, float(cpu_raw), gpu_count
```
(`_int_in_range` truncates floats in the lenient direction; the explicit integer check keeps `0.5` a rejection as the fixture case demands — put it BEFORE the `_int_in_range` call so the message is the integer one.) In `parse_process_runtime`: `hardware_profile, hardware_cpu, hardware_gpu_count = _parse_hardware(doc.get("hardware"))` and the three kwargs on `ProcessRuntime(...)`.

- [ ] **Step 4:** the four files → PASS; `npm run verify`; `uv run pytest -q`; `uv run ruff check .`.

- [ ] **Step 5: Commit** — `feat(processes): runtime hardware {profile, cpu, gpu_count} — schema default, deploy-time bounds check, pipeline reader, fixture cases (K-1)`

---

### Task 4: Launch-time enforcement, `RunSpec` fields, `priority`

**Files:**
- Modify: `services/pipeline/src/pipeline/process/executor.py:51-71` — `RunSpec` fields
- Modify: `services/pipeline/src/pipeline/process/launch.py` — `build_run_spec` and `execute_run` gain `profile` and `priority`; re-export nothing new
- Modify: `services/pipeline/src/pipeline/process/runner.py` — `run_one` gains `profiles: HardwareProfileSet | None = None`; the bounds check beside `check_network_cap`; passes `profile`/`priority` to `execute_run`
- Test: `services/pipeline/tests/test_process_executor.py` (append beside the `check_network_cap` tests), `services/pipeline/tests/test_process_triggers.py` (append beside the test that asserts `"PROCESS_NETWORK_MAX" in result.error` — line ~711; mirror its scaffold)

**Interfaces:**
- Consumes: `HardwareProfileSet`, `check_hardware_bounds`, `load_hardware_profiles`, `HardwareProfileError` (Task 1); `ProcessRuntime.hardware_*` (Task 3); `QueuedRun.is_test`.
- Produces:
  ```python
  RunSpec.cpu: float = 1.0; RunSpec.gpu_count: int = 0; RunSpec.profile: HardwareProfile | None = None; RunSpec.priority: str = "triggered"
  PRIORITIES = ("interactive", "triggered")            # executor.py
  def build_run_spec(..., profile: HardwareProfile | None = None, priority: str = "triggered") -> RunSpec
  def execute_run(..., profile: HardwareProfile | None = None, priority: str = "triggered")   # threads them to build_run_spec
  async def run_one(..., profiles: HardwareProfileSet | None = None)   # None ⇒ load_hardware_profiles() once per call
  ```

- [ ] **Step 1: Failing tests.** Append to `tests/test_process_executor.py` (beside `test_check_network_cap…`; reuse its `settings(...)` and runtime helpers):
```python
def test_run_spec_carries_hardware_and_priority_with_safe_defaults():
    from pipeline.process.executor import PRIORITIES, RunSpec

    spec = RunSpec(run_id="r1", process_id="p1", image="img")
    assert (spec.cpu, spec.gpu_count, spec.profile, spec.priority) == (1.0, 0, None, "triggered")
    assert PRIORITIES == ("interactive", "triggered")


def test_build_run_spec_carries_the_resolved_profile_and_priority():
    from pipeline.process.hardware import load_hardware_profiles
    from pipeline.process.launch import build_run_spec

    profiles = load_hardware_profiles()
    rt = parse_process_runtime(
        {"kind": "inline_python", "hardware": {"profile": "cpu-large", "cpu": 3, "gpu_count": 0}}
    )
    spec = build_run_spec(
        settings(),
        run_id="r1",
        process_id="p1",
        runtime=rt,
        code="print(1)",
        env={},
        credentials=_credentials(),        # the file's credentials fixture/helper
        profile=profiles.get("cpu-large"),
        priority="interactive",
    )
    assert spec.cpu == 3.0 and spec.gpu_count == 0
    assert spec.profile is not None and spec.profile.id == "cpu-large"
    assert spec.priority == "interactive"
    # No executor change this slice: memory/timeout/network are what they were.
    assert spec.memory_mb == rt.memory_mb
```
Append to `tests/test_process_triggers.py` beside the network-cap dead-run test (copy its scaffold — the fake repo, the run, `run_one(...)` call — and change only the runtime + assertions):
```python
async def test_run_one_dies_on_a_hardware_profile_outside_the_deployment():
    """K-1 dual enforcement: an unknown profile or out-of-bounds counts die at
    launch naming the bound — before inputs are planned or credentials minted."""
    # <same scaffold as the PROCESS_NETWORK_MAX test>, with
    # runtime={"kind": "inline_python", "hardware": {"profile": "tpu-v5", "cpu": 1, "gpu_count": 0}}
    result = await run_one(...)
    assert result.status == "dead"
    assert result.error == "hardware.profile 'tpu-v5' is not a hardware profile of this deployment"
    assert executor.launched == []          # or the scaffold's equivalent "never launched" assertion


async def test_run_one_dies_on_cpu_above_the_profile_bound():
    # same scaffold; runtime hardware {"profile": "standard", "cpu": 64, "gpu_count": 0}
    result = await run_one(...)
    assert result.status == "dead"
    assert result.error == "hardware.cpu 64 is outside profile 'standard' bounds 0.25–4"


async def test_run_one_marks_a_test_run_interactive():
    # same scaffold with a run whose is_test=True and a capturing executor;
    # assert the launched RunSpec has priority == "interactive" and profile.id == "standard"
    ...
```
(Write the three tests fully from the scaffold; the `...` above is a placeholder for the copied scaffold, not for the assertions.)

- [ ] **Step 2:** `uv run pytest tests/test_process_executor.py tests/test_process_triggers.py -q -k "hardware or priority or interactive"` → FAIL.

- [ ] **Step 3: Implement.**

`executor.py` — after the `RunSpec` docstring's fields:
```python
    # K-1 (process-compute spec §4): the hardware the run asked for, resolved
    # against the deployment's profile set at launch. Defaults keep every
    # existing construction valid; no executor reads them yet (K-3 does).
    cpu: float = 1.0
    gpu_count: int = 0
    profile: HardwareProfile | None = None
    #: "interactive" for a UI test run, "triggered" otherwise — Kueue's two
    #: priority classes (K-5/K-6); unread until then.
    priority: str = "triggered"
```
with `PRIORITIES = ("interactive", "triggered")` at module level and `from pipeline.process.hardware import HardwareProfile` (check for an import cycle: `hardware.py` imports nothing from the package — fine).

`launch.py`:
```python
class HardwareProfileRejected(Exception):
    """The revision's hardware block names a profile this deployment lacks or
    numbers outside its bounds — configuration, so the run dies naming the
    bound rather than launching on hardware the operator never saw."""


def check_hardware_bounds_for(runtime: ProcessRuntime, profiles: HardwareProfileSet) -> HardwareProfile:
    """K-1 spec §4: the launch-time half of the dual enforcement — the app's
    write gate ran the same check with the same messages."""
    try:
        return check_hardware_bounds(
            profiles=profiles,
            profile_id=runtime.hardware_profile,
            cpu=runtime.hardware_cpu,
            gpu_count=runtime.hardware_gpu_count,
            memory_mb=runtime.memory_mb,
        )
    except HardwareProfileError as err:
        raise HardwareProfileRejected(str(err)) from err
```
`build_run_spec(..., profile: HardwareProfile | None = None, priority: str = "triggered")` adds `cpu=runtime.hardware_cpu, gpu_count=runtime.hardware_gpu_count, profile=profile, priority=priority` to the `RunSpec(...)`; `execute_run` gains the same two keyword parameters and passes them to `build_run_spec` (read `execute_run`'s signature at launch.py ~180–235 first — add the parameters after its last keyword parameter, defaults as above).

`runner.py` `run_one(..., profiles: HardwareProfileSet | None = None)`; after the runtime-image check:
```python
    # K-1 spec §4: the hardware block against the deployment's profile set —
    # the same check the app ran at deploy time, run again here because the
    # set is deployment config that may differ from the app's.
    try:
        profile = check_hardware_bounds_for(runtime, profiles or load_hardware_profiles())
    except (HardwareProfileRejected, HardwareProfileError) as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))
```
(`HardwareProfileError` covers an unreadable profile file — a `dead` run with the reason, not a crash.) Pass `profile=profile, priority="interactive" if run.is_test else "triggered"` into the `execute_run(...)` call.

- [ ] **Step 4:** `uv run pytest -q`; `uv run ruff check .`. (`jobs/process.py` needs no change: `run_one`'s `profiles=None` loads the set per run — one small file read per run; caching is K-3's concern when the capacity path reads `backend`.)

- [ ] **Step 5: Commit** — `feat(process): launch-time hardware bounds (dead run naming the bound), RunSpec cpu/gpu_count/profile/priority (K-1)`

---

### Task 5: Packaging and docs

**Files:**
- Modify: `services/pipeline/Dockerfile` — `COPY --from=hardware local.json /app/share/hardware-profiles/local.json` beside the fixtures COPY; `ENV PROCESS_HARDWARE_PROFILES_FILE=/app/share/hardware-profiles/local.json`
- Modify: `app/Dockerfile` — in the runtime (final) stage: `COPY --from=hardware local.json /app/share/hardware-profiles/local.json` and the same `ENV`
- Modify: `docker-compose.yml` — pipeline `additional_contexts` gains `hardware: ./infra/hardware-profiles`; `- PROCESS_HARDWARE_PROFILES_FILE=${PROCESS_HARDWARE_PROFILES_FILE:-/app/share/hardware-profiles/local.json}` under the pipeline env (beside `PROCESS_NETWORK_MAX`); the app service (`api`) build block — if it has `additional_contexts: fixtures:` add `hardware:` beside it, and the env line; if the app image is built elsewhere (the auth-enforced overlay), do the same there
- Modify: `.github/workflows/containers.yml` — every `build-contexts: fixtures=tests/contract-fixtures` gains a second line `hardware=infra/hardware-profiles` (multi-line value)
- Modify: `services/pipeline/README.md` — env-contract row for `PROCESS_HARDWARE_PROFILES_FILE`; a short `## Hardware profiles (K-1)` section before `## Docker`
- Modify: `docs/backend.md` — route table row `| /api/processes/hardware-profiles | GET | The deployment's hardware profiles minus their backend blocks, plus the executor backend (member+; K-1) |`; env table row if the file has one for the app
- Modify: `docs/processes.md` — nothing (K-2 adds "Hardware"); `docs/FEATURES.md` — a `| K-1 · hardware-profile contract | ✅ | … |` row after the X-3 row (the K queue's first row)
- Modify: `.env.example` — a commented `# PROCESS_HARDWARE_PROFILES_FILE=` line beside the `PROCESS_RUNTIME_IMAGE*` ones

- [ ] **Step 1:** Dockerfiles + compose + CI as listed. The named context is NOT filtered by the repo-root `.dockerignore` (which excludes `infra/`), which is exactly why a named context is used rather than a re-include. Verify locally without Docker: `grep -n "hardware" services/pipeline/Dockerfile app/Dockerfile docker-compose.yml .github/workflows/containers.yml` shows every anchor.
- [ ] **Step 2:** README section:
```markdown
## Hardware profiles (K-1)

A run asks for hardware by naming a **profile** the deployment defines plus
CPU/memory/GPU counts within its bounds (process-compute spec §3, ADR 0019).
The profile document is one JSON file both runtimes read
(`PROCESS_HARDWARE_PROFILES_FILE`; the image copies
`infra/hardware-profiles/local.json` to `/app/share/hardware-profiles/` through
the `hardware` named build context; unset means the repo checkout). The app
validates it strictly at deploy time and serves it minus each profile's
`backend` block on `GET /api/processes/hardware-profiles`; the pipeline
re-checks the revision's `hardware` block at launch (`check_hardware_bounds`)
and a run outside its profile — or naming a profile this deployment lacks —
dies with the bound in its error, before inputs are planned or credentials
minted. `backend` is opaque until K-3 (Docker: `NanoCpus`, `DeviceRequests`,
per-profile capacity) and K-5 (Kubernetes) consume it. `RunSpec` already
carries `cpu`, `gpu_count`, the resolved `profile` and a `priority`
(`interactive` for a UI test run, `triggered` otherwise) that nothing reads
yet.
```
- [ ] **Step 3:** FEATURES row, backend.md row, `.env.example` line.
- [ ] **Step 4:** `npm run verify` and `uv run ruff check .` (docs + packaging only) → commit: `docs(k1): hardware-profile packaging (named build context), README section, route table, FEATURES row`.

---

### Task 6: Verify, merge, live check (lead only)

- [ ] `npm run verify`; pytest + ruff on the branch. Merge `--no-ff` into `ai/main`; gates again.
- [ ] `docker compose build pipeline && docker compose up -d pipeline` (the new named context must resolve — a failure here is the packaging step); confirm `docker compose exec pipeline sh -c 'ls -l $PROCESS_HARDWARE_PROFILES_FILE'`. Canary fresh.
- [ ] e2e: `npm run test:e2e:ci -- processes` (the processes spec deploys revisions — the default block must not break it), then the whole suite.
- [ ] Live: dev server up with `.env`; `curl -s :4321/api/processes/hardware-profiles` (dev bypass identity) lists `standard` + `cpu-large` without `backend`; deploy a revision through `POST /api/processes/[id]/revisions` (curl with the CSRF headers from the GOES memory) with `hardware.cpu: 64` → 400 naming the bound; with `cpu-large` cpu 3 → 201 and the stored runtime carries the block; run it (Run now) → the run completes (no executor change) and its `process_runs` row is unchanged in shape.
- [ ] TODO tick K-1, K queue table note, "K-1 landed" follow-up (deviations, the live results); FEATURES date; remove the worktree + branch. K-2 (UI picker) and K-3 (Docker honours the profile; migration 029) are next — both need their own plans.

---

## Self-review

- **Spec coverage:** §3.2 document shape + rules (exactly one `standard`, unique ids, inclusive bounds, `gpu_count` ⇔ `accelerator`, `image` base) — Task 1 fixture/reader, Task 2 schema; §3.3 route minus `backend` + `backend: "docker"` — Task 2; §3.4 backend block opaque — Tasks 1–2; §4 `hardware` block, lenient readers, `memory_mb` bounded by the profile, dual enforcement with a 400 naming the bound and a `dead` run — Tasks 3–4; `RunSpec` fields incl. `priority` — Task 4; `PROCESS_HARDWARE_PROFILES_FILE` + in-repo default sets + `process-runtime.json` cases — Tasks 1, 3, 5; "no executor change" — nowhere touched; §13.1 (profiles are deployment config) — the file-based loaders.
- **Placeholder scan:** the "read the file first" instructions each name the file and the fact (the deploy test's body shape and `revision()` helper; `execute_run`'s signature; the parser-test file; the app service's build block; the network-cap test scaffold to copy). Task 4's trigger tests show the assertions and name the scaffold to copy rather than repeating 60 lines of fake-repo setup.
- **Type consistency:** `check_hardware_bounds(*, profiles, profile_id, cpu, gpu_count, memory_mb) -> HardwareProfile` (T1) is what T4's `check_hardware_bounds_for` calls; `hardwareBoundsError(hardware, memoryMb, set): string | null` (T2) is what T3's route calls; the message strings are identical on both sides and pinned by `bounds_cases[].reason`; `DEFAULT_HARDWARE_*` names match across `hardware.py`, `config.py`, `hardware.ts`, `schemas.ts`; `ProcessRuntime.hardware_profile/hardware_cpu/hardware_gpu_count` (T3) are what T4's `build_run_spec` reads; `RunSpec.priority` values come from `PRIORITIES`.
- **Known trade recorded:** `run_one` reads the profile file once per run when no set is injected (a few KB; K-3 caches when it needs `backend` on the claim path). The app-side loader is the first request-time `readFileSync` in `app/src` — documented in the module docstring with the reason (per-deployment path).
