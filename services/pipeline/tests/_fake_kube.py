"""A fake Kubernetes API server for the K-5 executor tests (plan Task 2).

One ``ThreadingHTTPServer`` on ``127.0.0.1:0``, plain HTTP (TLS is K-6's
kind leg). It serves the handful of endpoints the executor calls, and it
ENFORCES the Role in ``infra/kubernetes/pipeline-role.yaml``: a request the
Role does not grant answers ``403 Forbidden`` exactly as a real authorizer
would, so every executor test that passes against it proves the Role is
sufficient (K5-R10), and ``used`` lets ``test_kubernetes_rbac.py`` prove
every grant is necessary.

There is no controller loop. Tests move state with the controller-side
helpers (``set_job_status``, ``add_pod``, ``set_workload``); each bumps the
global ``resourceVersion`` and emits a watch event. With ``kueue`` on, a
queue-labelled Job is suspended at create and gets a Workload, as Kueue's
webhook and job reconciler do; with it off, the Kueue API answers 404.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import yaml

ROLE_PATH = Path(__file__).resolve().parents[3] / "infra" / "kubernetes" / "pipeline-role.yaml"
NAMESPACE = "stac-higher-runs"
KUEUE_GROUP = "kueue.x-k8s.io"
#: The Kueue API version this fake serves; pinned equal to the executor's.
KUEUE_API_VERSION = "v1beta2"

QUEUE_LABEL = "kueue.x-k8s.io/queue-name"
JOB_UID_LABEL = "kueue.x-k8s.io/job-uid"
POD_JOB_LABEL = "batch.kubernetes.io/job-name"

_KINDS = {
    ("batch", "jobs"): ("batch/v1", "Job"),
    ("", "pods"): ("v1", "Pod"),
    ("", "secrets"): ("v1", "Secret"),
    (KUEUE_GROUP, "workloads"): (f"{KUEUE_GROUP}/{KUEUE_API_VERSION}", "Workload"),
    ("networking.k8s.io", "networkpolicies"): ("networking.k8s.io/v1", "NetworkPolicy"),
}
#: The runs namespace's default-deny policy R-8 renders (the executor's
#: isolation precondition); the fake serves it unless told otherwise.
DEFAULT_DENY_POLICY = {
    "metadata": {"name": "stac-higher-runs-default-deny"},
    "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
}
_REASONS = {
    400: "BadRequest", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound",
    409: "AlreadyExists", 410: "Expired", 415: "UnsupportedMediaType", 422: "Invalid",
    500: "InternalError",
}
_BASE_TIME = dt.datetime(2026, 11, 26, 12, 0, 0, tzinfo=dt.UTC)


def _ts(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_body(code: int, message: str = "", reason: str | None = None) -> dict:
    return {
        "kind": "Status", "apiVersion": "v1", "metadata": {}, "status": "Failure",
        "message": message, "reason": reason or _REASONS.get(code, ""), "code": code,
    }


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    query: dict[str, str]
    verb: str
    group: str
    resource: str
    body: Any


class _Reply(Exception):
    def __init__(self, code: int, message: str = "", reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.body = status_body(code, message, reason)


def load_role_rules(path: Path = ROLE_PATH) -> set[tuple[str, str, str]]:
    """The Role's grants as (group, resource, verb) triples."""
    doc = yaml.safe_load(path.read_text())
    granted: set[tuple[str, str, str]] = set()
    for rule in doc["rules"]:
        for group in rule["apiGroups"]:
            for resource in rule["resources"]:
                for verb in rule["verbs"]:
                    granted.add((group, resource, verb))
    return granted


def _matches(labels: dict[str, str], selector: str) -> bool:
    for term in filter(None, (t.strip() for t in selector.split(","))):
        if "!=" in term:
            key, value = term.split("!=", 1)
            if labels.get(key.strip()) == value.strip():
                return False
        elif "=" in term:
            key, value = term.replace("==", "=").split("=", 1)
            if labels.get(key.strip()) != value.strip():
                return False
        elif term.startswith("!"):
            if term[1:] in labels:
                return False
        elif term not in labels:
            return False
    return True


class _Watch:
    def __init__(self, key: tuple[str, str], selector: str, rv: int, bookmarks: bool) -> None:
        self.key = key
        self.selector = selector
        self.rv = rv
        self.bookmarks = bookmarks
        self.pending: list[dict] = []
        self.closed = False


class FakeKube:
    """See the module docstring. A context manager."""

    def __init__(self, *, role_path: Path = ROLE_PATH, default_deny: bool = True) -> None:
        self.namespace = NAMESPACE
        self.token = "fake-token-" + secrets.token_hex(4)
        self.kueue = True
        #: Kueue creates the Workload later (``create_pending_workloads``).
        self.kueue_async = False
        #: A foreground Job delete keeps the Job, ``deletionTimestamp`` set,
        #: until ``finish_deletes`` (as the real GC does while pods go).
        self.linger_deletes = False
        self._pending_workloads: list[str] = []
        self._deleting: list[str] = []
        self.calls: list[Call] = []
        self.used: set[tuple[str, str, str]] = set()
        self.granted = load_role_rules(role_path)
        self._store: dict[tuple[str, str], dict[str, dict]] = {k: {} for k in _KINDS}
        self._logs: dict[str, bytes] = {}
        self._rv = 1
        self._compacted = 0
        self._ticks = 0
        self._failures: list[tuple[str, str, int, str, str]] = []
        self._events: list[tuple[int, tuple[str, str], str, dict]] = []
        self._watches: list[_Watch] = []
        self._cond = threading.Condition()
        self._stopping = False
        self._server: ThreadingHTTPServer | None = None
        if default_deny:
            with self._cond:
                self._put(
                    ("networking.k8s.io", "networkpolicies"), copy.deepcopy(DEFAULT_DENY_POLICY)
                )

    # -- lifecycle ------------------------------------------------------------

    def __enter__(self) -> FakeKube:
        fake = self

        class Handler(_Handler):
            server_fake = fake

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()
        return self

    def __exit__(self, *exc: object) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- state ----------------------------------------------------------------

    def objects(self, group: str, resource: str) -> dict[str, dict]:
        """The live store of one resource, by name (tests may mutate it)."""
        return self._store[(group, resource)]

    def _now(self) -> dt.datetime:
        self._ticks += 1
        return _BASE_TIME + dt.timedelta(seconds=self._ticks)

    def _emit(self, key: tuple[str, str], kind: str, obj: dict) -> None:
        """Bump the RV onto ``obj`` (except DELETED) and log the event.
        Caller holds ``_cond``."""
        self._rv += 1
        obj["metadata"]["resourceVersion"] = str(self._rv)
        snapshot = copy.deepcopy(obj)
        self._events.append((self._rv, key, kind, snapshot))
        for watch in self._watches:
            if watch.key == key and _matches(snapshot["metadata"].get("labels") or {},
                                             watch.selector):
                watch.pending.append({"type": kind, "object": snapshot})
        self._cond.notify_all()

    def _put(self, key: tuple[str, str], obj: dict, *, event: str = "ADDED") -> dict:
        api_version, kind = _KINDS[key]
        obj.setdefault("apiVersion", api_version)
        obj.setdefault("kind", kind)
        meta = obj.setdefault("metadata", {})
        meta.setdefault("namespace", self.namespace)
        meta.setdefault("uid", str(uuid.uuid4()))
        meta.setdefault("creationTimestamp", _ts(self._now()))
        self._store[key][meta["name"]] = obj
        self._emit(key, event, obj)
        return obj

    def _delete(self, key: tuple[str, str], name: str) -> dict | None:
        obj = self._store[key].pop(name, None)
        if obj is not None:
            self._emit(key, "DELETED", obj)
        return obj

    def _cascade(self, job: dict) -> None:
        uid = job["metadata"]["uid"]
        name = job["metadata"]["name"]
        for secret in list(self._store[("", "secrets")].values()):
            owners = secret["metadata"].get("ownerReferences") or []
            if any(o.get("uid") == uid for o in owners):
                self._delete(("", "secrets"), secret["metadata"]["name"])
        for pod in list(self._store[("", "pods")].values()):
            if (pod["metadata"].get("labels") or {}).get(POD_JOB_LABEL) == name:
                self._delete(("", "pods"), pod["metadata"]["name"])
                self._logs.pop(pod["metadata"]["name"], None)
        for wl in list(self._store[(KUEUE_GROUP, "workloads")].values()):
            if (wl["metadata"].get("labels") or {}).get(JOB_UID_LABEL) == uid:
                self._delete((KUEUE_GROUP, "workloads"), wl["metadata"]["name"])

    def _workload_for(self, job: str) -> dict | None:
        uid = self._store[("batch", "jobs")][job]["metadata"]["uid"]
        for wl in self._store[(KUEUE_GROUP, "workloads")].values():
            if wl["metadata"]["labels"].get(JOB_UID_LABEL) == uid:
                return wl
        return None

    def _create_workload(self, job: dict) -> dict:
        meta = job["metadata"]
        wl = {
            "metadata": {
                "name": f"job-{meta['name']}-{secrets.token_hex(3)[:5]}",
                "labels": {JOB_UID_LABEL: meta["uid"]},
                "ownerReferences": [{
                    "apiVersion": "batch/v1", "kind": "Job", "name": meta["name"],
                    "uid": meta["uid"], "controller": True, "blockOwnerDeletion": True,
                }],
            },
            "spec": {"active": True, "queueName": meta["labels"][QUEUE_LABEL]},
            "status": {"conditions": [{
                # Kueue v0.20's reason (K-6 A1); the executor reads only the
                # status and treats the reason as detail text.
                "type": "QuotaReserved", "status": "False", "reason": "WaitingForQuota",
                "message": "couldn't assign flavors to pod set main: insufficient quota",
                "lastTransitionTime": _ts(self._now()),
            }]},
        }
        return self._put((KUEUE_GROUP, "workloads"), wl)

    # -- controller-side helpers (tests) --------------------------------------

    def set_job_status(self, name: str, *, suspend: bool | None = None,
                       active: int | None = None, ready: int | None = None,
                       conditions: list[dict] | None = None,
                       start_time: dt.datetime | None = None) -> None:
        with self._cond:
            job = self._store[("batch", "jobs")][name]
            if suspend is not None:
                job["spec"]["suspend"] = suspend
            status = job.setdefault("status", {})
            if active is not None:
                status["active"] = active
            if ready is not None:
                status["ready"] = ready
            if conditions is not None:
                status["conditions"] = [
                    {"lastTransitionTime": _ts(self._now()), **c} for c in conditions
                ]
            if start_time is not None:
                status["startTime"] = _ts(start_time)
            self._emit(("batch", "jobs"), "MODIFIED", job)

    def add_pod(self, job: str, *, phase: str, state: dict | None = None,
                conditions: tuple[dict, ...] | list[dict] = (), reason: str | None = None,
                message: str | None = None, start_time: dt.datetime | None = None,
                log: bytes = b"", created: dt.datetime | None = None) -> str:
        """A pod of ``job`` (labels and spec from its template). ``state`` is
        the ``run`` container's state; ``created`` overrides the creation
        time (default: the fake's monotonic clock)."""
        with self._cond:
            owner = self._store[("batch", "jobs")][job]
            template = owner["spec"]["template"]
            name = f"{job}-{secrets.token_hex(3)[:5]}"
            labels = {**(template.get("metadata", {}).get("labels") or {}),
                      POD_JOB_LABEL: job, "job-name": job}
            status: dict[str, Any] = {"phase": phase, "conditions": list(conditions)}
            if reason:
                status["reason"] = reason
            if message:
                status["message"] = message
            if start_time is not None:
                status["startTime"] = _ts(start_time)
            if state is not None:
                status["containerStatuses"] = [{"name": "run", "state": state}]
            pod = {
                "metadata": {
                    "name": name, "labels": labels,
                    "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job",
                                         "name": job, "uid": owner["metadata"]["uid"],
                                         "controller": True}],
                },
                "spec": copy.deepcopy(template.get("spec") or {}),
                "status": status,
            }
            if created is not None:
                pod["metadata"]["creationTimestamp"] = _ts(created)
            self._put(("", "pods"), pod)
            self._logs[name] = log
            return name

    def set_workload(self, job: str, *, conditions: list[dict], active: bool = True) -> str:
        with self._cond:
            wl = self._workload_for(job)
            if wl is None:
                wl = self._create_workload(self._store[("batch", "jobs")][job])
            wl["spec"]["active"] = active
            wl["status"]["conditions"] = [
                {"lastTransitionTime": _ts(self._now()), **c} for c in conditions
            ]
            self._emit((KUEUE_GROUP, "workloads"), "MODIFIED", wl)
            return wl["metadata"]["name"]

    def fail_next(self, method: str, resource: str, status: int, *, reason: str = "",
                  message: str = "") -> None:
        """The next ``method`` on ``resource`` ("jobs", "secrets", "pods",
        "pods/log", "workloads") answers ``status`` with a Status body."""
        with self._cond:
            self._failures.append((method.upper(), resource, status, reason, message))

    def create_pending_workloads(self) -> None:
        """Kueue's job reconciler catching up (``kueue_async``)."""
        with self._cond:
            for name in self._pending_workloads:
                job = self._store[("batch", "jobs")].get(name)
                if job is not None:
                    self._create_workload(job)
            self._pending_workloads.clear()

    def finish_deletes(self) -> None:
        """The garbage collector completing every lingering foreground delete."""
        with self._cond:
            for name in self._deleting:
                job = self._delete(("batch", "jobs"), name)
                if job is not None:
                    self._cascade(job)
            self._deleting.clear()

    def compact(self) -> None:
        """Every resourceVersion up to now is compacted: a watch starting
        from one gets ``ERROR`` 410."""
        with self._cond:
            self._compacted = self._rv
            self._events.clear()

    def bookmark(self) -> None:
        """Every open watch that asked for bookmarks gets one at the current RV."""
        with self._cond:
            for watch in self._watches:
                if watch.bookmarks:
                    api_version, kind = _KINDS[watch.key]
                    watch.pending.append({"type": "BOOKMARK", "object": {
                        "apiVersion": api_version, "kind": kind,
                        "metadata": {"resourceVersion": str(self._rv)},
                    }})
            self._cond.notify_all()

    def close_watches(self) -> None:
        """End every open watch stream cleanly (the server's own close)."""
        with self._cond:
            for watch in self._watches:
                watch.closed = True
            self._cond.notify_all()

    @property
    def open_watches(self) -> int:
        with self._cond:
            return len(self._watches)


def _route(path: str) -> tuple[str, str | None, str, str | None, str | None]:
    """(group, namespace, resource, name, subresource) of an API path; the
    resource is "" for a path this fake does not serve."""
    parts = [p for p in path.split("/") if p]
    group = ""
    if parts[:2] == ["api", "v1"]:
        rest = parts[2:]
    elif parts[:1] == ["apis"] and len(parts) >= 3:
        group = parts[1]
        rest = parts[3:]
    else:
        return "", None, "", None, None
    namespace = None
    if rest[:1] == ["namespaces"] and len(rest) >= 3:
        namespace, rest = rest[1], rest[2:]
    if not rest:
        return group, namespace, "", None, None
    resource = rest[0]
    name = rest[1] if len(rest) > 1 else None
    sub = rest[2] if len(rest) > 2 else None
    return group, namespace, resource, name, sub


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_fake: FakeKube

    def log_message(self, format: str, *args: object) -> None:
        return

    # -- plumbing -------------------------------------------------------------

    def _send(self, code: int, body: Any, *, content_type: str = "application/json") -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_PUT(self) -> None:
        self._handle("PUT")

    # -- request --------------------------------------------------------------

    def _handle(self, method: str) -> None:
        fake = self.server_fake
        split = urlsplit(self.path)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        body = self._body()
        group, namespace, resource, name, sub = _route(split.path)
        if sub:
            # Review C10: every subresource is its own RBAC resource
            # (pods/log, pods/status, pods/ephemeralcontainers, jobs/status…),
            # exactly as the real authorizer sees it.
            resource = f"{resource}/{sub}"
        watching = query.get("watch") in ("1", "true")
        if method == "GET":
            verb = "get" if name else ("watch" if watching else "list")
        else:
            verb = {"POST": "create", "DELETE": "delete" if name else "deletecollection",
                    "PATCH": "patch", "PUT": "update"}[method]
        with fake._cond:
            fake.calls.append(Call(method, split.path, query, verb, group, resource, body))
        try:
            if self.headers.get("Authorization") != f"Bearer {fake.token}":
                raise _Reply(401, "Unauthorized")
            if namespace != fake.namespace or (group, resource, verb) not in fake.granted:
                raise _Reply(
                    403,
                    f'{resource or split.path} is forbidden: cannot {verb} resource '
                    f'"{resource}" in API group "{group}" in the namespace "{namespace}"',
                )
            with fake._cond:
                fake.used.add((group, resource, verb))
                for i, (f_method, f_resource, code, reason, message) in enumerate(fake._failures):
                    if f_method == method and f_resource == resource:
                        del fake._failures[i]
                        raise _Reply(code, message, reason or None)
            if group == KUEUE_GROUP and (
                not fake.kueue or split.path.split("/")[3] != KUEUE_API_VERSION
            ):
                raise _Reply(404, "the server could not find the requested resource")
            key = (group, resource.split("/")[0])
            if key not in _KINDS or ("/" in resource and resource != "pods/log"):
                raise _Reply(404, "the server could not find the requested resource")
            if verb == "watch":
                self._watch(key, query)
                return
            code, payload, ctype = self._operate(method, verb, key, name, query, body)
            self._send(code, payload, content_type=ctype)
        except _Reply as reply:
            self._send(reply.code, reply.body)

    def _operate(self, method: str, verb: str, key: tuple[str, str], name: str | None,
                 query: dict[str, str], body: Any) -> tuple[int, Any, str]:
        fake = self.server_fake
        store = fake._store[key]
        with fake._cond:
            if verb == "create":
                return 201, self._create(key, body), "application/json"
            if verb == "list":
                return 200, self._list(key, query), "application/json"
            if name is None:
                raise _Reply(405, "method not allowed")
            obj = store.get(name)
            if obj is None:
                raise _Reply(404, f'{key[1]} "{name}" not found')
            if verb == "get" and self.path.split("?")[0].endswith("/log"):
                return 200, self._log(obj, query), "text/plain"
            if verb == "get":
                return 200, copy.deepcopy(obj), "application/json"
            if verb == "delete":
                foreground = query.get("propagationPolicy") == "Foreground"
                if key == ("batch", "jobs") and fake.linger_deletes and foreground:
                    if not obj["metadata"].get("deletionTimestamp"):
                        obj["metadata"]["deletionTimestamp"] = _ts(fake._now())
                        fake._emit(key, "MODIFIED", obj)
                        fake._deleting.append(name)
                    return 200, copy.deepcopy(obj), "application/json"
                fake._delete(key, name)
                if key == ("batch", "jobs"):
                    fake._cascade(obj)
                return 200, copy.deepcopy(obj), "application/json"
            if verb == "patch":
                return 200, self._patch(key, obj, body), "application/json"
        raise _Reply(405, "method not allowed")

    def _create(self, key: tuple[str, str], body: Any) -> dict:
        fake = self.server_fake
        if not isinstance(body, dict) or not (body.get("metadata") or {}).get("name"):
            raise _Reply(422, "metadata.name: Required value")
        obj = copy.deepcopy(body)
        name = obj["metadata"]["name"]
        if name in fake._store[key]:
            raise _Reply(409, f'{key[1]} "{name}" already exists')
        if key == ("batch", "jobs"):
            if len(name) > 63:
                raise _Reply(422, f"metadata.name: Invalid value: {name!r}: must be no more "
                                  "than 63 characters")
            obj.setdefault("spec", {})
            obj["status"] = {}
            queued = QUEUE_LABEL in (obj["metadata"].get("labels") or {})
            if fake.kueue and queued:
                obj["spec"]["suspend"] = True
            fake._put(key, obj)
            if fake.kueue and queued:
                if fake.kueue_async:
                    fake._pending_workloads.append(name)
                else:
                    fake._create_workload(obj)
            return copy.deepcopy(obj)
        if key == ("", "secrets"):
            for value in (obj.get("data") or {}).values():
                try:
                    base64.b64decode(value, validate=True)
                except (ValueError, TypeError) as err:
                    raise _Reply(422, "data: Invalid value: not base64") from err
        fake._put(key, obj)
        return copy.deepcopy(obj)

    def _list(self, key: tuple[str, str], query: dict[str, str]) -> dict:
        fake = self.server_fake
        selector = query.get("labelSelector", "")
        items = [
            copy.deepcopy(obj) for _name, obj in sorted(fake._store[key].items())
            if _matches(obj["metadata"].get("labels") or {}, selector)
        ]
        start = int(query.get("continue") or 0)
        limit = int(query.get("limit") or 0)
        page = items[start:start + limit] if limit else items[start:]
        meta: dict[str, str] = {"resourceVersion": str(fake._rv)}
        if limit and start + limit < len(items):
            meta["continue"] = str(start + limit)
        api_version, kind = _KINDS[key]
        return {"apiVersion": api_version, "kind": f"{kind}List", "metadata": meta,
                "items": page}

    def _log(self, pod: dict, query: dict[str, str]) -> bytes:
        fake = self.server_fake
        states = [s.get("state") or {} for s in pod["status"].get("containerStatuses") or []]
        if not any("running" in s or "terminated" in s for s in states):
            raise _Reply(400, f'container "run" in pod "{pod["metadata"]["name"]}" is '
                              "waiting to start: ContainerCreating")
        data = fake._logs.get(pod["metadata"]["name"], b"")
        if query.get("limitBytes"):
            data = data[: int(query["limitBytes"])]
        return data

    def _patch(self, key: tuple[str, str], obj: dict, body: Any) -> dict:
        fake = self.server_fake
        if self.headers.get("Content-Type") != "application/merge-patch+json":
            raise _Reply(415, "the body of the request was in an unknown format")
        spec = body.get("spec") if isinstance(body, dict) else None
        if (
            key != ("", "pods") or set(body) != {"spec"} or not isinstance(spec, dict)
            or set(spec) != {"activeDeadlineSeconds"}
            or not isinstance(spec["activeDeadlineSeconds"], int)
            or spec["activeDeadlineSeconds"] < 1
        ):
            raise _Reply(422, "Pod is invalid: spec: Forbidden: pod updates may not change "
                              "fields other than activeDeadlineSeconds (lowered)")
        current = obj["spec"].get("activeDeadlineSeconds")
        if current is not None and spec["activeDeadlineSeconds"] > current:
            raise _Reply(422, "spec.activeDeadlineSeconds: Invalid value: must be lowered")
        obj["spec"]["activeDeadlineSeconds"] = spec["activeDeadlineSeconds"]
        fake._emit(key, "MODIFIED", obj)
        return copy.deepcopy(obj)

    # -- watch ----------------------------------------------------------------

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _watch(self, key: tuple[str, str], query: dict[str, str]) -> None:
        fake = self.server_fake
        selector = query.get("labelSelector", "")
        timeout = float(query["timeoutSeconds"]) if query.get("timeoutSeconds") else None
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        with fake._cond:
            raw_rv = query.get("resourceVersion")
            if raw_rv and int(raw_rv) < fake._compacted:
                gone = {"type": "ERROR", "object": status_body(
                    410, f"too old resource version: {raw_rv} ({fake._compacted})", "Expired")}
                watch = None
            else:
                gone = None
                start = int(raw_rv) if raw_rv else fake._rv
                watch = _Watch(key, selector, start,
                               query.get("allowWatchBookmarks") in ("1", "true"))
                if not raw_rv:
                    for obj in sorted(fake._store[key].values(),
                                      key=lambda o: o["metadata"]["name"]):
                        if _matches(obj["metadata"].get("labels") or {}, selector):
                            watch.pending.append({"type": "ADDED",
                                                  "object": copy.deepcopy(obj)})
                for rv, ev_key, kind, snapshot in fake._events:
                    if rv > start and ev_key == key and _matches(
                        snapshot["metadata"].get("labels") or {}, selector
                    ):
                        watch.pending.append({"type": kind, "object": copy.deepcopy(snapshot)})
                fake._watches.append(watch)
        try:
            if gone is not None:
                self._chunk(json.dumps(gone).encode() + b"\n")
                return
            assert watch is not None
            deadline = None if timeout is None else _monotonic() + timeout
            while True:
                with fake._cond:
                    while (not watch.pending and not watch.closed and not fake._stopping
                           and (deadline is None or _monotonic() < deadline)):
                        fake._cond.wait(0.05)
                    batch, watch.pending = watch.pending, []
                    done = (watch.closed or fake._stopping
                            or (deadline is not None and _monotonic() >= deadline))
                for event in batch:
                    self._chunk(json.dumps(event).encode() + b"\n")
                if done and not batch:
                    return
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            with fake._cond:
                if watch is not None and watch in fake._watches:
                    fake._watches.remove(watch)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass


def _monotonic() -> float:
    import time

    return time.monotonic()
