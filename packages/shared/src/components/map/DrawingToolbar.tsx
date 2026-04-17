import { Button } from "@shared/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@shared/components/ui/tooltip";
import { Pentagon, Square, MapPin, X } from "lucide-react";
import type { DrawMode } from "@shared/stores/mapStore";

interface DrawingToolbarProps {
  activeMode: DrawMode;
  onModeChange: (mode: DrawMode) => void;
}

const tools: {
  mode: NonNullable<DrawMode>;
  icon: typeof Pentagon;
  label: string;
  hint: string;
}[] = [
  {
    mode: "polygon",
    icon: Pentagon,
    label: "Draw Polygon",
    hint: "Click to add vertices, double-click to finish",
  },
  {
    mode: "bbox",
    icon: Square,
    label: "Draw Rectangle",
    hint: "Click two opposite corners to define a bounding box",
  },
  {
    mode: "point",
    icon: MapPin,
    label: "Place Point",
    hint: "Click on the map to place a point",
  },
];

export function DrawingToolbar({ activeMode, onModeChange }: DrawingToolbarProps) {
  return (
    <div className="absolute top-3 left-3 z-10 flex flex-col gap-1 bg-background/90 backdrop-blur rounded-lg border border-border p-1">
      {tools.map(({ mode, icon: Icon, label, hint }) => (
        <Tooltip key={mode}>
          <TooltipTrigger asChild>
            <Button
              variant={activeMode === mode ? "default" : "ghost"}
              size="icon"
              className="h-8 w-8"
              onClick={() => onModeChange(activeMode === mode ? null : mode)}
            >
              <Icon className="h-4 w-4" />
              <span className="sr-only">{label}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right" className="max-w-[200px]">
            <p className="font-medium text-xs">{label}</p>
            <p className="text-xs text-muted-foreground">{hint}</p>
          </TooltipContent>
        </Tooltip>
      ))}
      {activeMode && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onModeChange(null)}
            >
              <X className="h-4 w-4" />
              <span className="sr-only">Cancel drawing</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            <p className="text-xs">Cancel drawing</p>
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}
