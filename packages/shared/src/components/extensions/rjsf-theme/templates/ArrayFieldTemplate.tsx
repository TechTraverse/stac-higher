import type { ArrayFieldTemplateProps } from "@rjsf/utils";
import { Button } from "@shared/components/ui/button";
import { Plus } from "lucide-react";

export function ArrayFieldTemplate({
  title,
  items,
  canAdd,
  onAddClick,
}: ArrayFieldTemplateProps) {
  return (
    <div className="space-y-3">
      {title && <p className="text-sm font-medium">{title}</p>}

      <div className="space-y-3">
        {items.map((item, i) => (
          <div key={i} className="p-3 rounded-md border border-border">
            {item}
          </div>
        ))}
      </div>

      {canAdd && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onAddClick}
        >
          <Plus className="h-3.5 w-3.5 mr-1" />
          Add Item
        </Button>
      )}
    </div>
  );
}
