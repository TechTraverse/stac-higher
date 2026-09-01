import { useExtension } from "@/lib/extensions/queries";
import { AppShell } from "@/components/layout/AppShell";
import { ExtensionFormPage } from "./ExtensionForm";
import { LoadingState } from "@stac-higher/shared";
import { ErrorState } from "@stac-higher/shared";

function ExtensionEditInner({ extensionId }: { extensionId: string }) {
  const { data, isLoading, error, refetch } = useExtension(extensionId);

  if (isLoading) {
    return (
      <>
        <main className="flex-1 p-6">
          <LoadingState />
        </main>
      </>
    );
  }

  if (error || !data) {
    return (
      <>
        <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
          <ErrorState
            message={error instanceof Error ? error.message : "Extension not found"}
            onRetry={() => refetch()}
          />
        </main>
      </>
    );
  }

  return <ExtensionFormPage existingExtension={data} />;
}

export function ExtensionEditPage({ extensionId }: { extensionId: string }) {
  return (
    <AppShell>
      <ExtensionEditInner extensionId={extensionId} />
    </AppShell>
  );
}
