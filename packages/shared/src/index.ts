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
export {
  FOOTPRINT_SOURCE,
  EXTENT_SOURCE,
  footprintFillLayer,
  footprintLineLayer,
  extentFillLayer,
  extentLineLayer,
  selectedFillLayer,
  selectedLineLayer,
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
export { LoadingState } from "@shared/components/shared/LoadingState";

// Map components
export { StacMap } from "@shared/components/map/StacMap";
export { DrawingToolbar } from "@shared/components/map/DrawingToolbar";
export { FootprintLayer } from "@shared/components/map/FootprintLayer";
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
export { Switch } from "@shared/components/ui/switch";
export {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@shared/components/ui/select";
