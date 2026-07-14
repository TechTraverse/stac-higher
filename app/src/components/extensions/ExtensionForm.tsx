import { FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  extensionFormSchema,
  extensionToForm,
  formToExtensionSchema,
  type ExtensionFormValues,
} from "@/lib/extensions/schemas";
import type { StacExtension } from "@/lib/extensions/types";
import {
  useCreateExtension,
  useUpdateExtension,
} from "@/lib/extensions/queries";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { Button, JsonViewer } from "@stac-higher/shared";
import { ArrowLeft, Save } from "lucide-react";
import { toast } from "sonner";
import { ExtensionMetaFields } from "./ExtensionMetaFields";
import { SchemaEditor } from "./SchemaEditor";

interface ExtensionFormInnerProps {
  existingExtension?: StacExtension;
}

function ExtensionFormInner({ existingExtension }: ExtensionFormInnerProps) {
  const isEdit = !!existingExtension;
  const createMutation = useCreateExtension();
  const updateMutation = useUpdateExtension();

  const form = useForm<ExtensionFormValues>({
    resolver: zodResolver(extensionFormSchema) as any,
    defaultValues: existingExtension
      ? extensionToForm(existingExtension)
      : {
          name: "",
          prefix: "",
          version: "1.0.0",
          description: "",
          properties: [
            {
              name: "",
              type: "string",
              description: "",
              required: false,
            },
          ],
        },
  });

  const {
    handleSubmit,
    watch,
    formState: { isSubmitting },
  } = form;

  const previewSchema = formToExtensionSchema(watch());

  const onSubmit = (data: ExtensionFormValues) => {
    if (isEdit && existingExtension) {
      updateMutation.mutate(
        { id: existingExtension.id, data },
        {
          onSuccess: () => {
            toast.success("Extension updated");
            window.location.href = `/extensions/${encodeURIComponent(existingExtension.id)}`;
          },
          onError: (err) => toast.error(`Update failed: ${err.message}`),
        },
      );
    } else {
      createMutation.mutate(data, {
        onSuccess: (ext) => {
          toast.success("Extension created");
          window.location.href = `/extensions/${encodeURIComponent(ext.id)}`;
        },
        onError: (err) => toast.error(`Create failed: ${err.message}`),
      });
    }
  };

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a
            href="/extensions"
            className="hover:text-foreground transition-colors inline-flex items-center gap-1"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Extensions
          </a>
          <span>/</span>
          <span className="text-foreground">
            {isEdit ? `Edit ${existingExtension.name}` : "New Extension"}
          </span>
        </div>

        <h1 className="text-2xl font-bold mb-6">
          {isEdit ? "Edit Extension" : "Create Extension"}
        </h1>

        <FormProvider {...form}>
          <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
              <ExtensionMetaFields isEdit={isEdit} />
              <SchemaEditor />

              <div className="flex items-center gap-3">
                <Button type="submit" disabled={isSubmitting}>
                  <Save className="h-4 w-4 mr-1.5" />
                  {isSubmitting
                    ? "Saving..."
                    : isEdit
                      ? "Update Extension"
                      : "Create Extension"}
                </Button>
                <a href="/extensions">
                  <Button type="button" variant="outline">
                    Cancel
                  </Button>
                </a>
              </div>
            </form>

            <div className="hidden lg:block">
              <div className="sticky top-20">
                <JsonViewer data={previewSchema} title="JSON Schema Preview" defaultOpen />
              </div>
            </div>
          </div>
        </FormProvider>
      </main>
    </>
  );
}

export function ExtensionFormPage({
  existingExtension,
}: {
  existingExtension?: StacExtension;
}) {
  return (
    <QueryProvider>
      <ExtensionFormInner existingExtension={existingExtension} />
    </QueryProvider>
  );
}
