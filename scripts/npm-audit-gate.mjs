#!/usr/bin/env node
// CI gate: `npm audit --omit=dev`, failing on any high/critical advisory not
// accepted in .npm-audit-allow.json. npm audit has no ignore flag, so an
// advisory with no patched release would otherwise hold the gate red forever.
// Each accepted entry names its GHSA, the reason, and the ISSUES.md entry that
// records the residual risk. Stale entries (advisory no longer reported) raise
// a CI warning rather than failing, so a fix upstream never reds an unrelated PR.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const BLOCKING = new Set(["high", "critical"]);
const allowPath = new URL("../.npm-audit-allow.json", import.meta.url);
const allowed = new Map(
  JSON.parse(readFileSync(allowPath, "utf8")).advisories.map((a) => [a.id, a]),
);

let raw;
try {
  raw = execFileSync("npm", ["audit", "--omit=dev", "--json"], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
} catch (err) {
  // npm audit exits non-zero when it finds anything; the JSON is still on stdout.
  raw = err.stdout;
  if (!raw) throw err;
}
const report = JSON.parse(raw);
if (report.error) {
  console.error("npm audit failed:", report.error);
  process.exit(2);
}

// Advisory objects sit in `via`; string entries are transitive pointers to
// another package's advisories, so collecting the objects covers every path.
const found = new Map();
for (const vuln of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vuln.via) {
    if (typeof via !== "object" || !BLOCKING.has(via.severity)) continue;
    const id = via.url.split("/").pop();
    found.set(id, { ...via, id });
  }
}

const blocking = [...found.values()].filter((a) => !allowed.has(a.id));
const stale = [...allowed.keys()].filter((id) => !found.has(id));

for (const a of found.values()) {
  const tag = allowed.has(a.id) ? `accepted (${allowed.get(a.id).issue})` : "BLOCKING";
  console.log(`${a.severity.padEnd(8)} ${a.id} ${a.name} — ${a.title} [${tag}]`);
}
for (const id of stale) {
  console.log(`::warning::${id} is allowlisted but no longer reported — remove it from .npm-audit-allow.json`);
}

if (blocking.length) process.exit(1);
console.log(`npm audit gate: no unaccepted high/critical advisories (${found.size} accepted).`);
