"""The platform image scanner (C-2, container-images spec §6, ADR 0021).

Runs inside ``services/image-scanner``'s image as a platform run: it reads
its job from the environment, resolves and SBOMs an image (Syft), matches
it (Grype, with KEV and EPSS), writes its objects under its own scan
prefix, and writes ``result.json`` last. Standard library plus boto3."""
