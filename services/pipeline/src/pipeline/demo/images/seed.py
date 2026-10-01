"""`python -m pipeline.demo images-seed | images-status | images-teardown`
(C-5, container-images spec, GitHub #54): the images live-gate scenario.

Where `goes-seed` writes its GOES rows straight through SQL, this seeder adds
its demo images through the APP's `/api/images` HTTP surface (the same path
an operator uses on the dashboard), then deploys two processes that snapshot
whatever the app hands back:

    goes-geocolor-img   a kind-2 twin of goes-seed's `goes-geocolor`: the SAME
                         GeoColor code, on an APPROVED user image instead of
                         the built-in runtime, triggered by `goes-abi-mcmipc`
    images-canary       a second kind-2 process on the SLIM image, no outputs,
                         just enough to prove a small user image also deploys

Needs `goes-seed` run first (its source collection), and the app running
against the same database (the image registry, migration 030, and the
`/api/images*` routes are the app's).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, NamedTuple

import psycopg

from pipeline.demo.goes import geocolor_code
from pipeline.demo.goes.seed import GEOCOLOR_RUNTIME, GROUP, SOURCE_COLLECTION, TRIGGER
from pipeline.demo.images import canary_code
from pipeline.demo.platform import (
    check_migrations,
    enable_serving,
    install_process,
    json_request,
    put_collection,
    remove_process,
    request,
    say,
)

REQUIRED_MIGRATION = "030_container_images"

CREATED_BY = "images-seed"
OUTPUT_COLLECTION = "goes-geocolor-img"
GEOCOLOR_IMG_NAME = "goes-geocolor-img"
CANARY_NAME = "images-canary"

GEOCOLOR_IMG_ID = "60e50000-0000-4000-8000-000000000011"
GEOCOLOR_IMG_REVISION_ID = "60e50000-0000-4000-8000-000000000012"
CANARY_ID = "60e50000-0000-4000-8000-000000000013"
CANARY_REVISION_ID = "60e50000-0000-4000-8000-000000000014"

RUNTIME_IMAGE = "ghcr.io/techtraverse/stac-higher-process-runtime:latest"
SLIM_IMAGE = "python:3.12-slim"
LARGE_IMAGE = "python:3.12"
KEV_IMAGE = "vulnerables/cve-2014-6271"

EXCEPTION_REASON = "images-seed: platform runtime image for the kind-2 GOES demo (C-5)"

#: The statuses a scan settles into (spec §4.3); anything else keeps polling.
TERMINAL_STATUSES = frozenset({"approved", "rejected", "flagged", "revoked", "scan_failed"})


class SeedError(Exception):
    """A one-line, user-facing reason the seed cannot proceed."""


# --------------------------------------------------------------------------- #
# the app's reference grammar (app/src/lib/images/normalize.ts), just enough
# of it to split the FIXED set of demo images above into the normalized
# (reference, tag) pair the registry stores. Not a general parser: it exists
# so `find_image` can match a row the app itself normalized.
# --------------------------------------------------------------------------- #

_DOCKER_HUB_ALIASES = frozenset(
    {"docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com"}
)
_DEFAULT_TAG = "latest"


def _canonical_host(host: str) -> str:
    lower = host.strip().lower()
    return "docker.io" if lower in _DOCKER_HUB_ALIASES else lower


def _split_reference(typed: str) -> tuple[str, str]:
    last_slash = typed.rfind("/")
    last_colon = typed.rfind(":")
    if last_colon > last_slash:
        name, tag = typed[:last_colon], typed[last_colon + 1 :]
    else:
        name, tag = typed, _DEFAULT_TAG
    parts = name.split("/")
    first = parts[0]
    has_host = len(parts) > 1 and ("." in first or ":" in first or first.lower() == "localhost")
    host = _canonical_host(first) if has_host else "docker.io"
    path = [part.lower() for part in (parts[1:] if has_host else parts)]
    if host == "docker.io" and len(path) == 1:
        path = ["library", *path]
    return "/".join([host, *path]), tag


def _demo_reference_tag_pairs() -> list[tuple[str, str]]:
    images = (RUNTIME_IMAGE, SLIM_IMAGE, LARGE_IMAGE, KEV_IMAGE)
    return [_split_reference(image) for image in images]


# --------------------------------------------------------------------------- #
# the app client
# --------------------------------------------------------------------------- #


class AppError(Exception):
    """A non-2xx response from the app."""

    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body[:200]}")
        self.status = status
        self.body = body


def pick_image(images: list[dict], reference: str, tag: str | None) -> dict | None:
    """The NEWEST non-revoked row for `(reference, tag)` (tag None: any tag).

    A re-pushed tag (the platform runtime's `:latest` after a rebuild) gets a
    second row when it is re-added; the old row may still be approved through
    an exception. Newest wins, even mid-scan: the seed then waits for that
    scan rather than silently deploying on the older digest. A revoked row is
    terminal and never picked."""
    live = [
        image
        for image in images
        if image.get("reference") == reference
        and (tag is None or image.get("tag_at_add") == tag)
        and image.get("status") != "revoked"
    ]
    return max(live, key=lambda image: image.get("created_at") or "", default=None)


class AppClient:
    """The slice of `/api/images*` the seed needs. Every request carries the
    `Origin` header the app's CSRF check requires (every mutating route
    rejects a cross-origin POST) and, when set, a bearer token."""

    def __init__(self, base_url: str, bearer: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.bearer = bearer

    def _headers(self) -> dict[str, str]:
        headers = {"Origin": self.base_url}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        return headers

    def _call(self, path: str, *, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
        status, payload = json_request(
            f"{self.base_url}{path}", method=method, body=body, headers=self._headers()
        )
        if status >= 300:
            raise AppError(status, payload.decode(errors="replace"))
        return json.loads(payload) if payload else {}

    def find_image(self, reference: str, tag: str | None) -> dict | None:
        query = reference if tag is None else f"{reference}:{tag}"
        result = self._call(f"/api/images?q={urllib.parse.quote(query)}")
        return pick_image(result.get("images", []), reference, tag)

    def add_image(self, reference: str) -> dict:
        # The app's POST answers 202 with the admission it opened (id,
        # normalized reference/tag, scan id), a SMALLER shape than the
        # ApiImage `get_image`/`find_image` return (no status, no digest).
        # Reported deviation from the brief's "returns the created image
        # dict": see the Task 1/2 report.
        return self._call("/api/images", method="POST", body={"reference": reference})

    def get_image(self, image_id: str) -> dict:
        # GET /api/images/[id] answers `{image, scans, in_use_by, ...}`, not
        # a bare image: unwrapped here so every caller sees one ApiImage
        # shape regardless of which AppClient method produced it.
        result = self._call(f"/api/images/{image_id}")
        return result["image"]

    def grant_exception(self, image_id: str, reason: str, expires_at: str) -> dict:
        result = self._call(
            f"/api/images/{image_id}/exception",
            method="POST",
            body={"reason": reason, "expires_at": expires_at},
        )
        return result["image"]


class DemoImage(NamedTuple):
    key: str
    reference: str  # as typed by a user, e.g. "python:3.12-slim"


def ensure_image(client: AppClient, demo: DemoImage) -> dict:
    """Find-or-add one demo image. A revoked row is terminal (Review Focus
    #4): it is never reused, so a fresh add follows it. The add response is
    normalized into a full ApiImage dict via `get_image`, so every caller
    downstream (wait_for_scans, kind2_runtime, require_deployable) sees the
    same shape whether the image was reused or just added."""
    reference, tag = _split_reference(demo.reference)
    existing = client.find_image(reference, tag)
    if existing is not None:
        return existing
    added = client.add_image(demo.reference)
    return client.get_image(added["id"])


def wait_for_scans(
    client: AppClient,
    ids: list[str],
    *,
    timeout_s: int,
    poll_s: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    find_by: dict[str, tuple[str, str]] | None = None,
) -> dict[str, dict]:
    """Poll each id's image until every one is terminal (spec §4.3) or the
    timeout passes. Returns the last dict seen per id either way (keyed by
    the ORIGINAL id passed in), so the caller can still report a
    `pending`/`scanning` image once time is up.

    A provisional admission can be folded into an existing row by digest
    while a scan is in flight (spec §9.1's de-duplication): the drain
    deletes the provisional row, so `get_image(id)` 404s for it from then on.
    `find_by` maps each id to its `(reference, tag)` pair so a 404 can be
    resolved once by re-finding the surviving row; without an entry there
    (or when the re-find comes up empty), a 404 is a clear `SeedError`
    rather than a raw `AppError`/traceback.
    """
    find_by = find_by or {}
    lookup_id = {image_id: image_id for image_id in ids}
    deadline = clock() + timeout_s
    last: dict[str, dict] = {}
    while True:
        for original_id in ids:
            try:
                last[original_id] = client.get_image(lookup_id[original_id])
            except AppError as err:
                if err.status != 404:
                    raise
                reference_tag = find_by.get(original_id)
                found = client.find_image(*reference_tag) if reference_tag else None
                if found is None:
                    raise SeedError(
                        f"image {original_id} vanished mid-scan (folded into another row "
                        "by the drain, and no row matches its reference/tag); re-run"
                        " images-seed"
                    ) from err
                lookup_id[original_id] = found["id"]
                last[original_id] = found
        if all(image["status"] in TERMINAL_STATUSES for image in last.values()):
            return last
        if clock() >= deadline:
            return last
        sleep(poll_s)


def image_snapshot(image: dict) -> dict:
    return {"id": image["id"], "reference": image["reference"], "digest": image["digest"]}


def kind2_runtime(image: dict, base: dict) -> dict:
    runtime = dict(base)
    runtime["kind"] = "inline_python_on_image"
    runtime["image"] = image_snapshot(image)
    return runtime


def require_deployable(image: dict, name: str) -> None:
    reference = image.get("reference", "?")
    status = image["status"]
    if status in ("pending", "scanning"):
        raise SeedError(
            f"{name} ({reference}) is still scanning;"
            " re-run without --no-wait (or later)"
        )
    if status == "scan_failed":
        # The exception route 409s on a scan_failed image (only rejected,
        # flagged, or an approved row with an exception to replace take one),
        # so the fix here is always a rescan, never --exception-days.
        raise SeedError(f"{name} ({reference}) failed to scan; select Rescan now on /images")
    if status != "approved":
        raise SeedError(
            f"{name} ({reference}) is {status}, not approved;"
            " grant an exception (--exception-days) or wait for a passing rescan"
        )
    if image.get("stale"):
        raise SeedError(f"{name} ({reference}) is stale: rescan it on /images")


def _output_collection_document() -> dict:
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": OUTPUT_COLLECTION,
        "description": (
            "GeoColor COGs from goes-geocolor-img: the goes-geocolor code on an "
            "approved user image (kind 2, C-5)."
        ),
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-152.0, 14.0, -52.0, 57.0]]},
            "temporal": {"interval": [["2025-04-07T00:00:00Z", None]]},
        },
        "links": [],
    }


def _expires_at(days: int) -> str:
    when = dt.datetime.now(dt.UTC) + dt.timedelta(days=days)
    return when.replace(microsecond=0).isoformat()


def _auth_hint(reference: str, err: AppError) -> str:
    return (
        f"the app answered {err} on {reference}: it needs an operator identity"
        ' (DEV_AUTH_IDENTITY=\'{"roles":["operator"]}\') or a valid --bearer token'
    )


def _selected_demo_images(args: argparse.Namespace) -> list[DemoImage]:
    demos = [DemoImage("runtime", RUNTIME_IMAGE), DemoImage("slim", SLIM_IMAGE)]
    if getattr(args, "with_large", False):
        demos.append(DemoImage("large", LARGE_IMAGE))
    if getattr(args, "with_kev", False):
        demos.append(DemoImage("kev", KEV_IMAGE))
    return demos


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #


def seed(args: argparse.Namespace) -> int:
    check_migrations(args.database_url, REQUIRED_MIGRATION)

    status, _ = request(f"{args.stac_url}/collections/{SOURCE_COLLECTION}")
    if status != 200:
        raise SeedError(
            f"run `python -m pipeline.demo goes-seed` first: {SOURCE_COLLECTION} is missing"
        )

    client = AppClient(args.app_url, getattr(args, "bearer", None))
    demos = _selected_demo_images(args)

    say("images")
    images: dict[str, dict] = {}
    for demo in demos:
        try:
            image = ensure_image(client, demo)
        except AppError as err:
            if err.status in (401, 403):
                raise SeedError(_auth_hint(demo.reference, err)) from err
            raise
        images[demo.key] = image
        say(f"  image {demo.reference}: {image['status']}")

    if not getattr(args, "no_wait", False):
        say("waiting for scans")
        ids = [image["id"] for image in images.values()]
        find_by = {
            image["id"]: (image["reference"], image["tag_at_add"]) for image in images.values()
        }
        updated = wait_for_scans(client, ids, timeout_s=args.scan_timeout, find_by=find_by)
        for demo in demos:
            image = updated[images[demo.key]["id"]]
            images[demo.key] = image
            reasons = ((image.get("verdict") or {}).get("reasons") or [])[:3]
            say(f"  {demo.reference}: {image['status']} {reasons}")

    runtime_image = images["runtime"]
    exception_days = getattr(args, "exception_days", None)
    if runtime_image["status"] in ("rejected", "flagged") and exception_days:
        try:
            client.grant_exception(
                runtime_image["id"], EXCEPTION_REASON, _expires_at(exception_days)
            )
        except AppError as err:
            if err.status == 403:
                raise SeedError(
                    "granting an exception needs an ADMIN app identity: start the dev"
                    ' server with DEV_AUTH_IDENTITY=\'{"roles":["admin"]}\''
                    f" (app answered {err})"
                ) from err
            raise
        say(f"  runtime image: exception granted ({exception_days} day(s))")

    runtime_image = client.get_image(images["runtime"]["id"])
    slim_image = client.get_image(images["slim"]["id"])
    try:
        require_deployable(runtime_image, "runtime image")
        require_deployable(slim_image, "slim image")
    except SeedError as err:
        say(str(err))
        return 2

    say("catalog")
    put_collection(args.stac_url, _output_collection_document())

    say("platform")
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        enable_serving(conn, OUTPUT_COLLECTION, GROUP)
        say("  OGC serving enabled")

        install_process(
            conn,
            process_id=GEOCOLOR_IMG_ID,
            revision_id=GEOCOLOR_IMG_REVISION_ID,
            name=GEOCOLOR_IMG_NAME,
            description=(
                "C-5: a GeoColor-style true-colour COG per ABI MCMIPC granule, "
                "running the goes-geocolor code on an approved user image "
                "instead of the built-in runtime (kind 2)."
            ),
            group=GROUP,
            created_by=CREATED_BY,
            kind="transform",
            max_runs_per_hour=120,
            runtime=kind2_runtime(runtime_image, GEOCOLOR_RUNTIME),
            code=geocolor_code(OUTPUT_COLLECTION),
            sources=((SOURCE_COLLECTION, TRIGGER),),
            outputs=(OUTPUT_COLLECTION,),
        )
        say(f"  process {GEOCOLOR_IMG_NAME} deployed ({SOURCE_COLLECTION} -> {OUTPUT_COLLECTION})")

        install_process(
            conn,
            process_id=CANARY_ID,
            revision_id=CANARY_REVISION_ID,
            name=CANARY_NAME,
            description=(
                "C-5: a minimal kind-2 canary; proves a small (slim) user image "
                "deploys and runs, no outputs."
            ),
            group=GROUP,
            created_by=CREATED_BY,
            kind="transform",
            max_runs_per_hour=60,
            runtime=kind2_runtime(
                slim_image, GEOCOLOR_RUNTIME | {"memory_mb": 256, "timeout_seconds": 60}
            ),
            code=canary_code(),
            sources=((SOURCE_COLLECTION, TRIGGER),),
        )
        say(f"  process {CANARY_NAME} deployed (no outputs)")

    app_url = args.app_url.rstrip("/")
    say("")
    say(f"UI:    {app_url}/images")
    say(f"       {app_url}/processes")
    say(f"       {app_url}/collections/{OUTPUT_COLLECTION}/items")
    say("Strict policy (flags an image the default passes):")
    say(
        "  docker compose -f docker-compose.yml"
        " -f infra/compose.strict-image-policy.yml up -d pipeline"
    )
    return 0


# --------------------------------------------------------------------------- #
# status / teardown
# --------------------------------------------------------------------------- #


def status(args: argparse.Namespace) -> int:
    client = AppClient(args.app_url, getattr(args, "bearer", None))

    say("images")
    for image_ref in (RUNTIME_IMAGE, SLIM_IMAGE, LARGE_IMAGE, KEV_IMAGE):
        reference, tag = _split_reference(image_ref)
        image = client.find_image(reference, tag)
        if image is None:
            continue
        reasons = ((image.get("verdict") or {}).get("reasons") or [])[:3]
        digest = (image.get("digest") or "")[:19]
        exception = image.get("exception")
        expiry = f", exception until {exception['expires_at']}" if exception else ""
        say(
            f"  {image['reference']}:{image['tag_at_add']}  {image['status']}"
            f"  {digest}  last scanned {image.get('last_scanned_at')}{expiry}  {reasons}"
        )

    with psycopg.connect(args.database_url) as conn:
        # Two LIMIT 5 queries rather than one LIMIT 10 across both processes,
        # so a chatty canary run history cannot crowd the geocolor twin's
        # runs (or vice versa) out of the last-five-each report.
        runs: list[tuple] = []
        for process_id, name in ((GEOCOLOR_IMG_ID, GEOCOLOR_IMG_NAME), (CANARY_ID, CANARY_NAME)):
            runs.extend(
                conn.execute(
                    "SELECT %s, status, to_char(created_at, 'HH24:MI:SS'),"
                    "       round(extract(epoch from (finished_at - started_at)))"
                    "  FROM stac_higher.process_runs"
                    " WHERE process_id = %s"
                    " ORDER BY created_at DESC LIMIT 5",
                    (name, process_id),
                ).fetchall()
            )
        alerts = conn.execute(
            "SELECT message, first_seen FROM stac_higher.alerts"
            " WHERE kind = 'process_image_flagged' AND process_id = ANY(%s)"
            "   AND resolved_at IS NULL"
            " ORDER BY first_seen DESC",
            ([GEOCOLOR_IMG_ID, CANARY_ID],),
        ).fetchall()

    if not runs:
        say("no runs yet")
    else:
        say("recent runs (newest first)")
        for name, run_status, created, seconds in runs:
            elapsed = seconds if seconds is not None else "-"
            say(f"  {name:<18} {run_status:<10} {created:>10} {elapsed!s:>6}s")

    if alerts:
        say("open process_image_flagged alerts")
        for message, first_seen in alerts:
            say(f"  {first_seen}: {message}")

    code, body = request(f"{args.stac_url}/collections/{OUTPUT_COLLECTION}/items?limit=1")
    if code == 200:
        parsed = json.loads(body)
        count = parsed.get("numberMatched")
        if count is None:
            code2, body2 = request(
                f"{args.stac_url}/collections/{OUTPUT_COLLECTION}/items?limit=1000"
            )
            count = len(json.loads(body2).get("features", [])) if code2 == 200 else -1
        say(f"{OUTPUT_COLLECTION}: {count} item(s)")
    else:
        say(f"{OUTPUT_COLLECTION}: not found ({code})")
    return 0


def teardown(args: argparse.Namespace) -> int:
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        if not getattr(args, "force", False):
            running = conn.execute(
                "SELECT DISTINCT process_id FROM stac_higher.process_runs"
                " WHERE process_id = ANY(%s) AND status IN ('queued', 'running')",
                ([GEOCOLOR_IMG_ID, CANARY_ID],),
            ).fetchall()
            if running:
                raise SeedError(
                    "a run is queued or in progress for "
                    + ", ".join(str(row[0]) for row in running)
                    + "; wait for it to finish or pass --force"
                )

        remove_process(conn, CANARY_ID)
        remove_process(conn, GEOCOLOR_IMG_ID)
        conn.execute(
            "DELETE FROM stac_higher.collection_settings WHERE collection_id = %s",
            (OUTPUT_COLLECTION,),
        )

        if getattr(args, "images", False):
            # (reference, tag_at_add) pairs, not reference alone: matching on
            # reference only would also catch a user's own `python:3.11` or
            # `python:3.12` row that merely shares the demo's repository. This
            # still matches (and, with --yes, deletes) a row the SEED merely
            # found already registered (a user's own python:3.12-slim, or the
            # runtime row with its admin exception): it is not scoped to
            # rows images-seed itself created. Deleting bypasses the app
            # entirely and writes no audit row, so list-then-confirm: without
            # --yes this only prints what WOULD be deleted.
            pairs = _demo_reference_tag_pairs()
            references = [reference for reference, _ in pairs]
            tags = [tag for _, tag in pairs]
            rows = conn.execute(
                "SELECT i.id, i.reference, i.tag_at_add, i.status,"
                "       EXISTS (SELECT 1 FROM stac_higher.process_revisions r"
                "                WHERE r.runtime->'image'->>'id' = i.id::text) AS in_use"
                "  FROM stac_higher.container_images i"
                " WHERE (i.reference, i.tag_at_add) IN ("
                "   SELECT * FROM unnest(%s::text[], %s::text[]))",
                (references, tags),
            ).fetchall()
            unused = [row for row in rows if not row[4]]
            used = [row for row in rows if row[4]]
            if getattr(args, "yes", False):
                to_delete = [row[0] for row in unused]
                if to_delete:
                    conn.execute(
                        "DELETE FROM stac_higher.container_images WHERE id = ANY(%s)",
                        (to_delete,),
                    )
                say(f"  {len(to_delete)} image row(s) deleted")
                for image_id, reference, tag, image_status, _ in unused:
                    say(f"    deleted {reference}:{tag}  {image_id}  {image_status}")
            else:
                say(f"  {len(unused)} image row(s) match and are unused:")
                for image_id, reference, tag, image_status, _ in unused:
                    say(f"    {reference}:{tag}  {image_id}  {image_status}")
                if unused:
                    say("  re-run with --yes to delete these rows")
            for _, reference, tag, image_status, _ in used:
                say(
                    f"  kept {reference}:{tag} ({image_status}):"
                    " still in use by a process revision"
                )

    say("platform rows removed")

    code, _ = request(f"{args.stac_url}/collections/{OUTPUT_COLLECTION}", method="DELETE")
    say(f"  collection {OUTPUT_COLLECTION}: {code}")
    return 0
