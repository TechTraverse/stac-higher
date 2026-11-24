// Release manifest generator (R-3).
//
// Output is the contract that docs/releases.md documents and that the Helm
// chart's `global.imageDigests` reads (spec sections 6.2 and 11): field names,
// key order and validation rules are stable. Adding an image means adding it
// to IMAGE_KEYS, the release workflow and the docs table in the same PR.
//
// Dependency-free Node >= 22 ESM. Usage:
//   node release-manifest.mjs --version v1.2.3 --git-sha <40 hex> \
//     --repository <owner>/<repo> --digests-dir <dir> --out <file>
// <dir> holds one file per image, named by its key, containing its digest.

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

/** @type {readonly string[]} */
export const IMAGE_KEYS = Object.freeze([
  "app",
  "pipeline",
  "titiler",
  "cube-server",
  "image-scanner",
  "process-runtime",
  "process-runtime-stactools",
  "auth-proxy",
]);

// No `+build` metadata: Docker tags cannot carry `+`.
const VERSION_RE = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$/;
const SHA_RE = /^[0-9a-f]{40}$/;
const REPOSITORY_RE = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;
const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

/**
 * @typedef {{ ref: string, digest: string }} ImageEntry
 * @typedef {{
 *   schemaVersion: 1,
 *   version: string,
 *   gitSha: string,
 *   images: Record<string, ImageEntry>,
 *   chart: { ref: string, version: string },
 *   terraformModules: { ref: string },
 * }} ReleaseManifest
 */

/**
 * @param {{ version: string, gitSha: string, repository: string, digests: Record<string, string> }} input
 * @returns {ReleaseManifest}
 */
export function buildReleaseManifest({ version, gitSha, repository, digests }) {
  /** @type {string[]} */
  const problems = [];
  if (typeof version !== "string" || !VERSION_RE.test(version)) {
    problems.push(`version must look like v1.2.3 or v1.2.3-rc.1, got ${JSON.stringify(version)}`);
  }
  if (typeof gitSha !== "string" || !SHA_RE.test(gitSha)) {
    problems.push(`gitSha must be 40 lowercase hex characters, got ${JSON.stringify(gitSha)}`);
  }
  if (typeof repository !== "string" || !REPOSITORY_RE.test(repository)) {
    problems.push(`repository must look like owner/name, got ${JSON.stringify(repository)}`);
  }
  const given = digests ?? {};
  for (const key of IMAGE_KEYS) {
    if (!Object.hasOwn(given, key)) {
      problems.push(`missing digest for image ${key}`);
    } else if (typeof given[key] !== "string" || !DIGEST_RE.test(given[key])) {
      problems.push(`digest for image ${key} must be sha256:<64 lowercase hex>, got ${JSON.stringify(given[key])}`);
    }
  }
  for (const key of Object.keys(given)) {
    if (!IMAGE_KEYS.includes(key)) problems.push(`unexpected image ${key}`);
  }
  if (problems.length > 0) throw new Error(problems.join("\n"));

  const semver = version.slice(1);
  const [owner, repo] = repository.split("/");
  const ownerLc = owner.toLowerCase();
  const repoLc = repo.toLowerCase();

  /** @type {Record<string, ImageEntry>} */
  const images = {};
  for (const key of IMAGE_KEYS) {
    images[key] = { ref: `ghcr.io/${ownerLc}/${repoLc}-${key}:${semver}`, digest: given[key] };
  }
  return {
    schemaVersion: 1,
    version,
    gitSha,
    images,
    chart: { ref: `oci://ghcr.io/${ownerLc}/charts/stac-higher`, version: semver },
    terraformModules: {
      ref: `git::https://github.com/${owner}/${repo}.git//infra/terraform/modules/stac-higher?ref=${version}`,
    },
  };
}

/**
 * Read one file per image (named by its key) from `dir`; content is the digest.
 * Every entry is read, so stray files surface as unexpected images.
 * @param {string} dir
 * @returns {Record<string, string>}
 */
export function readDigestsDir(dir) {
  /** @type {Record<string, string>} */
  const digests = {};
  for (const name of readdirSync(dir).sort()) {
    digests[name] = readFileSync(join(dir, name), "utf8").trim();
  }
  return digests;
}

/**
 * @param {string[]} argv
 * @returns {number} process exit code
 */
export function main(argv) {
  try {
    const { values } = parseArgs({
      args: argv,
      strict: true,
      options: {
        version: { type: "string" },
        "git-sha": { type: "string" },
        repository: { type: "string" },
        "digests-dir": { type: "string" },
        out: { type: "string" },
      },
    });
    for (const flag of ["version", "git-sha", "repository", "digests-dir", "out"]) {
      if (!values[flag]) throw new Error(`missing required flag --${flag}`);
    }
    const manifest = buildReleaseManifest({
      version: /** @type {string} */ (values.version),
      gitSha: /** @type {string} */ (values["git-sha"]),
      repository: /** @type {string} */ (values.repository),
      digests: readDigestsDir(/** @type {string} */ (values["digests-dir"])),
    });
    writeFileSync(/** @type {string} */ (values.out), JSON.stringify(manifest, null, 2) + "\n");
    return 0;
  } catch (err) {
    process.stderr.write(`release-manifest: ${err instanceof Error ? err.message : String(err)}\n`);
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main(process.argv.slice(2));
}
