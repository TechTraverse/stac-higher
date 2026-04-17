import { useStore } from "@nanostores/react";
import { $activeCatalog } from "@/stores/catalogStore";
import { useItem } from "@/lib/query/items";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { ItemFormPage } from "./ItemForm";
import { LoadingState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";

function ItemEditInner({
  collectionId,
  itemId,
}: {
  collectionId: string;
  itemId: string;
}) {
  const catalog = useStore($activeCatalog);
  const endpointUrl = catalog?.url ?? "";
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
