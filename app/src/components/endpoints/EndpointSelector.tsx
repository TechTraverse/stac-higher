import { useStore } from "@nanostores/react";
import {
  $endpoints,
  $activeEndpoint,
  setActiveEndpoint,
} from "@/stores/endpointStore";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Globe } from "lucide-react";

export function EndpointSelector() {
  const endpoints = useStore($endpoints);
  const active = useStore($activeEndpoint);

  if (endpoints.length === 0) {
    return (
      <a
        href="/endpoints"
        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        Add an endpoint
      </a>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Globe className="h-4 w-4 text-muted-foreground" />
      <Select value={active?.id ?? ""} onValueChange={setActiveEndpoint}>
        <SelectTrigger className="h-8 w-[200px] text-xs">
          <SelectValue placeholder="Select endpoint" />
        </SelectTrigger>
        <SelectContent>
          {endpoints.map((ep) => (
            <SelectItem key={ep.id} value={ep.id} className="text-xs">
              {ep.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
