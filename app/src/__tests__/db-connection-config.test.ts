// @vitest-environment node
import { describe, it, expect, vi } from "vitest";
import { DEFAULT_DATABASE_URL, poolConfigFromEnv } from "@/lib/db/connection";

describe("poolConfigFromEnv", () => {
  it("is today's config when PGSSLROOTCERT is unset or blank", () => {
    expect(poolConfigFromEnv({})).toEqual({ connectionString: DEFAULT_DATABASE_URL });
    expect(
      poolConfigFromEnv({
        DATABASE_URL: "postgresql://u:p@db.example.org:5432/app",
        PGSSLROOTCERT: " ",
      }),
    ).toEqual({ connectionString: "postgresql://u:p@db.example.org:5432/app" });
  });

  it("verifies the server certificate against the CA bundle when PGSSLROOTCERT is set", () => {
    const readFile = vi.fn(() => "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n");
    const cfg = poolConfigFromEnv({ PGSSLROOTCERT: "/etc/ssl/rds/global-bundle.pem" }, readFile);
    expect(readFile).toHaveBeenCalledWith("/etc/ssl/rds/global-bundle.pem");
    expect(cfg.ssl).toEqual({
      ca: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
      rejectUnauthorized: true,
    });
  });

  it("fails loudly, naming the path, when the bundle cannot be read", () => {
    const readFile = () => {
      throw new Error("ENOENT");
    };
    expect(() => poolConfigFromEnv({ PGSSLROOTCERT: "/missing.pem" }, readFile)).toThrow(
      /PGSSLROOTCERT.*\/missing\.pem.*ENOENT/,
    );
  });

  it("refuses an empty bundle rather than connecting unverified", () => {
    expect(() => poolConfigFromEnv({ PGSSLROOTCERT: "/empty.pem" }, () => "  \n")).toThrow(
      /PGSSLROOTCERT.*\/empty\.pem.*empty/,
    );
  });
});
