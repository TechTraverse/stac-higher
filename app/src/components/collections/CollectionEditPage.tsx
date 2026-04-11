import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import { useCollection } from "@/lib/query/collections";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { CollectionFormPage } from "./CollectionForm";
import { LoadingState } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";

function CollectionEditInner({ collectionId }: { collectionId: string }) {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
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
