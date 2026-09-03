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
    retentionMaxItems: null,
    gcGraceDays: 30,
    archived: false,
    servingEnabled: false,
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
        retention_max_items: null,
        gc_grace_days: 7,
        archived: false,
        serving_enabled: false,
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

  it("tightening retention shows the counted dry-run before saving (M2-F)", async () => {
    useSettingsMock.mockReturnValue(loaded({ retentionDays: null }));
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ total_items: 40, expired_items: 12 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;

    render(<SettingsTab collectionId="sentinel-2" />);
    fireEvent.change(screen.getByTestId("settings-retention"), {
      target: { value: "14" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));

    // Nothing saved yet — the warn-and-proceed dialog is up with the counts.
    const impact = await screen.findByTestId("settings-impact");
    expect(impact.textContent).toContain("12 of 40 items");
    expect(updateMutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("settings-confirm"));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].retention_days).toBe(14);
  });

  it("archiving shows the total-item warning before saving", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ total_items: 7, expired_items: 7 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;

    render(<SettingsTab collectionId="sentinel-2" />);
    fireEvent.click(screen.getByTestId("settings-archived"));
    fireEvent.click(screen.getByTestId("settings-save"));

    const impact = await screen.findByTestId("settings-impact");
    expect(impact.textContent).toContain("All 7 items");
    expect(updateMutate).not.toHaveBeenCalled();
  });

  // --- maximum items (W-2) ------------------------------------------------ //

  function mockImpact(total: number, expired: number) {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ total_items: total, expired_items: expired }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;
    return global.fetch as unknown as ReturnType<typeof vi.fn>;
  }

  it("setting a maximum-items cap shows the dry-run, then saves it", async () => {
    const fetchMock = mockImpact(5, 3);
    render(<SettingsTab collectionId="sentinel-2" />);
    fireEvent.change(screen.getByTestId("settings-max-items"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));

    const impact = await screen.findByTestId("settings-impact");
    expect(impact.textContent).toContain("3 of 5 items");
    expect(impact.textContent).toContain("beyond the newest 2");
    expect(String(fetchMock.mock.calls[0][0])).toContain("retention_max_items=2");
    expect(updateMutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("settings-confirm"));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].retention_max_items).toBe(2);
  });

  it("lowering an existing cap warns; raising it saves directly", async () => {
    useSettingsMock.mockReturnValue(loaded({ retentionMaxItems: 100 }));
    mockImpact(200, 150);
    render(<SettingsTab collectionId="sentinel-2" />);
    expect(
      (screen.getByTestId("settings-max-items") as HTMLInputElement).value,
    ).toBe("100");

    fireEvent.change(screen.getByTestId("settings-max-items"), {
      target: { value: "50" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));
    const impact = await screen.findByTestId("settings-impact");
    expect(impact.textContent).toContain("150 of 200 items");
    expect(updateMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Cancel"));

    fireEvent.change(screen.getByTestId("settings-max-items"), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].retention_max_items).toBe(500);
  });

  it("clearing the cap sends null without a warning", () => {
    useSettingsMock.mockReturnValue(loaded({ retentionMaxItems: 100 }));
    render(<SettingsTab collectionId="sentinel-2" />);
    fireEvent.change(screen.getByTestId("settings-max-items"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByTestId("settings-save"));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].retention_max_items).toBeNull();
  });

  it("blocks save and explains when the cap is invalid", () => {
    render(<SettingsTab collectionId="sentinel-2" />);
    fireEvent.change(screen.getByTestId("settings-max-items"), {
      target: { value: "0" },
    });
    expect(screen.getByText(/Maximum items must be a whole number/)).toBeTruthy();
    expect(
      (screen.getByTestId("settings-save") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("shows the archived badge when archived", () => {
    useSettingsMock.mockReturnValue(loaded({ archived: true }));
    render(<SettingsTab collectionId="sentinel-2" />);
    expect(screen.getByText("archived")).toBeTruthy();
  });
});
