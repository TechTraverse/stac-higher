/**
 * Settings tab "Virtual cube" card (Z-8b, virtual cube spec §9): create and
 * edit the cube sink of this collection, toggle it, and read its status.
 * Mounted only after the collection settings loaded, so the caller manages the
 * collection and a GET 404 means "no sink yet". Rules mirrored from the API:
 *  - always send `enabled` on PUT (the schema defaults an omitted one to true);
 *  - once `last_snapshot_id` is set the source and layout fields are locked;
 *  - while it is null and ledger rows are pending, every write is refused: any
 *    app write bumps the row version and makes the pipeline discard and redo
 *    the first append.
 */
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  Switch,
} from "@stac-higher/shared";
import { Box, Save } from "lucide-react";
import { toast } from "sonner";
import {
  useCubeSink,
  useFreshCubeSink,
  usePatchCubeSink,
  usePutCubeSink,
  useReferenceSources,
} from "@/lib/cubes/queries";
import {
  CUBE_FORM_DEFAULTS,
  apiIssuesToFormErrors,
  cubeSinkFormSchema,
  formToPutBody,
  sinkToForm,
  type CubeSinkFormValues,
} from "@/lib/cubes/form";
import type { CubeSinkView } from "@/lib/cubes/queries";
import { CUBE_SOURCE_REFUSAL_CODES } from "@/lib/cubes/status";
import { CubeSinkStatus } from "./CubeSinkStatus";

const REFUSAL_LABEL: Record<string, string> = {
  no_reference_ingest: "no reference-mode ingest",
  unsupported_source_protocol: "not an S3 source",
  signed_source_unsupported: "signed (private) source",
};

const RELOAD_MESSAGE =
  "The sink changed since this page loaded. It has been reloaded; review and save again.";

export function CubeSinkCard({
  collectionId,
  canAct,
}: {
  collectionId: string;
  canAct: boolean;
}) {
  const {
    data: view,
    isLoading,
    isError,
    error: loadError,
    refetch,
  } = useCubeSink(collectionId);
  const fetchFresh = useFreshCubeSink(collectionId);
  const sources = useReferenceSources(canAct);
  const put = usePutCubeSink(collectionId);
  const patch = usePatchCubeSink(collectionId);

  const [open, setOpen] = useState(false);
  const [alert, setAlert] = useState<string | null>(null);
  const [toggleAlert, setToggleAlert] = useState<string | null>(null);

  const sink = view?.sink ?? null;
  const locked = Boolean(sink?.last_snapshot_id);
  // The view a fresh read or a 409 found pending while the cached one said
  // otherwise; it lapses as soon as the cache holds a newer view.
  const [pendingFor, setPendingFor] = useState<CubeSinkView | null>(null);
  const firstAppendPending =
    sink !== null &&
    !sink.last_snapshot_id &&
    ((view?.ledger.counts.pending ?? 0) > 0 ||
      (pendingFor !== null && pendingFor === view));

  const form = useForm<CubeSinkFormValues>({
    resolver: zodResolver(cubeSinkFormSchema),
    defaultValues: sink ? sinkToForm(sink) : CUBE_FORM_DEFAULTS,
  });
  const { register, handleSubmit, setError, reset, watch, setValue, formState } =
    form;

  // Re-seed when the stored row's version or repository existence changes.
  // The pipeline never writes `version` (updated_at); its first commit sets
  // only `last_snapshot_id`, which locks the layout. Unsaved edits survive a
  // re-seed (e.g. the Enabled toggle bumps the version) except after a 409,
  // which says the form's basis is stale.
  const version = sink?.version;
  const snapshotId = sink?.last_snapshot_id ?? null;
  const forceReseed = useRef(false);
  useEffect(() => {
    if (!sink) return;
    reset(
      sinkToForm(sink),
      forceReseed.current ? undefined : { keepDirtyValues: true },
    );
    forceReseed.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, snapshotId]);

  if (isLoading) {
    return (
      <Card data-testid="cube-sink-card">
        <CardContent className="pt-6">
          <Skeleton className="h-24 w-full" data-testid="cube-sink-loading" />
        </CardContent>
      </Card>
    );
  }
  if (isError && view === undefined) {
    // Never fall through to "Set up": its Save would replace a sink that
    // exists but failed to load.
    return (
      <div data-testid="cube-sink-error">
        <ErrorState
          title="Could not load the virtual cube"
          message={
            loadError instanceof Error ? loadError.message : "Request failed"
          }
          onRetry={() => void refetch()}
        />
      </div>
    );
  }
  if (!sink && !canAct) return null;

  const showForm = canAct && (sink !== null || open);
  const writeBlocked =
    firstAppendPending || put.isPending || patch.isPending;
  const errors = formState.errors;

  const sourceOptions = (sources.data ?? []).filter(
    (s) => s.collection_id !== collectionId,
  );
  const eligible = sourceOptions.some((s) => s.refusal === null);
  const currentSource = watch("source_collection_id");
  const knownCurrent = sourceOptions.some(
    (s) => s.collection_id === currentSource,
  );

  /** Reads the sink fresh and reports whether the first append is running
   * now. A failed read does not block: the server refuses the write itself. */
  const firstAppendRunningNow = async (): Promise<boolean> => {
    try {
      const fresh = await fetchFresh();
      const running =
        fresh !== null &&
        !fresh.sink.last_snapshot_id &&
        fresh.ledger.counts.pending > 0;
      if (running) setPendingFor(fresh);
      return running;
    } catch {
      return false;
    }
  };

  const onSubmit = async (values: CubeSinkFormValues) => {
    if (writeBlocked) return;
    setAlert(null);
    if (sink && (await firstAppendRunningNow())) return;
    put.mutate(formToPutBody(values, sink?.enabled ?? true, sink?.config), {
      onSuccess: (data) => {
        toast.success("Virtual cube saved");
        if (data?.sink) reset(sinkToForm(data.sink));
      },
      onError: (err) => {
        if (err.status === 400) {
          const mapped = apiIssuesToFormErrors(err.details);
          for (const [field, message] of Object.entries(mapped)) {
            if (field === "root") setAlert(message ?? err.message);
            else
              setError(field as keyof CubeSinkFormValues, { message });
          }
          if (Object.keys(mapped).length === 0) setAlert(err.message);
        } else if (
          err.status === 422 &&
          err.code &&
          (CUBE_SOURCE_REFUSAL_CODES as readonly string[]).includes(err.code)
        ) {
          setError("source_collection_id", { message: err.message });
        } else if (err.status === 409 && err.code === "first_append_pending") {
          setPendingFor(view ?? null);
        } else if (
          err.status === 409 &&
          (err.code === "cube_layout_locked" ||
            err.code === "cube_sink_conflict")
        ) {
          forceReseed.current = true;
          setAlert(RELOAD_MESSAGE);
        } else {
          setAlert(err.message);
        }
      },
    });
  };

  const onToggle = async (enabled: boolean) => {
    if (writeBlocked) return;
    setToggleAlert(null);
    if (await firstAppendRunningNow()) return;
    patch.mutate(
      { enabled },
      {
        onError: (err) => {
          if (err.status === 409 && err.code === "first_append_pending") {
            setPendingFor(view ?? null);
          } else {
            setToggleAlert(err.message);
          }
        },
      },
    );
  };

  const fieldError = (name: keyof CubeSinkFormValues) =>
    errors[name]?.message ? (
      <p
        className="text-xs text-destructive"
        role="alert"
        data-testid={`cube-sink-error-${name}`}
      >
        {errors[name]?.message}
      </p>
    ) : null;

  return (
    <Card data-testid="cube-sink-card">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Box className="h-5 w-5" />
          Virtual cube
        </CardTitle>
      </CardHeader>
      <CardContent className="grid max-w-xl gap-5">
        {view && <CubeSinkStatus view={view} />}

        {!sink && !open && (
          <div className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              Keep a rolling window of the source's files as virtual references
              in an Icechunk cube, served as map tiles and point time series.
              No source bytes are copied.
            </p>
            <div>
              <Button
                data-testid="cube-sink-setup"
                variant="outline"
                onClick={() => setOpen(true)}
              >
                Set up a virtual cube
              </Button>
            </div>
          </div>
        )}

        {sink && canAct && (
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="cube-sink-enabled">Enabled</Label>
            <Switch
              id="cube-sink-enabled"
              data-testid="cube-sink-enabled"
              checked={sink.enabled}
              onCheckedChange={(v) => void onToggle(v)}
              disabled={writeBlocked}
            />
          </div>
        )}
        {toggleAlert && (
          <p
            className="text-sm text-destructive"
            role="alert"
            data-testid="cube-sink-toggle-alert"
          >
            {toggleAlert}
          </p>
        )}

        {showForm && (
          <form
            className="grid gap-4"
            onSubmit={handleSubmit(onSubmit)}
            data-testid="cube-sink-form"
            noValidate
          >
            {locked && (
              <p
                className="text-xs text-muted-foreground"
                data-testid="cube-sink-locked-note"
              >
                Locked: the cube repository exists. Only the window can change.
              </p>
            )}
            {firstAppendPending && (
              <p
                className="text-xs text-muted-foreground"
                data-testid="cube-sink-pending-note"
              >
                The first append is running. Changes wait until it commits: an
                edit now would make the pipeline discard and redo it.
              </p>
            )}

            <div className="grid gap-1.5">
              <Label>Source collection</Label>
              <Select
                value={currentSource}
                onValueChange={(v) =>
                  setValue("source_collection_id", v, {
                    shouldDirty: true,
                    shouldValidate: formState.isSubmitted,
                  })
                }
                disabled={locked}
              >
                <SelectTrigger data-testid="cube-sink-source">
                  <SelectValue placeholder="Choose a source collection" />
                </SelectTrigger>
                <SelectContent>
                  {currentSource !== "" && !knownCurrent && (
                    <SelectItem value={currentSource}>
                      {currentSource}
                    </SelectItem>
                  )}
                  {sourceOptions.map((s) => (
                    <SelectItem
                      key={s.collection_id}
                      value={s.collection_id}
                      disabled={s.refusal !== null}
                    >
                      {s.title
                        ? `${s.title} (${s.collection_id})`
                        : s.collection_id}
                      {s.refusal
                        ? `: ${REFUSAL_LABEL[s.refusal] ?? s.refusal}`
                        : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {sources.isSuccess && !eligible && !locked && (
                <p
                  className="text-xs text-muted-foreground"
                  data-testid="cube-sink-no-source"
                >
                  No eligible source: add a reference-mode ingest association
                  on an anonymous S3 connection first.
                </p>
              )}
              {fieldError("source_collection_id")}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="cube-sink-append-dim">Append dimension</Label>
              <Input
                id="cube-sink-append-dim"
                data-testid="cube-sink-append-dim"
                disabled={locked}
                {...register("append_dim")}
              />
              {fieldError("append_dim")}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="cube-sink-variables">Variables</Label>
              <Input
                id="cube-sink-variables"
                data-testid="cube-sink-variables"
                disabled={locked}
                {...register("variables")}
              />
              <p className="text-xs text-muted-foreground">
                Comma-separated.
              </p>
              {fieldError("variables")}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="cube-sink-loadable">Loadable variables</Label>
              <Input
                id="cube-sink-loadable"
                data-testid="cube-sink-loadable-variables"
                disabled={locked}
                {...register("loadable_variables")}
              />
              <p className="text-xs text-muted-foreground">
                Comma-separated; must include the append dimension.
              </p>
              {fieldError("loadable_variables")}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="cube-sink-max-steps">Max steps</Label>
                <Input
                  id="cube-sink-max-steps"
                  data-testid="cube-sink-max-steps"
                  inputMode="numeric"
                  placeholder="no cap"
                  {...register("max_steps")}
                />
                {fieldError("max_steps")}
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="cube-sink-max-age">Max age</Label>
                <Input
                  id="cube-sink-max-age"
                  data-testid="cube-sink-max-age"
                  placeholder="e.g. 24h"
                  {...register("max_age")}
                />
                {fieldError("max_age")}
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Window: steps older than the max age, or beyond the newest max
              steps, are trimmed. Leave both empty to keep every step (30d max
              age).
            </p>

            {alert && (
              <p
                className="text-sm text-destructive"
                role="alert"
                data-testid="cube-sink-alert"
              >
                {alert}
              </p>
            )}

            <div>
              <Button
                type="submit"
                data-testid="cube-sink-save"
                disabled={writeBlocked}
              >
                <Save className="mr-1.5 h-4 w-4" />
                {sink ? "Save virtual cube" : "Create virtual cube"}
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
