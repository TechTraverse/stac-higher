// @vitest-environment node
// (server-side validation — no DOM involved)
import { describe, it, expect } from "vitest";
import {
  STAC_API_RESERVED_MESSAGE,
  parseConnectionCreate,
  parseConnectionUpdate,
} from "@/lib/connections/schemas";

const base = {
  name: "Test endpoint",
  group_id: "earth-observation",
};

function create(overrides: Record<string, unknown>) {
  return parseConnectionCreate({ ...base, ...overrides });
}

describe("parseConnectionCreate — protocol matrix", () => {
  it("accepts s3 with full config and credentials", () => {
    const result = create({
      protocol: "s3",
      config: {
        bucket: "stac-higher",
        region: "us-east-1",
        endpoint: "http://localhost:9000",
        force_path_style: true,
      },
      credentials: {
        access_key_id: "minioadmin",
        secret_access_key: "minioadmin",
        session_token: "tok",
      },
    });
    expect(result.success).toBe(true);
  });

  it("rejects s3 without a bucket or without a secret", () => {
    expect(
      create({
        protocol: "s3",
        config: {},
        credentials: { access_key_id: "a", secret_access_key: "b" },
      }).success,
    ).toBe(false);
    expect(
      create({
        protocol: "s3",
        config: { bucket: "b" },
        credentials: { access_key_id: "a" },
      }).success,
    ).toBe(false);
  });

  it.each(["ssh", "sftp"] as const)(
    "accepts %s with password OR private_key and applies defaults",
    (protocol) => {
      const withPassword = create({
        protocol,
        config: { host: "sftp.example.com" },
        credentials: { username: "u", password: "p" },
      });
      expect(withPassword.success).toBe(true);
      if (withPassword.success) {
        expect(withPassword.data.config).toMatchObject({
          port: 22,
          root_path: "/",
        });
        expect(withPassword.data.enabled).toBe(true);
        expect(withPassword.data.description).toBe("");
      }
      expect(
        create({
          protocol,
          config: { host: "h", port: 2222, root_path: "/incoming" },
          credentials: {
            username: "u",
            private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
            passphrase: "pp",
          },
        }).success,
      ).toBe(true);
    },
  );

  it.each(["ssh", "sftp"] as const)(
    "rejects %s credentials with neither password nor private_key",
    (protocol) => {
      const result = create({
        protocol,
        config: { host: "h" },
        credentials: { username: "u" },
      });
      expect(result.success).toBe(false);
      if (!result.success) {
        expect(JSON.stringify(result.error.issues)).toMatch(
          /password or private_key/,
        );
      }
    },
  );

  it("accepts ftp and defaults port 21", () => {
    const result = create({
      protocol: "ftp",
      config: { host: "ftp.example.com" },
      credentials: { username: "u", password: "p" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.config).toMatchObject({ port: 21, root_path: "/" });
    }
  });

  it("rejects ftp credentials without a password", () => {
    expect(
      create({
        protocol: "ftp",
        config: { host: "h" },
        credentials: { username: "u" },
      }).success,
    ).toBe(false);
  });

  it("accepts ftps and defaults implicit=false", () => {
    const result = create({
      protocol: "ftps",
      config: { host: "ftps.example.com" },
      credentials: { username: "u", password: "p" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.config).toMatchObject({ implicit: false, port: 21 });
    }
  });

  it("rejects config keys from the wrong protocol (strict shapes)", () => {
    expect(
      create({
        protocol: "sftp",
        config: { host: "h", bucket: "nope" },
        credentials: { username: "u", password: "p" },
      }).success,
    ).toBe(false);
  });

  it("rejects unknown protocols", () => {
    expect(
      create({ protocol: "gopher", config: {}, credentials: {} }).success,
    ).toBe(false);
  });

  it("rejects the reserved stac-api protocol with a clear message", () => {
    const result = create({
      protocol: "stac-api",
      config: { url: "https://earth-search.aws.element84.com/v1" },
      credentials: {},
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toBe(STAC_API_RESERVED_MESSAGE);
      expect(result.error.issues[0].path).toEqual(["protocol"]);
    }
  });

  it("accepts an anonymous s3 connection with no credentials", () => {
    const result = create({
      protocol: "s3",
      config: { bucket: "noaa-goes19", region: "us-east-1", anonymous: true },
    });
    expect(result.success).toBe(true);
    if (result.success && result.data.protocol === "s3") {
      expect(result.data.config.anonymous).toBe(true);
      expect(result.data.credentials).toBeUndefined();
    }
  });

  it("still requires credentials when anonymous is false or absent", () => {
    const absent = create({ protocol: "s3", config: { bucket: "b" } });
    expect(absent.success).toBe(false);
    if (!absent.success) {
      expect(
        absent.error.issues.some((i) => i.path.join(".") === "credentials"),
      ).toBe(true);
    }
    const explicit = create({
      protocol: "s3",
      config: { bucket: "b", anonymous: false },
    });
    expect(explicit.success).toBe(false);
  });

  it("defaults anonymous to false on parse", () => {
    const result = create({
      protocol: "s3",
      config: { bucket: "b" },
      credentials: { access_key_id: "a", secret_access_key: "b" },
    });
    expect(result.success).toBe(true);
    if (result.success && result.data.protocol === "s3") {
      expect(result.data.config.anonymous).toBe(false);
    }
  });
});

describe("parseConnectionUpdate", () => {
  it("accepts a metadata-only patch without credentials", () => {
    const result = parseConnectionUpdate(
      { name: "Renamed", enabled: false },
      "sftp",
    );
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.credentials).toBeUndefined();
      expect(result.data.config).toBeUndefined();
    }
  });

  it("validates config against the EXISTING protocol", () => {
    expect(
      parseConnectionUpdate({ config: { bucket: "b" } }, "sftp").success,
    ).toBe(false);
    expect(
      parseConnectionUpdate({ config: { host: "new-host" } }, "sftp").success,
    ).toBe(true);
  });

  it("validates replacement credentials against the EXISTING protocol", () => {
    expect(
      parseConnectionUpdate(
        { credentials: { access_key_id: "a", secret_access_key: "b" } },
        "ftp",
      ).success,
    ).toBe(false);
    expect(
      parseConnectionUpdate(
        { credentials: { username: "u", password: "p" } },
        "ftp",
      ).success,
    ).toBe(true);
  });

  it("rejects protocol changes", () => {
    const result = parseConnectionUpdate({ protocol: "s3" }, "sftp");
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toMatch(/immutable/);
    }
  });

  it("allows restating the existing protocol", () => {
    expect(parseConnectionUpdate({ protocol: "sftp" }, "sftp").success).toBe(
      true,
    );
  });

  it("rejects updates against a reserved stac-api row", () => {
    const result = parseConnectionUpdate({ name: "x" }, "stac-api");
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toBe(STAC_API_RESERVED_MESSAGE);
    }
  });

  it("rejects unknown top-level keys", () => {
    expect(
      parseConnectionUpdate({ nme: "typo" }, "sftp").success,
    ).toBe(false);
  });
});
