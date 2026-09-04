// The platform process-runtime images (ADR 0013): the base image every
// inline_python process runs on, and the stactools variant the built-in
// extractor library needs (X queue spec §6 / §8). Run from the REPO ROOT —
// bake resolves paths against the working directory, not this file:
//
//   docker buildx bake -f services/process-runtime/docker-bake.hcl            # both
//   docker buildx bake -f services/process-runtime/docker-bake.hcl stactools  # variant (builds the base too)
//
// The variant is chained off the base TARGET, so one command builds both in
// the right order and CI needs no published base to build the variant. The
// `fixtures` context is how tests/contract-fixtures/builtin-extractors.json
// reaches the image: the runtime's own build context contains no tests/, and
// a copy of the registry would be a second source of truth for the pin check.

group "default" {
  targets = ["runtime", "stactools"]
}

target "runtime" {
  context    = "services/process-runtime"
  dockerfile = "Dockerfile"
  tags       = ["stac-higher-process-runtime:local"]
}

target "stactools" {
  context    = "services/process-runtime"
  dockerfile = "Dockerfile.stactools"
  contexts = {
    base     = "target:runtime"
    fixtures = "tests/contract-fixtures"
  }
  args = {
    BASE_IMAGE = "base"
  }
  tags = ["stac-higher-process-runtime-stactools:local"]
}
