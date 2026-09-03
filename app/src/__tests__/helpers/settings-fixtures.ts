/**
 * Typed CollectionSettings fixture factory (the M2 hygiene follow-up).
 *
 * Every suite that mocks `getCollectionSettings` builds its fixture here so
 * the compiler flags ALL call sites the next time the settings shape grows —
 * the drift class that broke CI in the M2 promotion (hand-copied literals
 * missing a new field) and re-surfaced when `serving_enabled` landed.
 *
 * Type-only import: `vi.mock("@/lib/collections/settings")` in the consuming
 * suites cannot clobber it.
 */
import type { CollectionSettings } from "@/lib/collections/settings";

export function makeCollectionSettings(
  overrides: Partial<CollectionSettings> = {},
): CollectionSettings {
  return {
    collectionId: "test-collection",
    groupId: null,
    externallyWritable: false,
    retentionDays: null,
    retentionMaxItems: null,
    gcGraceDays: 30,
    archived: false,
    servingEnabled: false,
    ...overrides,
  };
}
