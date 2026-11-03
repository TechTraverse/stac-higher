# aws-eks example

The template a deployment repo copies: it wires the four R-4 modules
(`kms`, `storage`, `secrets`, `registry` under `../../terraform/modules/`)
with placeholder values (`us-east-1`, `stac-higher-example`,
`https://app.example.com`, `ecr-pullthroughcache/ghcr`). It declares only
`provider "aws" { region = var.region }`: no backend, no credentials.

This root is **never applied**. A deployment uses `git::` sources pinned by
tag instead of the local paths, and adds its own backend; see
[`../../terraform/README.md`](../../terraform/README.md). R-5 replaces the four
calls with one call to the `stac-higher` composition module and adds a
`helm_values` output.

The GHCR credentials secret is looked up by name; the deployment creates it
(see the registry module README).

Check it with the mock-provider test, no account needed:

```bash
tofu init -backend=false && tofu validate && tofu test
```
