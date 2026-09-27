/**
 * The image policy (C-1, container-images spec §7). It is a per-deployment
 * document both runtimes read (the hardware-profiles pattern), in-repo
 * default `infra/image-policy/default.json`, overridden by
 * `PROCESS_IMAGE_POLICY_FILE`. The app validates it STRICTLY and FAILS
 * CLOSED: when the document cannot be read or is invalid, no user image can
 * be deployed or added. That is a 503 at the gate, never a silent pass.
 * Inline revisions never read it.
 *
 * Pinned by `tests/contract-fixtures/image-policy.json` against
 * `pipeline/images/policy.py`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { z } from "zod";

const REGISTRY_PATTERN_RE = /^[a-z0-9*](?:[a-z0-9*.-]*[a-z0-9*])?(?::[0-9]{1,5})?$/;
const PLATFORM_RE = /^[a-z0-9]+\/[a-z0-9]+(?:\/[a-z0-9]+)?$/;
const LABEL_RE = /^[a-z0-9-]+$/;

export const imagePolicyBlockSchema = z
  .object({
    kev: z.boolean(),
    critical_fixed: z.boolean(),
    critical_unfixed_older_than_days: z.number().int().min(0).nullable(),
    high_fixed_epss_at_least: z.number().min(0).max(1).nullable(),
    high_unfixed: z.boolean(),
  })
  .strict();

export const imagePolicySchema = z
  .object({
    version: z.literal(1),
    allowed_registries: z
      .array(
        z
          .string()
          .regex(REGISTRY_PATTERN_RE, "allowed_registries entries are lowercase hostnames; `*` stands for one label"),
      )
      .min(1, "allowed_registries must name at least one registry"),
    platform: z.string().regex(PLATFORM_RE, "platform must be os/architecture, e.g. linux/amd64"),
    max_image_size_mb: z.number().int().min(1),
    block: imagePolicyBlockSchema,
    scan_window_days: z.number().int().min(1),
    rescan_interval_hours: z.number().int().min(1),
    exception_max_days: z.number().int().min(1),
    scan_limits: z
      .object({
        memory_mb: z.number().int().min(128),
        timeout_seconds: z.number().int().min(1).max(86_400),
      })
      .strict(),
  })
  .strict()
  .superRefine((policy, ctx) => {
    if (policy.rescan_interval_hours > policy.scan_window_days * 24) {
      ctx.addIssue({
        code: "custom",
        path: ["rescan_interval_hours"],
        message:
          "rescan_interval_hours must fit inside scan_window_days; otherwise every image goes stale between rescans",
      });
    }
  });

export type ImagePolicy = z.infer<typeof imagePolicySchema>;

/** The policy is missing or invalid. Callers answer 503 (fail closed). */
export class ImagePolicyUnavailable extends Error {}

export function parseImagePolicy(document: unknown): ImagePolicy {
  const parsed = imagePolicySchema.safeParse(document);
  if (!parsed.success) {
    throw new ImagePolicyUnavailable(
      `image policy: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`,
    );
  }
  return parsed.data;
}

const CHECKOUT_DEFAULT_POLICY = fileURLToPath(
  new URL("../../../../infra/image-policy/default.json", import.meta.url),
);

export function imagePolicyPath(env: Record<string, string | undefined> = process.env): string {
  const override = env.PROCESS_IMAGE_POLICY_FILE?.trim();
  return override ? override : CHECKOUT_DEFAULT_POLICY;
}

let cache: { path: string; policy: ImagePolicy } | null = null;

/** Read and validate the deployment's policy, cached per path for the
 * process lifetime (deployment config, not operator data). */
export function loadImagePolicy(env: Record<string, string | undefined> = process.env): ImagePolicy {
  const path = imagePolicyPath(env);
  if (cache && cache.path === path) return cache.policy;
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    throw new ImagePolicyUnavailable(
      `could not read the image policy at ${path}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  const policy = parseImagePolicy(document);
  cache = { path, policy };
  return policy;
}

export function resetImagePolicyCache(): void {
  cache = null;
}

/** Is a registry HOST allowed by the policy's patterns? `*` is exactly one
 * DNS label; the host is case-folded; a port must match literally. The same
 * rule as `registry_allowed` in the pipeline (fixture `registry_cases`). */
export function registryAllowed(host: string, patterns: readonly string[]): boolean {
  const labels = host.toLowerCase().split(".");
  return patterns.some((pattern) => {
    const want = pattern.split(".");
    return (
      want.length === labels.length &&
      want.every((label, i) => (label === "*" ? LABEL_RE.test(labels[i]) : label === labels[i]))
    );
  });
}
