import { useStore } from "@nanostores/react";
import { $activeCatalog } from "@/stores/catalogStore";
import { useCollection } from "@/lib/query/collections";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { CollectionFormPage } from "./CollectionForm";
import { LoadingState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";

function CollectionEditInner({ collectionId }: { collectionId: string }) {
  const catalog = useStore($activeCatalog);
  const endpointUrl = catalog?.url ?? "";
  const { data, isLoading, error, refetch } = useCollection(endpointUrl, collectionId);

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
            message={error instanceof Error ? error.message : "Collection not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  return <CollectionFormPage existingCollection={data} />;
}

export function CollectionEditPage({ collectionId }: { collectionId: string }) {
  return (
    <QueryProvider>
      <CollectionEditInner collectionId={collectionId} />
    </QueryProvider>
  );
}
