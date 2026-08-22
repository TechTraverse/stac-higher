/**
 * Collection "Data flow" tab (ROADMAP §8): the ingest half (Phase 4,
 * `IngestSection`) plus the delivery half (Phase 5 Slice D,
 * `DeliverySection`).
 *
 * Associates connections to this (built-in-catalog) collection as ingest
 * sources / delivery destinations and edits the §5.1 configs. Flow state
 * lives in `stac_higher` (not the catalog), so this reads/writes the
 * same-origin `/api/collections/[id]/connections` surface, never `stacFetch`.
 */
import { useMemo } from "react";
import { ErrorState, LoadingState } from "@stac-higher/shared";
import { useConnections } from "@/lib/connections/queries";
import { useAssociations } from "@/lib/associations/queries";
import { DeliverySection } from "./DeliverySection";
import { IngestSection } from "./IngestSection";

interface DataFlowTabProps {
  collectionId: string;
}

export function DataFlowTab({ collectionId }: DataFlowTabProps) {
  const associations = useAssociations(collectionId);
  const connections = useConnections();

  const ingest = useMemo(
    () => (associations.data ?? []).filter((a) => a.direction === "ingest"),
    [associations.data],
  );
  const deliver = useMemo(
    () => (associations.data ?? []).filter((a) => a.direction === "deliver"),
    [associations.data],
  );

  if (associations.isLoading) return <LoadingState message="Loading data flow…" />;
  if (associations.error) {
    return (
      <ErrorState
        message={
          associations.error instanceof Error
            ? associations.error.message
            : "Failed to load associations"
        }
        onRetry={() => associations.refetch()}
      />
    );
  }

  return (
    <div className="space-y-8">
      <IngestSection
        collectionId={collectionId}
        associations={ingest}
        connections={connections.data ?? []}
      />

      <DeliverySection
        collectionId={collectionId}
        associations={deliver}
        connections={connections.data ?? []}
      />
    </div>
  );
}
