import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import { useItem } from "@/lib/query/items";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { ItemFormPage } from "./ItemForm";
import { LoadingState } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";

function ItemEditInner({
  collectionId,
  itemId,
}: {
  collectionId: string;
  itemId: string;
}) {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
  const { data, isLoading, error, refetch } = useItem(
    endpointUrl,
    collectionId,
    itemId,
  );

  if (isLoading) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6">
          <LoadingState />
        </main>
      </>
    );
  }

  if (error || !data) {
    return (
      <>
        <Header />
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Item not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  return <ItemFormPage collectionId={collectionId} existingItem={data} />;
}

export function ItemEditPage({
  collectionId,
  itemId,
}: {
  collectionId: string;
  itemId: string;
}) {
  return (
    <QueryProvider>
      <ItemEditInner collectionId={collectionId} itemId={itemId} />
    </QueryProvider>
  );
}
