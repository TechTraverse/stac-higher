/**
 * The lineage strip (ADR 0017's signature element): sources → processes →
 * product → destinations, drawn as connected health-dotted groups.
 *
 * ONE visual language at three zoom levels, not three widgets:
 *   - `mini`   — a single inline row of group counts (home product rows)
 *   - `medium` — grouped node cards (the product Overview tab)
 *   - `full`   — the pipeline-graph view
 *
 * Purely presentational and app-agnostic: it takes ordered groups of nodes
 * with a health verdict already decided. Every derivation — which alert makes
 * a node red, which edge belongs in which group — stays on the app side, so
 * this component can never disagree with the monitoring surfaces about what
 * "healthy" means.
 *
 * NOTE: `full` currently renders the `medium` layout at a larger scale. The
 * columnar pipeline-graph renderer lands with the /graph slice (UI-7).
 */
import { ArrowRight, Cpu, Layers, Plug, type LucideIcon } from "lucide-react";
import { cn } from "@shared/lib/utils";

export type LineageHealth = "ok" | "warn" | "error" | "unknown";
export type LineageKind = "connection" | "collection" | "process";
export type LineageSize = "mini" | "medium" | "full";

export interface LineageNode {
  id: string;
  label: string;
  /** Secondary line — an id, protocol, or trigger. Rendered monospace. */
  detail?: string;
  health?: LineageHealth;
  href?: string;
}

export interface LineageGroup {
  kind: LineageKind;
  /** Plural label for the group: "Sources", "Processes", "Destinations". */
  label: string;
  /** Singular label, used when the group holds exactly one node. */
  labelOne?: string;
  nodes: LineageNode[];
  /** Group-level verdict for `mini`, where individual nodes are collapsed. */
  health?: LineageHealth;
  href?: string;
}

export interface LineageStripProps {
  groups: LineageGroup[];
  size?: LineageSize;
  /** Shown when every group is empty. */
  emptyLabel?: string;
  className?: string;
}

const KIND_ICON: Record<LineageKind, LucideIcon> = {
  connection: Plug,
  collection: Layers,
  process: Cpu,
};

const DOT_CLASS: Record<LineageHealth, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  error: "bg-danger",
  unknown: "bg-muted-foreground/40",
};

export function healthDotClass(health: LineageHealth = "unknown"): string {
  return DOT_CLASS[health];
}

function groupLabel(group: LineageGroup): string {
  const n = group.nodes.length;
  if (n === 1 && group.labelOne) return group.labelOne;
  return group.label;
}

function HealthDot({
  health = "unknown",
  className,
}: {
  health?: LineageHealth;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        DOT_CLASS[health],
        className,
      )}
    />
  );
}

// -- mini --------------------------------------------------------------------

function MiniGroup({ group }: { group: LineageGroup }) {
  const Icon = KIND_ICON[group.kind];
  const count = group.nodes.length;
  const body = (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap",
        count === 0 && "text-muted-foreground/60",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="tabular-nums font-medium">{count}</span>
      <span>{groupLabel(group).toLowerCase()}</span>
      {count > 0 && <HealthDot health={group.health} />}
    </span>
  );
  // `group.href` is deliberately ignored here: the mini glyph is rendered
  // inside rows that are themselves links, and a nested <a> is invalid HTML.
  // Only the medium/full variants link their nodes.
  return body;
}

function MiniStrip({ groups, emptyLabel, className }: LineageStripProps) {
  if (groups.every((g) => g.nodes.length === 0)) {
    return (
      <span className={cn("text-xs text-muted-foreground/70", className)}>
        {emptyLabel ?? "Not wired"}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground",
        className,
      )}
    >
      {groups.map((group, i) => (
        <span
          key={`${group.kind}-${group.label}`}
          className="inline-flex items-center gap-2"
        >
          {i > 0 && (
            <ArrowRight
              aria-hidden="true"
              className="h-3 w-3 shrink-0 text-muted-foreground/50"
            />
          )}
          <MiniGroup group={group} />
        </span>
      ))}
    </span>
  );
}

// -- medium / full -----------------------------------------------------------

function NodeChip({ node, large }: { node: LineageNode; large: boolean }) {
  const inner = (
    <>
      <HealthDot health={node.health} />
      <span className="min-w-0">
        <span
          className={cn(
            "block truncate font-medium",
            large ? "text-sm" : "text-[13px]",
          )}
        >
          {node.label}
        </span>
        {node.detail && (
          <span className="tech block truncate text-[11px] text-muted-foreground">
            {node.detail}
          </span>
        )}
      </span>
    </>
  );
  const classes = cn(
    "flex items-center gap-2 rounded-md border border-border bg-card px-3",
    large ? "py-2.5" : "py-2",
  );
  return node.href ? (
    <a href={node.href} className={cn(classes, "hover:border-primary/50 hover:bg-accent")}>
      {inner}
    </a>
  ) : (
    <div className={classes}>{inner}</div>
  );
}

function GroupColumn({
  group,
  large,
}: {
  group: LineageGroup;
  large: boolean;
}) {
  const Icon = KIND_ICON[group.kind];
  return (
    <div className="min-w-0 flex-1 space-y-2">
      <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {groupLabel(group)}
      </div>
      {group.nodes.length === 0 ? (
        <div
          className={cn(
            "rounded-md border border-dashed border-border px-3 text-xs text-muted-foreground",
            large ? "py-2.5" : "py-2",
          )}
        >
          None
        </div>
      ) : (
        group.nodes.map((node) => (
          <NodeChip key={node.id} node={node} large={large} />
        ))
      )}
    </div>
  );
}

function ColumnStrip({ groups, emptyLabel, className, size }: LineageStripProps) {
  const large = size === "full";
  if (groups.every((g) => g.nodes.length === 0)) {
    return (
      <p className={cn("text-sm text-muted-foreground", className)}>
        {emptyLabel ?? "Nothing wired to this product yet."}
      </p>
    );
  }
  return (
    <div
      className={cn(
        "flex flex-col gap-3 sm:flex-row sm:items-start",
        large && "gap-5",
        className,
      )}
    >
      {groups.map((group, i) => (
        <div
          key={`${group.kind}-${group.label}`}
          className="flex min-w-0 flex-1 items-start gap-3"
        >
          {i > 0 && (
            <ArrowRight
              aria-hidden="true"
              className="mt-7 hidden h-4 w-4 shrink-0 text-muted-foreground/50 sm:block"
            />
          )}
          <GroupColumn group={group} large={large} />
        </div>
      ))}
    </div>
  );
}

export function LineageStrip({ size = "medium", ...props }: LineageStripProps) {
  return size === "mini" ? (
    <MiniStrip {...props} size={size} />
  ) : (
    <ColumnStrip {...props} size={size} />
  );
}
