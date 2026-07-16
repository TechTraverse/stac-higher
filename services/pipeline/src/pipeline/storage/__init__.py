"""Platform-owned object storage (ROADMAP Phase 3, §5.3).

Distinct from the connection adapters (which talk to *user* endpoints): this is
the platform's OWN bucket — MinIO locally, S3 in cloud — used by platform jobs
such as the staging TTL sweep.
"""

from pipeline.storage.platform import build_platform_client, cleanup_expired

__all__ = ["build_platform_client", "cleanup_expired"]
