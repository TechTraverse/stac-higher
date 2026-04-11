import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface BboxInputProps {
  value: number[];
  onChange: (bbox: number[]) => void;
}

const labels = ["Min Longitude", "Min Latitude", "Max Longitude", "Max Latitude"];
const placeholders = ["-180", "-90", "180", "90"];

export function BboxInput({ value, onChange }: BboxInputProps) {
  const handleChange = (index: number, val: string) => {
    const next = [...value];
    next[index] = val === "" ? 0 : parseFloat(val);
    onChange(next);
  };

  return (
    <div className="grid grid-cols-2 gap-3">
      {labels.map((label, i) => (
        <div key={label} className="space-y-1">
          <Label className="text-xs">{label}</Label>
          <Input
            type="number"
            step="any"
            placeholder={placeholders[i]}
            value={value[i] ?? ""}
            onChange={(e) => handleChange(i, e.target.value)}
          />
        </div>
      ))}
    </div>
  );
}
