"""M3 load harness (M3-S-A) — the synthetic feed + per-stage measurement the
NOAA-scale scoping work is sized from, and the M3 gate's rehearsal driver.

Not part of the running service: nothing here is registered as a job or
imported by the pipeline's own code paths. It ships inside the package so it
can reuse the real config, credential envelope and contract readers rather
than re-implementing them — a harness that builds its own idea of an ingest
config measures a pipeline nobody runs.

Entry point: ``python -m pipeline.loadgen --help`` (from services/pipeline,
against the compose stack's HOST ports). See ``README.md`` in this directory.
"""
