/**
 * Manual asset upload for the item form (ROADMAP Phase 3, flow C / §6.3).
 *
 * Picks a file → asks `/api/uploads` for a presigned PUT URL → uploads the bytes
 * straight to object storage → hands the resulting `/api/assets/...` href back to
 * the form. The app never streams the bytes; only the href is persisted.
 */
import { useRef, useState } from "react";
import { Button } from "@stac-higher/shared";
import { Upload, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { uploadAsset, type UploadedAsset } from "@/lib/storage/upload";

interface AssetUploadProps {
  collection: string;
  itemId: string;
  onUploaded: (result: UploadedAsset) => void;
}

export function AssetUpload({ collection, itemId, onUploaded }: AssetUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  // The canonical key is scoped by item id — without one there is nowhere to
  // put the file. On create the user must set the Item ID first.
  const ready = itemId.trim().length > 0;

  const handleFile = async (file: File) => {
    setUploading(true);
    try {
      const result = await uploadAsset(file, { collection, item: itemId });
      onUploaded(result);
      toast.success(`Uploaded ${file.name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!ready || uploading}
        title={ready ? undefined : "Set the Item ID first"}
        onClick={() => inputRef.current?.click()}
      >
        {uploading ? (
          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
        ) : (
          <Upload className="h-3.5 w-3.5 mr-1.5" />
        )}
        {uploading ? "Uploading..." : "Upload file"}
      </Button>
    </div>
  );
}
