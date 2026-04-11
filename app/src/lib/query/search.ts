import { useQuery } from "@tanstack/react-query";
import { stacKeys } from "./keys";
import { searchItems, getLandingPage } from "@/lib/stac-api/search";
import type { StacSearchBody } from "@/lib/stac-api/types";

export function useStacSearch(endpointUrl: string, params: StacSearchBody | null) {
  return useQuery({
    queryKey: params ? stacKeys.search(endpointUrl, params) : ["disabled"],
    queryFn: () => searchItems(params!, endpointUrl),
    enabled: !!endpointUrl && params !== null,
  });
}

export function useLandingPage(endpointUrl: string) {
  return useQuery({
    queryKey: stacKeys.landing(endpointUrl),
    queryFn: () => getLandingPage(endpointUrl),
    enabled: !!endpointUrl,
  });
}
