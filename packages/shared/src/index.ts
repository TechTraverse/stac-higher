// Types
export type {
  StacLink,
  StacAsset,
  StacProvider,
  StacSpatialExtent,
  StacTemporalExtent,
  StacExtent,
  StacCollection,
  StacItemProperties,
  StacItem,
  StacCollectionsResponse,
  StacItemCollection,
  StacSearchBody,
  StacLandingPage,
} from "@shared/lib/stac-api/types";
export { StacApiError } from "@shared/lib/stac-api/types";

// Utilities
export { cn } from "@shared/lib/utils";
export { bboxToPolygon, bboxToLngLatBounds, geometryToBbox } from "@shared/lib/map/bbox";

// Pipeline graph — pure lineage + layout (P-2)
export type {
  Graph,
  GraphEdge,
  GraphEdgeKind,
  GraphNode,
  GraphNodeType,
} from "@shared/lib/graph/types";
export { lineage, capLineage } from "@shared/lib/graph/lineage";
export type { CappedLineage, Lineage } from "@shared/lib/graph/lineage";
export { layeredLayout, LAYOUT_DEFAULTS } from "@shared/lib/graph/layout";
export type {
  Layout,
  LayoutOptions,
  PlacedEdge,
  PlacedNode,
} from "@shared/lib/graph/layout";
export { FIXTURE_GRAPH } from "@shared/lib/graph/fixtures";
export {
  FOOTPRINT_SOURCE,
  EXTENT_SOURCE,
  clamp01,
  footprintFillLayer,
  footprintLineLayer,
  footprintLayerIds,
  footprintLayers,
  extentFillLayer,
  extentLineLayer,
  selectedFillLayer,
  selectedLineLayer,
  RASTER_PREVIEW_SOURCE,
  RASTER_PREVIEW_LAYER,
  vectorTileLayers,
} from "@shared/lib/map/styles";

// Stores
export { $theme, toggleTheme, $sidebarOpen, toggleSidebar } from "@shared/stores/uiStore";
export type { DrawMode } from "@shared/stores/mapStore";
export { $mapViewState, $selectedFeatureIds, $drawMode, $drawnGeometry } from "@shared/stores/mapStore";

// Shared components
export { BboxInput } from "@shared/components/shared/BboxInput";
export { EmptyState } from "@shared/components/shared/EmptyState";
export { ErrorBoundary } from "@shared/components/shared/ErrorBoundary";
export { ErrorState } from "@shared/components/shared/ErrorState";
export { JsonViewer } from "@shared/components/shared/JsonViewer";
export {
  LineageStrip,
  healthDotClass,
  KIND_COLOR_VAR,
} from "@shared/components/shared/LineageStrip";
export type {
  LineageGroup,
  LineageHealth,
  LineageKind,
  LineageNode,
  LineageSize,
  LineageStripProps,
} from "@shared/components/shared/LineageStrip";
export { LoadingState } from "@shared/components/shared/LoadingState";
export { PipelineDag } from "@shared/components/shared/PipelineDag";
export type {
  DagNodeDecoration,
  PipelineDagProps,
  PipelineDagSize,
} from "@shared/components/shared/PipelineDag";

// Map components
export { StacMap } from "@shared/components/map/StacMap";
export { DrawingToolbar } from "@shared/components/map/DrawingToolbar";
export { FootprintLayer } from "@shared/components/map/FootprintLayer";
export type { FootprintLayerProps } from "@shared/components/map/FootprintLayer";
export { RasterTileLayer } from "@shared/components/map/RasterTileLayer";
export type { RasterTileLayerProps } from "@shared/components/map/RasterTileLayer";
export {
  RasterFrameStack,
  RASTER_FRAME_LOOKAHEAD,
  rasterFrameStackAnchorId,
} from "@shared/components/map/RasterFrameStack";
export type { RasterFrame, RasterFrameStackProps } from "@shared/components/map/RasterFrameStack";
export { VectorTileLayer } from "@shared/components/map/VectorTileLayer";
export type { VectorTileLayerProps } from "@shared/components/map/VectorTileLayer";
export { TimeSlider } from "@shared/components/map/TimeSlider";
export type { TimeSliderProps } from "@shared/components/map/TimeSlider";
export { ExtentLayer } from "@shared/components/map/ExtentLayer";

// Layout components
export { ThemeToggle } from "@shared/components/layout/ThemeToggle";

// Collection components
export { CollectionCard } from "@shared/components/collections/CollectionCard";

// Item components
export { ItemCard } from "@shared/components/items/ItemCard";

// Extensions — RJSF theme
export { shadcnTheme } from "@shared/components/extensions/rjsf-theme/theme";
export { TextWidget } from "@shared/components/extensions/rjsf-theme/widgets/TextWidget";
export { TextareaWidget } from "@shared/components/extensions/rjsf-theme/widgets/TextareaWidget";
export { NumberWidget } from "@shared/components/extensions/rjsf-theme/widgets/NumberWidget";
export { CheckboxWidget } from "@shared/components/extensions/rjsf-theme/widgets/CheckboxWidget";
export { SelectWidget } from "@shared/components/extensions/rjsf-theme/widgets/SelectWidget";
export { FieldTemplate } from "@shared/components/extensions/rjsf-theme/templates/FieldTemplate";
export { ObjectFieldTemplate } from "@shared/components/extensions/rjsf-theme/templates/ObjectFieldTemplate";
export { ArrayFieldTemplate } from "@shared/components/extensions/rjsf-theme/templates/ArrayFieldTemplate";

// UI primitives (re-exported for consumers that prefer one import location)
export { Button } from "@shared/components/ui/button";
export { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@shared/components/ui/card";
export { Badge } from "@shared/components/ui/badge";
export { Input } from "@shared/components/ui/input";
export { Label } from "@shared/components/ui/label";
export { Skeleton } from "@shared/components/ui/skeleton";
export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@shared/components/ui/tooltip";
export { Textarea } from "@shared/components/ui/textarea";
export { Slider } from "@shared/components/ui/slider";
export { Switch } from "@shared/components/ui/switch";
export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@shared/components/ui/select";
