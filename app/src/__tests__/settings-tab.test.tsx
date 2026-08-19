/**
 * SettingsTab island (M2-E): seeding from the loaded settings, validation
 * messaging, save payload shape, and read-only rendering for members.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { CollectionSettings } from "@/lib/collections/settings";

const { useSettingsMock, updateMutate, authState } = vi.hoisted(() => ({
  useSettingsMock: vi.fn(),
  updateMutate: vi.fn(),
  authState: { roles: ["operator"] as string[] },
}));

vi.mock("@/lib/query/auth", () => ({
  useAuthMe: () => ({
    data: {
      authenticated: true,
      mode: "bypass",
      identity: { sub: "u1", groups: ["earth-observation"], roles: authState.roles },
    },
  }),
}));
vi.mock("@/lib/collections/settings-client", () => ({
  useCollectionSettings: () => useSettingsMock(),
  useUpdateCollectionSettings: () => ({ mutate: updateMutate, isPending: false }),
}));

import { SettingsTab } from "@/components/collections/SettingsTab";

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

function loaded(overrides: Partial<CollectionSettings> = {}) {
  const data: CollectionSettings = {
    collectionId: "sentinel-2",
    groupId: null,
    externallyWritable: false,
    retentionDays: null,
    gcGraceDays: 30,
    archived: false,
    ...overrides,
  };
  return { data, isLoading: false, isError: false, error: null, refetch: vi.fn() };
}

beforeEach(() => {
  updateMutate.mockReset();
  authState.roles = ["operator"];
  useSettingsMock.mockReset().mockReturnValue(loaded());
});

describe("SettingsTab", () => {
  it("seeds from the loaded settings and saves the full document", () => {
    useSettingsMock.mockReturnValue(loaded({ retentionDays: 14, gcGraceDays: 7 }));
    render(<SettingsTab collectionId="sentinel-2" />);

    expect(
      (screen.getByTestId("settings-retention") as HTMLInputElement).value,
    ).toBe("14");

    fireEvent.change(screen.getByTestId("settings-retention"), {
      target: { value: "30" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));

    expect(updateMutate).toHaveBeenCalledWith(
      {
        group_id: null,
        externally_writable: false,
        retention_days: 30,
        gc_grace_days: 7,
        archived: false,
      },
      expect.anything(),
    );
  });

  it("empty retention means keep-forever (null)", () => {
    useSettingsMock.mockReturnValue(loaded({ retentionDays: 14 }));
    render(<SettingsTab collectionId="sentinel-2" />);

    fireEvent.change(screen.getByTestId("settings-retention"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));

    expect(updateMutate.mock.calls[0][0].retention_days).toBeNull();
  });

  it("blocks save and explains when retention is invalid", () => {
    render(<SettingsTab collectionId="sentinel-2" />);
    fireEvent.change(screen.getByTestId("settings-retention"), {
      target: { value: "0" },
    });
    expect(screen.getByText(/whole number of days/)).toBeTruthy();
    expect(
      (screen.getByTestId("settings-save") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("renders read-only for members (no save button, controls disabled)", () => {
    authState.roles = ["member"];
    render(<SettingsTab collectionId="sentinel-2" />);
    expect(screen.queryByTestId("settings-save")).toBeNull();
    expect(
      (screen.getByTestId("settings-retention") as HTMLInputElement).disabled,
    ).toBe(true);
  });

  it("shows the archived badge when archived", () => {
    useSettingsMock.mockReturnValue(loaded({ archived: true }));
    render(<SettingsTab collectionId="sentinel-2" />);
    expect(screen.getByText("archived")).toBeTruthy();
  });
});
