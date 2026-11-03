# aws-eks example

The template a deployment repo copies. It makes one call to the `stac-higher`
composition module (`../../terraform/modules/stac-higher`) with placeholder
values (`us-east-1`, `stac-higher-example`, `https://app.example.com`,
`ecr-pullthroughcache/ghcr`, `example-cluster`, and `vpc-0000...` network ids),
and exposes its `helm_values`, `helm_values_yaml` and
`external_secrets_policy_json` outputs. Cognito and the demo destination are on.
It declares only `provider "aws" { region = var.region }`: no backend, no
credentials.

This root is **never applied**. A deployment copies it, replaces the local
module source with a `git::` source pinned by tag, adds its own backend and sets
real values; see [`../../terraform/README.md`](../../terraform/README.md).

The GHCR credentials secret is looked up by name; the deployment creates it
(see the registry module README).

## Rendering the chart values

```bash
tofu output -raw helm_values_yaml > values-aws.yaml
helm upgrade --install stac-higher <chart> -f values-aws.yaml -f values.yaml
```

The deployment's own `values.yaml` goes last, so it wins. It carries what
Terraform does not emit: public hostnames and the RDS CA bundle (see the
`helm_values contract` section of the Terraform README).

Attach `external_secrets_policy_json` to the cluster's External Secrets
Operator role (`tofu output -raw external_secrets_policy_json`).

## Checking it

Check it with the mock-provider test, no account needed:

```bash
tofu init -backend=false && tofu validate && tofu test
```
