import type { ObjectFieldTemplateProps } from "@rjsf/utils";

export function ObjectFieldTemplate({
  properties,
  title,
  description,
}: ObjectFieldTemplateProps) {
  const isRoot = !title;

  if (isRoot) {
    return (
      <div className="space-y-4">
        {properties.map((prop) => (
          <div key={prop.name}>{prop.content}</div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
      <div className="space-y-4 pl-3 border-l border-border">
        {properties.map((prop) => (
          <div key={prop.name}>{prop.content}</div>
        ))}
      </div>
    </div>
  );
}
