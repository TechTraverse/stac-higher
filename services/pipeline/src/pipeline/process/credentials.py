"""Run-scoped storage credentials (spec §5, ADR 0013/0014).

The settled mechanic: mint **STS session credentials with an inline session
policy** that restricts S3 to the run's own staging prefix
(``staging/runs/{run_id}/``) plus read-only access to the canonical prefixes
of its SOURCE collections (GOES spec §3.2) and nothing else. The run can
write its outputs and read them back, read the platform-held inputs it was
triggered by, and cannot see another run's bytes, the rest of the canonical
``assets/`` tree, or any other bucket.

Two properties are load-bearing and worth stating plainly:

- **The platform's own keys never reach the run.** The worker holds them to
  CALL STS; what it hands the container is a short-lived derived credential.
  A leak of the run's environment leaks minutes of access to one prefix.
- **No silent widening.** If a deployment's object store has no STS, this
  raises. The tempting fallback — pass the platform keys "just for local dev"
  — would make the boundary a lie in exactly the environment where it is
  first exercised, so it is not offered (spec §5).

Expiry is the run's timeout plus a grace window: long enough that a run using
its full budget can still finish a final upload, short enough that a leaked
credential ages out on its own. A deployment may cap that duration
(``PROCESS_MAX_CREDENTIAL_SECONDS``): a run that would need more is refused at
mint with :class:`CredentialBudgetExceeded` -- never clamped, because a
refusal beats credentials expiring mid-run.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from pipeline.config import STS_MIN_DURATION_SECONDS, Settings
from pipeline.storage.keys import run_staging_prefix
from pipeline.storage.platform import pinned_endpoint_url, static_s3_credentials

#: STS minimum is 900s; a shorter ask is rejected by AWS outright.
MIN_DURATION_SECONDS = STS_MIN_DURATION_SECONDS

#: Real STS caps an inline session policy at 2048 characters; each read
#: prefix costs two ARN-ish strings. Eight keeps a worst-case policy under the
#: cap with room for long collection ids (GOES spec §14, ISSUES I-90).
MAX_READ_PREFIXES = 8

#: The commercial partition -- what an unparseable role ARN falls back to.
DEFAULT_PARTITION = "aws"

_ARN_PARTITION = re.compile(r"^arn:([a-z][a-z0-9-]*):")


class RunCredentialsError(Exception):
    """Run-scoped credentials could not be minted — the run must not start."""


class CredentialBudgetExceeded(Exception):
    """The run's credentials would outlive ``PROCESS_MAX_CREDENTIAL_SECONDS``.

    Deliberately NOT a :class:`RunCredentialsError`: that one is our
    infrastructure failing and is requeued without spending an attempt, whereas
    this is configuration no retry fixes -- the runner finishes the run dead.
    """

    code: ClassVar[str] = "credential_budget_exceeded"

    def __init__(
        self,
        *,
        requested_seconds: int,
        max_seconds: int,
        timeout_seconds: int,
        grace_seconds: int,
        queue_wait_seconds: int = 0,
    ) -> None:
        self.requested_seconds = requested_seconds
        self.max_seconds = max_seconds
        self.timeout_seconds = timeout_seconds
        self.grace_seconds = grace_seconds
        self.queue_wait_seconds = queue_wait_seconds
        super().__init__(
            f"{self.code}: run credentials would need {requested_seconds} s "
            f"({_budget_terms(queue_wait_seconds, timeout_seconds, grace_seconds)}, "
            f"STS minimum {MIN_DURATION_SECONDS} s) but "
            f"PROCESS_MAX_CREDENTIAL_SECONDS is {max_seconds} s"
        )


def _budget_terms(queue_wait_seconds: int, timeout_seconds: int, grace_seconds: int) -> str:
    """K-4 (spec §5.4): the duration's terms, as both refusals word them."""
    return (
        f"max_queue_wait {queue_wait_seconds} s + timeout {timeout_seconds} s "
        f"+ grace {grace_seconds} s"
    )


#: How much of STS's own text a duration rejection keeps: enough for "exceeds
#: the MaxSessionDuration" vs "role chaining", never a whole echoed request.
_STS_MESSAGE_LIMIT = 200


class CredentialDurationRejected(CredentialBudgetExceeded):
    """STS itself refused the requested ``DurationSeconds`` (#157).

    The cap was unset, or set above what the role allows (its
    ``MaxSessionDuration``; one hour under role chaining), so the mint got
    past :class:`CredentialBudgetExceeded`'s own check and STS answered
    ``ValidationError``. Same remedy, same outcome: a subclass so the runner
    finishes the run dead instead of requeueing it forever as infrastructure.
    """

    def __init__(
        self,
        *,
        requested_seconds: int,
        max_seconds: int | None,
        timeout_seconds: int,
        grace_seconds: int,
        sts_message: str,
        queue_wait_seconds: int = 0,
    ) -> None:
        self.requested_seconds = requested_seconds
        self.max_seconds = max_seconds
        self.timeout_seconds = timeout_seconds
        self.grace_seconds = grace_seconds
        self.queue_wait_seconds = queue_wait_seconds
        if len(sts_message) > _STS_MESSAGE_LIMIT:
            sts_message = sts_message[:_STS_MESSAGE_LIMIT] + "..."
        current = "unset" if max_seconds is None else f"{max_seconds} s"
        Exception.__init__(
            self,
            f"{self.code}: STS refused run credentials of {requested_seconds} s "
            f"({_budget_terms(queue_wait_seconds, timeout_seconds, grace_seconds)}): "
            f"{sts_message} "
            f"Set PROCESS_MAX_CREDENTIAL_SECONDS (now {current}) to at most the role's "
            "maximum session duration (its MaxSessionDuration; 3600 s when the "
            "pipeline's own credentials are an assumed role, i.e. role chaining) so "
            "such revisions are refused at deploy, or raise the role's MaxSessionDuration.",
        )


def _sts_duration_rejection(err: Exception) -> str | None:
    """STS's message when ``err`` is its ``ValidationError`` about
    ``DurationSeconds``; None for every other failure, which stays
    :class:`RunCredentialsError`. Matches both AWS phrasings: "The requested
    DurationSeconds exceeds ..." and "Value '...' at 'durationSeconds' ..."."""
    if not isinstance(err, ClientError):
        return None
    error = err.response.get("Error") or {}
    message = str(error.get("Message") or "")
    if error.get("Code") == "ValidationError" and "durationseconds" in message.lower():
        return message
    return None


def _validate_scoped_prefix(prefix: str, *, what: str) -> None:
    """A prefix bound into an STS inline session policy (``session_policy``)
    must name exactly one sub-tree: non-empty, trailing-slash-terminated, and
    free of the glob characters ``session_policy`` itself appends (``*``) or
    that S3's ``StringLike`` condition would otherwise interpret (``?``).
    Anything else would widen -- or, for an empty string, ELIMINATE -- the
    boundary the policy exists to hold (final-review fix wave item 1)."""
    if not prefix or not prefix.endswith("/") or "*" in prefix or "?" in prefix:
        raise RunCredentialsError(
            f"{what} is not a safe STS session-policy prefix -- it must be a "
            "non-empty string ending with '/' and containing neither '*' nor '?'"
        )


@dataclass(frozen=True)
class RunCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    bucket: str
    prefix: str
    endpoint_url: str | None
    region: str
    #: K-4: a LOWER BOUND on when these expire — the clock read before the
    #: STS call plus the duration asked for. Recorded on the run row
    #: (`credentials_expire_at`); never part of the run's env (ADR 0013).
    expires_at: dt.datetime | None = None

    def as_env(self) -> dict[str, str]:
        """The subset of the run's environment that concerns storage.

        Standard AWS names so ordinary tooling inside the run (boto3, awscli,
        obstore) picks them up with no special casing, plus the two
        platform-specific values telling the code WHERE it is allowed to
        write — which it could not discover otherwise, since listing the
        bucket is denied.
        """
        env = {
            "AWS_ACCESS_KEY_ID": self.access_key_id,
            "AWS_SECRET_ACCESS_KEY": self.secret_access_key,
            "AWS_SESSION_TOKEN": self.session_token,
            "AWS_REGION": self.region,
            "AWS_DEFAULT_REGION": self.region,
            "STAC_HIGHER_OUTPUT_BUCKET": self.bucket,
            "STAC_HIGHER_OUTPUT_PREFIX": self.prefix,
        }
        if self.endpoint_url:
            env["AWS_ENDPOINT_URL"] = self.endpoint_url
            env["AWS_ENDPOINT_URL_S3"] = self.endpoint_url
        return env


def arn_partition(arn: str) -> str:
    """The partition (``aws``, ``aws-us-gov``, ``aws-cn``) an ARN names.

    The session policy takes its partition from the role ARN the run assumes
    (#158): STS cannot assume a role across partitions, so the role and the
    bucket it scopes always share one, and the ARN is already in hand -- no
    ``GetCallerIdentity`` round trip and no new way for a mint to fail.

    A value that is not an ARN falls back to the commercial partition. That is
    fail-closed, not a widening: MinIO ignores ``RoleArn`` entirely, and real
    STS rejects a malformed one before any policy is evaluated.
    """
    match = _ARN_PARTITION.match(arn or "")
    return match.group(1) if match else DEFAULT_PARTITION


def session_policy(
    bucket: str,
    prefix: str,
    read_prefixes: Sequence[str] = (),
    *,
    partition: str = DEFAULT_PARTITION,
) -> dict:
    """The inline policy bounding one run.

    Object actions are scoped to the run's prefix. ``read_prefixes`` — the
    canonical ``assets/{collection}/`` prefixes of the run's SOURCE collections
    (GOES spec §3.2) — get ``s3:GetObject`` only. ListBucket is granted on the
    BUCKET resource — that is where S3 evaluates it — conditioned on the run
    prefix plus the read prefixes, so a run can enumerate its own outputs and
    its inputs without discovering that anything else exists.

    ``partition`` must match the bucket's: an ``arn:aws:s3:::`` resource
    matches nothing in GovCloud, and the credentials would grant nothing.
    """
    bucket_arn = f"arn:{partition}:s3:::{bucket}"
    list_prefixes = [f"{prefix}*", *(f"{p}*" for p in read_prefixes)]
    statements = [
        {
            "Sid": "RunPrefixObjects",
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
            "Resource": [f"{bucket_arn}/{prefix}*"],
        },
        {
            "Sid": "RunPrefixList",
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": [bucket_arn],
            "Condition": {"StringLike": {"s3:prefix": list_prefixes}},
        },
    ]
    if read_prefixes:
        statements.append(
            {
                "Sid": "SourceCollectionsRead",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"{bucket_arn}/{p}*" for p in read_prefixes],
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def build_sts_client(settings: Settings):
    endpoint = pinned_endpoint_url(
        settings.staging_s3_endpoint,
        settings.staging_s3_region,
        settings.egress_allow_hosts,
    )
    return boto3.client(
        "sts",
        endpoint_url=endpoint,
        region_name=settings.staging_s3_region,
        config=Config(signature_version="s3v4"),
        **static_s3_credentials(settings),
    )


def mint_prefix_credentials(
    settings: Settings,
    *,
    session_name: str,
    prefix: str,
    timeout_seconds: int,
    sts_client=None,
    read_prefixes: Sequence[str] = (),
    queue_wait_seconds: int = 0,
    now: dt.datetime | None = None,
) -> RunCredentials:
    """Mint credentials good only for ``prefix`` plus read access to
    ``read_prefixes``. A process run's prefix is ``staging/runs/{run_id}/``;
    an image scan's is ``scans/{image_id}/{scan_id}/`` (C-2, spec §6.2).

    Raises :class:`CredentialBudgetExceeded` (before any STS call) when the
    duration would exceed the deployment cap,
    :class:`CredentialDurationRejected` (a subclass) when STS refuses the
    duration itself, and :class:`RunCredentialsError` on any other STS
    failure -- the caller must
    fail the run rather than start it with wider access.

    K-4 (spec §5.4, plan R7/R8): the duration is ``max(900, queue_wait +
    timeout + grace)``, so a run that waits ``max_queue_wait_seconds`` for
    capacity still starts with credentials that outlive it. Over the cap it
    is REFUSED, never clamped (the app's deploy gate counts the same sum).
    """
    if queue_wait_seconds < 0:
        raise ValueError(f"queue_wait_seconds must not be negative, got {queue_wait_seconds}")
    _validate_scoped_prefix(prefix, what="prefix")
    for read_prefix in read_prefixes:
        _validate_scoped_prefix(read_prefix, what="a read prefix")
    if len(read_prefixes) > MAX_READ_PREFIXES:
        raise RunCredentialsError(
            f"run would need {len(read_prefixes)} read prefixes; the inline session "
            f"policy supports at most {MAX_READ_PREFIXES} source collections"
        )
    bucket = settings.staging_bucket
    duration = max(
        MIN_DURATION_SECONDS,
        queue_wait_seconds + timeout_seconds + settings.process_credential_grace_seconds,
    )
    cap = settings.process_max_credential_seconds
    if cap is not None and duration > cap:
        raise CredentialBudgetExceeded(
            requested_seconds=duration,
            max_seconds=cap,
            timeout_seconds=timeout_seconds,
            grace_seconds=settings.process_credential_grace_seconds,
            queue_wait_seconds=queue_wait_seconds,
        )
    # Read BEFORE the call: the real expiry is at least this + duration.
    minted_at = now or dt.datetime.now(dt.UTC)
    client = sts_client if sts_client is not None else build_sts_client(settings)

    try:
        response = client.assume_role(
            RoleArn=settings.process_sts_role_arn,
            # Session names are surfaced in access logs; the run or scan id
            # makes an S3 audit trail directly attributable to its row.
            RoleSessionName=session_name[:64],
            Policy=json.dumps(
                session_policy(
                    bucket,
                    prefix,
                    read_prefixes,
                    partition=arn_partition(settings.process_sts_role_arn),
                )
            ),
            DurationSeconds=duration,
        )
    # Deliberately broad: the contract here is "any STS failure means the run
    # does not start". Enumerating boto's exception types would let an
    # unanticipated one (a transport error, a stubbed client, a
    # misconfiguration surfacing as AttributeError) escape as itself, and a
    # caller that expected RunCredentialsError would then handle it as
    # something else -- the one outcome this module exists to prevent.
    except Exception as err:
        sts_message = _sts_duration_rejection(err)
        if sts_message is not None:
            raise CredentialDurationRejected(
                requested_seconds=duration,
                max_seconds=cap,
                timeout_seconds=timeout_seconds,
                grace_seconds=settings.process_credential_grace_seconds,
                sts_message=sts_message,
                queue_wait_seconds=queue_wait_seconds,
            ) from err
        raise RunCredentialsError(
            "could not mint run-scoped storage credentials via STS "
            f"({type(err).__name__}: {err}). The platform's own keys are NOT a "
            "fallback -- a deployment whose object store lacks STS cannot run "
            "processes (spec §5)."
        ) from err

    creds = (response or {}).get("Credentials") or {}
    missing = [
        field
        for field in ("AccessKeyId", "SecretAccessKey", "SessionToken")
        if not creds.get(field)
    ]
    if missing:
        raise RunCredentialsError(
            f"STS response is missing {', '.join(missing)} -- refusing to start "
            "a run with incomplete credentials"
        )

    return RunCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
        bucket=bucket,
        prefix=prefix,
        endpoint_url=settings.process_run_s3_endpoint or settings.staging_s3_endpoint,
        region=settings.staging_s3_region,
        expires_at=minted_at + dt.timedelta(seconds=duration),
    )


def mint_run_credentials(
    settings: Settings,
    run_id: str,
    timeout_seconds: int,
    *,
    queue_wait_seconds: int = 0,
    sts_client=None,
    read_prefixes: Sequence[str] = (),
    now: dt.datetime | None = None,
) -> RunCredentials:
    """Mint credentials good only for ``staging/runs/{run_id}/`` plus read
    access to ``read_prefixes`` (the source collections' canonical prefixes),
    lasting the profile's queue wait + timeout + grace (K-4)."""
    return mint_prefix_credentials(
        settings,
        session_name=f"stac-run-{run_id}",
        prefix=run_staging_prefix(run_id),
        timeout_seconds=timeout_seconds,
        sts_client=sts_client,
        read_prefixes=read_prefixes,
        queue_wait_seconds=queue_wait_seconds,
        now=now,
    )
