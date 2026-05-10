import { Button } from "antd";
import { useIntl } from "react-intl";

interface Props {
  uploading: boolean;
  currentFileIndex: number;
  fileCount: number;
  mode: "new" | "existing";
  onUpload: () => void;
}

export function UploadSubmitButton({
  uploading,
  currentFileIndex,
  fileCount,
  mode,
  onUpload,
}: Props) {
  const { formatMessage } = useIntl();

  return (
    <Button
      type="primary"
      onClick={onUpload}
      loading={uploading}
      block
      size="large"
      disabled={fileCount === 0}
      style={{
        borderRadius: 10,
        fontWeight: 600,
        height: 44,
        background: "var(--gradient-primary)",
        border: "none",
      }}
    >
      {uploading
        ? formatMessage(
            { id: "upload.submit.uploading" },
            { current: currentFileIndex + 1, total: fileCount },
          )
        : `${mode === "new" ? formatMessage({ id: "upload.submit.createNew" }) : formatMessage({ id: "upload.submit.uploadExisting" })} ${fileCount > 0 ? `(${fileCount})` : ""}`}
    </Button>
  );
}
