"""Run-scoped storage credentials (spec §5, ADR 0013/0014).

The settled mechanic: mint **STS session credentials with an inline session
policy** that restricts S3 to the run's own staging prefix
(``staging/runs/{run_id}/``) and nothing else. The run can write its outputs
and read them back; it cannot see another run's bytes, the canonical
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
credential ages out on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import boto3
from botocore.client import Config

from pipeline.config import Settings
from pipeline.storage.keys import run_staging_prefix
from pipeline.storage.platform import pinned_endpoint_url

#: STS minimum is 900s; a shorter ask is rejected by AWS outright.
MIN_DURATION_SECONDS = 900


class RunCredentialsError(Exception):
    """Run-scoped credentials could not be minted — the run must not start."""


@dataclass(frozen=True)
class RunCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    bucket: str
    prefix: str
    endpoint_url: str | None
    region: str

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


def session_policy(bucket: str, prefix: str) -> dict:
    """The inline policy bounding one run.

    Object actions are scoped to the run's prefix. ListBucket is granted on
    the BUCKET resource — that is where S3 evaluates it — but conditioned on
    the same prefix, so a run can enumerate its own outputs without
    discovering that anything else exists.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "RunPrefixObjects",
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/{prefix}*"],
            },
            {
                "Sid": "RunPrefixList",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
                "Condition": {"StringLike": {"s3:prefix": [f"{prefix}*"]}},
            },
        ],
    }


def build_sts_client(settings: Settings):  # pragma: no cover - thin boto3 wrapper
    endpoint = pinned_endpoint_url(
        settings.staging_s3_endpoint,
        settings.staging_s3_region,
        settings.egress_allow_hosts,
    )
    return boto3.client(
        "sts",
        endpoint_url=endpoint,
        region_name=settings.staging_s3_region,
        aws_access_key_id=settings.staging_s3_access_key,
        aws_secret_access_key=settings.staging_s3_secret_key,
        config=Config(signature_version="s3v4"),
    )


def mint_run_credentials(
    settings: Settings,
    run_id: str,
    timeout_seconds: int,
    *,
    sts_client=None,
) -> RunCredentials:
    """Mint credentials good only for ``staging/runs/{run_id}/``.

    Raises :class:`RunCredentialsError` on any STS failure — the caller must
    fail the run rather than start it with wider access.
    """
    prefix = run_staging_prefix(run_id)
    bucket = settings.staging_bucket
    duration = max(
        MIN_DURATION_SECONDS, timeout_seconds + settings.process_credential_grace_seconds
    )
    client = sts_client if sts_client is not None else build_sts_client(settings)

    try:
        response = client.assume_role(
            RoleArn=settings.process_sts_role_arn,
            # Session names are surfaced in access logs; the run id makes an
            # S3 audit trail directly attributable to a run row.
            RoleSessionName=f"stac-run-{run_id}"[:64],
            Policy=json.dumps(session_policy(bucket, prefix)),
            DurationSeconds=duration,
        )
    # Deliberately broad: the contract here is "any STS failure means the run
    # does not start". Enumerating boto's exception types would let an
    # unanticipated one (a transport error, a stubbed client, a
    # misconfiguration surfacing as AttributeError) escape as itself, and a
    # caller that expected RunCredentialsError would then handle it as
    # something else — the one outcome this module exists to prevent.
    except Exception as err:
        raise RunCredentialsError(
            "could not mint run-scoped storage credentials via STS "
            f"({type(err).__name__}: {err}). The platform's own keys are NOT a "
            "fallback — a deployment whose object store lacks STS cannot run "
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
            f"STS response is missing {', '.join(missing)} — refusing to start "
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
    )
