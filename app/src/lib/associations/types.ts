/**
 * Client-facing association type. Re-exported type-only from the server storage
 * module, so nothing from `storage.ts` (pg client) is bundled into the browser
 * — the re-export is fully erased at build time. The client shape omits the
 * access-only `connectionGroupId` (that lives on `AssociationWithGroup`).
 */
export type { ApiAssociation as Association } from "./storage";
