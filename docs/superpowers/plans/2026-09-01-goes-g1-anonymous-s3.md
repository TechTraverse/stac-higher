# G-1 · Anonymous S3 Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator create an S3 connection to a public bucket (NODD's `noaa-goes19`) with no access keys, and have the pipeline talk to it unsigned.

**Architecture:** One new boolean on the s3 connection `config` (`anonymous`), which is a cross-runtime contract: the app's Zod schema stops requiring credentials when it is set, the form hides the key fields, and the Python `S3Adapter` builds its boto3 client with `botocore.UNSIGNED`. A new golden fixture pins the config shape for both suites, per `tests/contract-fixtures/README.md`.

**Tech Stack:** Zod v4 (app), React Hook Form, vitest + Testing Library, boto3/botocore (pipeline), pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §8.

## Global Constraints

- Work in a worktree off `ai/main`: `git worktree add .claude/worktrees/goes-g1 -b ai/goes-g1 ai/main`; `npm install` at the repo root of the worktree.
- `npm run verify` (repo root) and, from `services/pipeline/`, `uv run pytest` + `uv run ruff check .` must pass before the slice is declared done.
- Never edit `app/src/components/ui/*` by hand (hook-blocked).
- Credentials are write-only: nothing in this slice may return a credential value through the API.
- A new or changed cross-runtime shape ⇒ a fixture in `tests/contract-fixtures/` consumed by BOTH suites.
- The app schema is the strict writer; the pipeline reader is lenient (ignores unknown keys) — the same asymmetry every other fixture pins.
- Commit messages end with the attribution trailer given in the session's system reminder.

---

### Task 1: App schema — `anonymous` on the s3 config, credentials optional when set

**Files:**
- Modify: `app/src/lib/connections/schemas.ts` (header comment lines 1–24; `s3ConfigSchema` ~line 53; the s3 arm of `connectionCreateSchema` ~line 197)
- Test: `app/src/__tests__/connections-schemas.test.ts`

**Interfaces:**
- Produces: `s3ConfigSchema` accepts `{ anonymous?: boolean }` (default `false`); exported constant `S3_CREDENTIALS_REQUIRED_MESSAGE`; `parseConnectionCreate` accepts an s3 payload with `config.anonymous: true` and no `credentials`, and rejects `anonymous: false`/absent without credentials. `ConnectionCreateInput` for the s3 arm now has `credentials?: …`.

- [ ] **Step 1: Write the failing tests**

Append inside the `describe("parseConnectionCreate — protocol matrix")` block of `app/src/__tests__/connections-schemas.test.ts`:

```ts
  it("accepts an anonymous s3 connection with no credentials", () => {
    const result = create({
      protocol: "s3",
      config: { bucket: "noaa-goes19", region: "us-east-1", anonymous: true },
    });
    expect(result.success).toBe(true);
    if (result.success && result.data.protocol === "s3") {
      expect(result.data.config.anonymous).toBe(true);
      expect(result.data.credentials).toBeUndefined();
    }
  });

  it("still requires credentials when anonymous is false or absent", () => {
    const absent = create({ protocol: "s3", config: { bucket: "b" } });
    expect(absent.success).toBe(false);
    if (!absent.success) {
      expect(absent.error.issues.some((i) => i.path.join(".") === "credentials")).toBe(true);
    }
    const explicit = create({
      protocol: "s3",
      config: { bucket: "b", anonymous: false },
    });
    expect(explicit.success).toBe(false);
  });

  it("defaults anonymous to false on parse", () => {
    const result = create({
      protocol: "s3",
      config: { bucket: "b" },
      credentials: { access_key_id: "a", secret_access_key: "b" },
    });
    expect(result.success).toBe(true);
    if (result.success && result.data.protocol === "s3") {
      expect(result.data.config.anonymous).toBe(false);
    }
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/connections-schemas.test.ts`
Expected: the three new tests FAIL (`anonymous` is an unknown key under `.strict()`, so the first parse fails; the third fails on `anonymous` being `undefined`).

- [ ] **Step 3: Implement the schema change**

In `app/src/lib/connections/schemas.ts`:

1. Update the header comment's s3 line to
   `config      s3        {bucket, region?, endpoint?, force_path_style?, anonymous (default false)}` and add under the credentials table: `credentials are OPTIONAL for s3 when config.anonymous is true (public buckets — NODD); the pipeline signs nothing.`

2. Replace `s3ConfigSchema`:

```ts
export const s3ConfigSchema = z
  .object({
    bucket: z.string().min(1, "bucket is required"),
    region: z.string().min(1).optional(),
    endpoint: z.string().url("endpoint must be a URL").optional(),
    force_path_style: z.boolean().optional(),
    // Public buckets (NODD): the pipeline sends unsigned requests and the
    // credentials envelope may be empty. Default false so every stored config
    // written before this field reads the same as before.
    anonymous: z.boolean().default(false),
  })
  .strict();
```

3. Add, next to `STAC_API_RESERVED_MESSAGE`:

```ts
export const S3_CREDENTIALS_REQUIRED_MESSAGE =
  "access_key_id and secret_access_key are required unless config.anonymous is true";
```

4. Change the s3 arm of `connectionCreateSchema` to make credentials optional, and wrap the union in a refinement that re-imposes the requirement for non-anonymous s3. Because a refined schema is no longer a `ZodDiscriminatedUnion`, keep the union under a private name and export the refined one under the existing name:

```ts
const connectionCreateUnion = z.discriminatedUnion("protocol", [
  z.object({
    protocol: z.literal("s3"),
    ...baseCreateFields,
    config: s3ConfigSchema,
    credentials: s3CredentialsSchema.optional(),
  }),
  // …the ssh / sftp / ftp / ftps arms exactly as they are today…
]);

/**
 * POST /api/connections body — discriminated on protocol so config and
 * credentials are validated against the right per-protocol shape. s3 is the
 * one protocol whose credentials may be absent: only when `config.anonymous`.
 */
export const connectionCreateSchema = connectionCreateUnion.superRefine(
  (data, ctx) => {
    if (data.protocol === "s3" && !data.config.anonymous && !data.credentials) {
      ctx.addIssue({
        code: "custom",
        path: ["credentials"],
        message: S3_CREDENTIALS_REQUIRED_MESSAGE,
      });
    }
  },
);

export type ConnectionCreateInput = z.infer<typeof connectionCreateSchema>;
```

`parseConnectionCreate` keeps calling `connectionCreateSchema.safeParse(data)` — no change there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/connections-schemas.test.ts`
Expected: PASS, including the pre-existing s3 tests.

- [ ] **Step 5: Typecheck and fix the one caller that assumed credentials were always present**

Run: `cd app && npm run check`
Expected: an error in `app/src/pages/api/connections/index.ts` at `JSON.stringify(data.credentials)` (possibly `undefined`). Fix it there:

```ts
    // An anonymous s3 connection stores an EMPTY envelope rather than no
    // envelope: the pipeline's build_adapter treats "no credentials" as a
    // configuration error, and `{}` is exactly what an unsigned adapter needs.
    const envelope = getEncryptionProvider().encrypt(
      JSON.stringify(data.credentials ?? {}),
    );
```

Re-run `npm run check` until clean.

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/connections/schemas.ts app/src/__tests__/connections-schemas.test.ts app/src/pages/api/connections/index.ts
git commit -m "feat(connections): anonymous flag on s3 config; credentials optional when set (G-1)"
```

---

### Task 2: Contract fixture for the s3 connection config, consumed by both suites

**Files:**
- Create: `tests/contract-fixtures/s3-connection-config.json`
- Modify: `tests/contract-fixtures/README.md` (list the new file under "File format")
- Modify: `app/src/__tests__/contract-fixtures.test.ts`
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py` (add `parse_s3_config`)
- Modify: `services/pipeline/tests/test_contract_fixtures.py`

**Interfaces:**
- Produces: Python `S3Config` dataclass + `parse_s3_config(raw: Any) -> S3Config` in `pipeline.connections.adapters.s3` (raises `ValueError` for a missing/blank bucket or a non-boolean `anonymous`; ignores unknown keys). `S3Adapter.__init__` uses it (Task 3).

- [ ] **Step 1: Write the fixture**

`tests/contract-fixtures/s3-connection-config.json`:

```json
{
  "$comment": "Golden fixture for the s3 connection `config` shape (connections.config jsonb for protocol = s3). Consumed by app/src/__tests__/contract-fixtures.test.ts (s3ConfigSchema — the strict writer) and services/pipeline/tests/test_contract_fixtures.py (parse_s3_config — the lenient reader). `anonymous` (G-1, GOES spec §8) means the pipeline sends UNSIGNED requests and the credentials envelope may be empty; the write gate makes credentials optional only when it is true (pinned in connections-schemas.test.ts, since credentials are not part of this document).",
  "minimal": { "bucket": "noaa-goes19" },
  "defaults": { "bucket": "noaa-goes19", "anonymous": false },
  "cases": [
    {
      "name": "anonymous public bucket",
      "config": { "bucket": "noaa-goes19", "region": "us-east-1", "anonymous": true },
      "app": "accept",
      "pipeline": "accept"
    },
    {
      "name": "custom endpoint, path style, signed",
      "config": { "bucket": "stac-higher", "endpoint": "http://minio:9000", "force_path_style": true, "anonymous": false },
      "app": "accept",
      "pipeline": "accept"
    },
    {
      "name": "anonymous must be a boolean",
      "config": { "bucket": "b", "anonymous": "yes" },
      "app": "reject",
      "pipeline": "reject"
    },
    {
      "name": "bucket is required",
      "config": { "anonymous": true },
      "app": "reject",
      "pipeline": "reject"
    },
    {
      "name": "blank bucket",
      "config": { "bucket": "" },
      "app": "reject",
      "pipeline": "reject"
    },
    {
      "name": "endpoint must be a URL (writer); reader keeps it opaque",
      "config": { "bucket": "b", "endpoint": "minio:9000" },
      "app": "reject",
      "pipeline": "accept"
    },
    {
      "name": "unknown key (writer strict, reader lenient)",
      "config": { "bucket": "b", "sse": "aws:kms" },
      "app": "reject",
      "pipeline": "accept"
    }
  ]
}
```

Add one bullet to the README's file list: "`s3-connection-config.json` — the s3 connection `config` (G-1), validated by `connections/schemas.ts`'s `s3ConfigSchema` and `pipeline/connections/adapters/s3.py`'s `parse_s3_config`."

- [ ] **Step 2: Write the failing app-side fixture test**

Open `app/src/__tests__/contract-fixtures.test.ts`, find how the ingest config fixture is loaded and checked (`loadFixture("ingest-config.json")`, a `ConfigFixture`-style type with `minimal`/`defaults`/`cases`), and add a sibling block using the same helpers:

```ts
import { s3ConfigSchema } from "@/lib/connections/schemas";

describe("s3 connection config contract (tests/contract-fixtures/s3-connection-config.json)", () => {
  const fixture = loadFixture("s3-connection-config.json") as unknown as {
    minimal: unknown;
    defaults: unknown;
    cases: { name: string; config: unknown; app: "accept" | "reject" }[];
  };

  it("parses minimal to defaults", () => {
    expect(s3ConfigSchema.parse(fixture.minimal)).toEqual(fixture.defaults);
  });

  it.each(fixture.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(s3ConfigSchema.safeParse(c.config).success).toBe(c.app === "accept");
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/contract-fixtures.test.ts`
Expected: FAIL only if Task 1 was skipped; with Task 1 in place this block should PASS — that is fine, the fixture pins behaviour that now exists. Confirm every case's verdict matches; if one does not, the fixture and schema disagree and the schema is wrong.

- [ ] **Step 4: Write the failing pipeline-side parser test**

Append to `services/pipeline/tests/test_contract_fixtures.py`:

```python
from pipeline.connections.adapters.s3 import parse_s3_config

S3_CONFIG = _load("s3-connection-config.json")


@pytest.mark.parametrize("case", S3_CONFIG["cases"], ids=lambda c: c["name"])
def test_s3_config_cases(case):
    _check(parse_s3_config, case)


def test_s3_config_minimal_parses_to_defaults():
    parsed = parse_s3_config(S3_CONFIG["minimal"])
    for key, value in S3_CONFIG["defaults"].items():
        assert getattr(parsed, key) == value
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_contract_fixtures.py -k s3_config -v`
Expected: FAIL with `ImportError: cannot import name 'parse_s3_config'`.

- [ ] **Step 6: Implement `parse_s3_config`**

In `services/pipeline/src/pipeline/connections/adapters/s3.py`, after the imports:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class S3Config:
    """The parsed s3 connection ``config`` (cross-runtime contract —
    ``tests/contract-fixtures/s3-connection-config.json``). Lenient reader:
    unknown keys are ignored; the app's Zod schema is the strict writer."""

    bucket: str
    region: str | None = None
    endpoint: str | None = None
    force_path_style: bool = False
    #: Public bucket: sign nothing, credentials may be empty (G-1).
    anonymous: bool = False


def parse_s3_config(raw: Any) -> S3Config:
    if not isinstance(raw, dict):
        raise ValueError("s3 config must be an object")
    bucket = raw.get("bucket")
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("s3 config.bucket is required")
    anonymous = raw.get("anonymous", False)
    if not isinstance(anonymous, bool):
        raise ValueError("s3 config.anonymous must be a boolean")
    force_path_style = raw.get("force_path_style", False)
    if not isinstance(force_path_style, bool):
        raise ValueError("s3 config.force_path_style must be a boolean")
    region = raw.get("region")
    endpoint = raw.get("endpoint")
    return S3Config(
        bucket=bucket,
        region=str(region) if region else None,
        endpoint=str(endpoint) if endpoint else None,
        force_path_style=force_path_style,
        anonymous=anonymous,
    )
```

- [ ] **Step 7: Run both suites' fixture tests**

Run: `cd services/pipeline && uv run pytest tests/test_contract_fixtures.py -v && uv run ruff check .`
Run: `cd app && npx vitest run src/__tests__/contract-fixtures.test.ts`
Expected: PASS, PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/contract-fixtures/s3-connection-config.json tests/contract-fixtures/README.md app/src/__tests__/contract-fixtures.test.ts services/pipeline/src/pipeline/connections/adapters/s3.py services/pipeline/tests/test_contract_fixtures.py
git commit -m "test(contract): s3-connection-config fixture with the anonymous flag (G-1)"
```

---

### Task 3: Pipeline adapter — unsigned client when `anonymous`

**Files:**
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py` (`S3Adapter.__init__`, `_make_client`)
- Test: `services/pipeline/tests/test_adapters.py`

**Interfaces:**
- Consumes: `parse_s3_config` (Task 2).
- Produces: `S3Adapter({"bucket": …, "anonymous": True}, {})` works; `_make_client` passes `signature_version=botocore.UNSIGNED` and no keys when anonymous.

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_adapters.py` (the module already imports `s3_mod`, `S3Adapter`, `_PinSpy`, `_FakeS3Client`):

```python
def test_s3_anonymous_client_is_unsigned_and_keyless(monkeypatch):
    from botocore import UNSIGNED

    captured: dict = {}

    def fake_client(service, **kwargs):
        captured.update(kwargs)
        return _FakeS3Client()

    monkeypatch.setattr(s3_mod, "resolve_pinned", _PinSpy())
    monkeypatch.setattr(s3_mod.boto3, "client", fake_client)

    adapter = S3Adapter({"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True}, {})
    adapter._make_client(None)

    assert captured["config"].signature_version is UNSIGNED
    assert captured.get("aws_access_key_id") is None
    assert captured.get("aws_secret_access_key") is None


def test_s3_signed_client_keeps_keys(monkeypatch):
    from botocore import UNSIGNED

    captured: dict = {}

    def fake_client(service, **kwargs):
        captured.update(kwargs)
        return _FakeS3Client()

    monkeypatch.setattr(s3_mod.boto3, "client", fake_client)
    adapter = S3Adapter({"bucket": "b"}, {"access_key_id": "x", "secret_access_key": "y"})
    adapter._make_client(None)

    assert captured["aws_access_key_id"] == "x"
    assert captured["config"].signature_version is not UNSIGNED


def test_s3_anonymous_test_probe_succeeds_without_credentials(monkeypatch):
    monkeypatch.setattr(s3_mod, "resolve_pinned", _PinSpy())
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *a, **k: _FakeS3Client())
    adapter = S3Adapter({"bucket": "noaa-goes19", "anonymous": True}, {})
    result = asyncio.run(adapter.test()) if False else None  # replaced below
```

Delete the last two lines of that third test and make it async like its neighbours:

```python
async def test_s3_anonymous_test_probe_succeeds_without_credentials(monkeypatch):
    monkeypatch.setattr(s3_mod, "resolve_pinned", _PinSpy())
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *a, **k: _FakeS3Client())
    adapter = S3Adapter({"bucket": "noaa-goes19", "anonymous": True}, {})
    result = await adapter.test()
    assert result["ok"] is True
```

(`asyncio_mode = "auto"` is set in `pyproject.toml`, so a bare `async def test_…` runs.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_adapters.py -k anonymous -v`
Expected: the unsigned test FAILS (`signature_version` is not `UNSIGNED`; keys are `None` already but the config assertion fails).

- [ ] **Step 3: Implement**

In `s3.py`:

```python
from botocore import UNSIGNED
```

Replace the body of `S3Adapter.__init__`:

```python
        cfg = parse_s3_config(config)
        self._bucket = cfg.bucket
        self._region = cfg.region
        self._endpoint = cfg.endpoint
        self._force_path_style = cfg.force_path_style
        self._anonymous = cfg.anonymous
        self._creds = credentials
        self._allow_hosts = allow_hosts
```

Replace `_make_client`:

```python
    def _make_client(self, endpoint_url: str | None) -> Any:
        config_kwargs: dict[str, Any] = {
            "s3": {"addressing_style": "path" if self._force_path_style else "auto"},
            "connect_timeout": 10,
            "read_timeout": 30,
            "retries": {"max_attempts": 2},
        }
        if self._anonymous:
            # Public bucket (NODD): botocore skips signing entirely. Keys are
            # deliberately NOT passed even if the envelope has some — an
            # "anonymous" connection must behave identically whatever was
            # stored, or the flag would lie.
            config_kwargs["signature_version"] = UNSIGNED
            return boto3.client(
                "s3",
                region_name=self._region,
                endpoint_url=endpoint_url,
                config=Config(**config_kwargs),
            )
        return boto3.client(
            "s3",
            region_name=self._region,
            endpoint_url=endpoint_url,
            aws_access_key_id=self._creds.get("access_key_id"),
            aws_secret_access_key=self._creds.get("secret_access_key"),
            aws_session_token=self._creds.get("session_token"),
            config=Config(**config_kwargs),
        )
```

Update the module docstring's first paragraph to mention: "`anonymous: true` in the config (G-1) sends unsigned requests — public buckets such as NODD's need no credentials."

- [ ] **Step 4: Run the adapter tests and lint**

Run: `cd services/pipeline && uv run pytest tests/test_adapters.py tests/test_build_adapter.py -v && uv run ruff check .`
Expected: PASS. If `test_build_adapter.py` has a case asserting that an EMPTY decrypted payload fails, read it: `build_adapter` still raises only when `connection.credentials is None` (no envelope at all), and the app now always writes an envelope, so an empty `{}` payload must construct an adapter. Adjust that test's expectation only if it asserts the opposite, and say so in the commit.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/connections/adapters/s3.py services/pipeline/tests/test_adapters.py
git commit -m "feat(pipeline): unsigned S3 client for anonymous connections (G-1)"
```

---

### Task 4: Connection form — the anonymous switch hides the key fields

**Files:**
- Modify: `app/src/components/connections/ConnectionForm.tsx` (`CONFIG_FIELDS.s3` ~line 83; `defaultConfig` ~line 157; the per-protocol form schema ~lines 215–245; the credentials section render ~lines 625–640)
- Test: `app/src/__tests__/connections-form.test.tsx`

**Interfaces:**
- Consumes: `s3CredentialsSchema`, `S3_CREDENTIALS_REQUIRED_MESSAGE` from `@/lib/connections/schemas`.
- Produces: with the switch on, the form submits `config.anonymous: true` and NO `credentials` key; with it off, the existing required-fields behaviour is unchanged.

- [ ] **Step 1: Write the failing component test**

Read the existing tests in `connections-form.test.tsx` to copy their render/submit idiom (they render `<ConnectionForm …/>`, pick a protocol, `fireEvent.change` inputs, click the submit button, and assert on `createMock`'s argument). Then add:

```ts
describe("s3 anonymous connections", () => {
  it("hides credential fields and submits without credentials when anonymous is on", async () => {
    render(<ConnectionForm open onOpenChange={() => {}} groups={[{ id: "g1", name: "Group 1" }]} />);
    // Select the s3 protocol the same way the existing s3 test does.
    // …
    fireEvent.change(screen.getByLabelText("Bucket"), { target: { value: "noaa-goes19" } });
    fireEvent.click(screen.getByLabelText("Anonymous (public bucket)"));

    expect(screen.queryByLabelText("Access key ID")).toBeNull();
    expect(screen.queryByLabelText("Secret access key")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(createMock).toHaveBeenCalled());
    const payload = createMock.mock.calls[0][0];
    expect(payload.protocol).toBe("s3");
    expect(payload.config.anonymous).toBe(true);
    expect(payload.credentials).toBeUndefined();
  });

  it("still requires keys when anonymous is off", async () => {
    render(<ConnectionForm open onOpenChange={() => {}} groups={[{ id: "g1", name: "Group 1" }]} />);
    // select s3 …
    fireEvent.change(screen.getByLabelText("Bucket"), { target: { value: "b" } });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(screen.getByText(/access_key_id is required|required unless config.anonymous/)).toBeInTheDocument(),
    );
    expect(createMock).not.toHaveBeenCalled();
  });
});
```

Adapt the `render` props and the protocol-selection lines to exactly what the existing s3 test in this file does — copy them verbatim.

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/connections-form.test.tsx`
Expected: FAIL — no element labelled "Anonymous (public bucket)".

- [ ] **Step 3: Implement**

In `ConnectionForm.tsx`:

1. Add the switch to `CONFIG_FIELDS.s3` after `force_path_style`:

```ts
    {
      name: "anonymous",
      label: "Anonymous (public bucket)",
      type: "switch",
      optional: true,
      help: "Public buckets such as NOAA NODD need no keys; requests are sent unsigned.",
    },
```

2. In `defaultConfig`, the s3 case returns `{ bucket: "", region: "", endpoint: "", force_path_style: false, anonymous: false }`.

3. In the per-protocol form schema builder (the `useMemo`/function near line 215 that builds `z.object({ …, config: …, credentials: z.preprocess(cleanRecord, credentialsSchemaFor(protocol)) })` for CREATE), special-case s3 so credentials are a free record validated by a refinement that reads `config.anonymous`:

```ts
import { s3CredentialsSchema, S3_CREDENTIALS_REQUIRED_MESSAGE } from "@/lib/connections/schemas";

// inside the create-schema builder:
const credentialsField =
  protocol === "s3"
    ? z.record(z.string(), z.unknown())
    : z.preprocess(cleanRecord, credentialsSchemaFor(protocol));

const schema = z
  .object({
    /* the existing name / description / group_id / enabled / config fields */
    credentials: credentialsField,
  })
  .superRefine((data, ctx) => {
    if (protocol !== "s3") return;
    const anonymous = (data.config as Record<string, unknown>).anonymous === true;
    const creds = cleanRecord(data.credentials);
    if (anonymous) return;
    const parsed = s3CredentialsSchema.safeParse(creds);
    if (!parsed.success) {
      if (Object.keys(creds).length === 0) {
        ctx.addIssue({ code: "custom", path: ["credentials", "access_key_id"], message: S3_CREDENTIALS_REQUIRED_MESSAGE });
        return;
      }
      for (const issue of parsed.error.issues) {
        ctx.addIssue({ ...issue, path: ["credentials", ...issue.path] });
      }
    }
  });
```

Apply the same `protocol === "s3" && anonymous` short-circuit in the EDIT schema's existing `superRefine` (the one that validates `cleanRecord(data.credentials)` only when non-empty): when anonymous is on, skip credential validation entirely.

4. In `onSubmit`, after `const credentials = cleanRecord(data.credentials);`, drop credentials for anonymous s3:

```ts
    const anonymousS3 = protocol === "s3" && config.anonymous === true;
    // …create branch:
    createMutation.mutate({
      /* existing fields */,
      config,
      ...(anonymousS3 ? {} : { credentials }),
    } as ConnectionCreateInput, /* existing callbacks */);
```

and in the edit branch keep the existing "only send credentials when non-empty" logic, additionally never sending them when `anonymousS3`.

5. In the render, wrap the credentials section so it is hidden for anonymous s3. `watch` is already available from `useForm`; read `const anonymousS3 = protocol === "s3" && watch("config.anonymous") === true;` and render the credentials `<fieldset>` (heading, help text, fields) only when `!anonymousS3`. Replace the help copy for that state with nothing — the switch's own help text explains it.

- [ ] **Step 4: Run the form tests, then the whole app suite**

Run: `cd app && npx vitest run src/__tests__/connections-form.test.tsx && npm test`
Expected: PASS. Then `npm run check` (from `app/`) clean.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/connections/ConnectionForm.tsx app/src/__tests__/connections-form.test.tsx
git commit -m "feat(connections-ui): anonymous switch on the s3 form hides credential fields (G-1)"
```

---

### Task 5: Docs, FEATURES, verify, merge

**Files:**
- Modify: `docs/connections.md` ("Per-protocol `config`" and "Per-protocol `credentials`" tables; "Pipeline side")
- Modify: `docs/FEATURES.md` (the Connections row)
- Modify: `TODO.md` (tick G-1)

- [ ] **Step 1: Document**

In `docs/connections.md`, add to the s3 config row: `anonymous` (boolean, default `false`) — "public bucket: unsigned requests, credentials optional; e.g. NOAA NODD `noaa-goes19`". In the credentials section add one sentence: "For s3 with `anonymous: true` the credentials are optional and, if present, ignored by the adapter." In "Pipeline side" add: "`S3Adapter` builds an unsigned boto3 client (`botocore.UNSIGNED`) when `anonymous` is set; the egress policy still vets the endpoint host." Add a worked example:

```json
{ "protocol": "s3", "name": "NOAA NODD GOES-19", "group_id": "…",
  "config": { "bucket": "noaa-goes19", "region": "us-east-1", "anonymous": true } }
```

In `docs/FEATURES.md`, append to the Connections row: "Anonymous s3 connections (G-1, 2026-09): `config.anonymous` → unsigned requests, no credentials required (`s3-connection-config.json` fixture)."

- [ ] **Step 2: Full verification**

Run from the worktree root: `npm run verify`
Run from `services/pipeline/`: `uv run pytest && uv run ruff check .`
Expected: all green. Paste the summary lines into the commit body if anything needed attention.

- [ ] **Step 3: Commit, merge, clean up**

```bash
git add docs/connections.md docs/FEATURES.md
git commit -m "docs(connections): anonymous s3 connections (G-1)"
# tick G-1 in TODO.md, commit "chore(todo): G-1 done"
git checkout ai/main && git merge ai/goes-g1 --no-ff
git worktree remove .claude/worktrees/goes-g1 && git branch -d ai/goes-g1
```

Do NOT push `ai/main` (the lead keeps it local).
