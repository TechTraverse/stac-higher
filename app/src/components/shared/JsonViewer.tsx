import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronRight, Copy, Check } from "lucide-react";

interface JsonViewerProps {
  data: unknown;
  title?: string;
  defaultOpen?: boolean;
}

export function JsonViewer({ data, title = "Raw JSON", defaultOpen = false }: JsonViewerProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [copied, setCopied] = useState(false);

  const jsonStr = JSON.stringify(data, null, 2);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(jsonStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        className="flex items-center gap-2 w-full px-4 py-3 text-sm font-medium hover:bg-accent/50 transition-colors"
        onClick={() => setOpen(!open)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        {title}
      </button>
      {open && (
        <div className="relative border-t border-border">
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-2 right-2 h-7 w-7"
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy JSON"}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-green-500" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
          <pre className="p-4 overflow-auto max-h-[500px] text-xs font-mono text-muted-foreground">
            {jsonStr}
          </pre>
        </div>
      )}
    </div>
  );
}
