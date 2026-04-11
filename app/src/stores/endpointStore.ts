import { computed } from "nanostores";
import { persistentAtom } from "@nanostores/persistent";

export interface StacEndpoint {
  id: string;
  name: string;
  url: string;
  isDefault: boolean;
}

export const $endpoints = persistentAtom<StacEndpoint[]>(
  "stac-endpoints",
  [],
  {
    encode: JSON.stringify,
    decode: JSON.parse,
  },
);

export const $activeEndpointId = persistentAtom<string>(
  "stac-active-endpoint",
  "",
);

export const $activeEndpoint = computed(
  [$endpoints, $activeEndpointId],
  (endpoints, id) => {
    return endpoints.find((e) => e.id === id) ?? endpoints[0] ?? null;
  },
);

export function addEndpoint(endpoint: Omit<StacEndpoint, "id">) {
  const id = crypto.randomUUID();
  const current = $endpoints.get();
  const isFirst = current.length === 0;
  $endpoints.set([
    ...current,
    { ...endpoint, id, isDefault: isFirst || endpoint.isDefault },
  ]);
  if (isFirst || endpoint.isDefault) {
    $activeEndpointId.set(id);
  }
  return id;
}

export function updateEndpoint(id: string, updates: Partial<StacEndpoint>) {
  $endpoints.set(
    $endpoints.get().map((e) => (e.id === id ? { ...e, ...updates } : e)),
  );
}

export function removeEndpoint(id: string) {
  const current = $endpoints.get();
  const filtered = current.filter((e) => e.id !== id);
  $endpoints.set(filtered);
  if ($activeEndpointId.get() === id && filtered.length > 0) {
    $activeEndpointId.set(filtered[0].id);
  }
}

export function setActiveEndpoint(id: string) {
  $activeEndpointId.set(id);
}
