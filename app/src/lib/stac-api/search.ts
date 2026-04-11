import { stacFetch } from "./client";
import type {
  StacItemCollection,
  StacLandingPage,
  StacSearchBody,
} from "./types";

export function searchItems(params: StacSearchBody, endpointUrl?: string) {
  return stacFetch<StacItemCollection>("/search", {
    method: "POST",
    body: params,
    endpointUrl,
  });
}

export function getLandingPage(endpointUrl?: string) {
  return stacFetch<StacLandingPage>("/", { endpointUrl });
}
