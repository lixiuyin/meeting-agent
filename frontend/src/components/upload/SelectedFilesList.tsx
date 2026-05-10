import type { UploadFile } from "antd";
import { Button, Tooltip } from "antd";
import { CloseCircleOutlined } from "@ant-design/icons";
import { AnimatePresence, motion } from "framer-motion";
import { useIntl } from "react-intl";
import { formatFileSize, getFileIcon, getPreviewMode } from "./fileUtils";

interface Props {
  fileList: UploadFile[];
  uploading: boolean;
  currentFileIndex: number;
  previewByUid: Record<string, string>;
  onRemoveFile: (uid: string) => void;
}

export function SelectedFilesList({
  fileList,
  uploading,
  currentFileIndex,
  previewByUid,
  onRemoveFile,
}: Props) {
  const { formatMessage } = useIntl();

  return (
    <AnimatePresence>
      {fileList.length > 0 && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          style={{
            marginTop: 12,
            maxHeight: 200,
            overflow: "auto",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            padding: 8,
          }}
        >
          {fileList.map((file, index) => (
            <motion.div
              key={file.uid}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 12px",
                background:
                  uploading && index === currentFileIndex
                    ? "var(--color-primary-alpha)"
                    : "var(--color-bg-muted)",
                borderRadius: 6,
                marginBottom: 4,
                border:
                  uploading && index === currentFileIndex
                    ? "1px solid var(--color-primary)"
                    : "1px solid transparent",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                {(() => {
                  const { icon, color } = getFileIcon(file.name);
                  const mode = getPreviewMode(file.name);
                  const previewUrl = previewByUid[file.uid];
                  if (!mode || !previewUrl) {
                    return (
                      <div
                        style={{
                          width: 28,
                          height: 28,
                          borderRadius: 6,
                          background: `${color}15`,
                          color,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          flexShrink: 0,
                          fontSize: 12,
                        }}
                      >
                        {icon}
                      </div>
                    );
                  }
                  if (mode === "image") {
                    return (
                      <img
                        src={previewUrl}
                        alt={file.name}
                        style={{
                          width: 28,
                          height: 28,
                          borderRadius: 6,
                          objectFit: "cover",
                          flexShrink: 0,
                        }}
                      />
                    );
                  }
                  if (mode === "video") {
                    return (
                      <video
                        src={previewUrl}
                        muted
                        preload="metadata"
                        aria-label={file.name}
                        style={{
                          width: 28,
                          height: 28,
                          borderRadius: 6,
                          objectFit: "cover",
                          flexShrink: 0,
                        }}
                      />
                    );
                  }
                  return (
                    <div
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: 6,
                        background: `${color}15`,
                        color,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                        fontSize: 12,
                      }}
                    >
                      {icon}
                    </div>
                  );
                })()}
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 500,
                      color: "var(--color-text-primary)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                    title={file.name}
                  >
                    {file.name}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>
                    {file.originFileObj ? formatFileSize(file.originFileObj.size) : ""}
                    {uploading && index === currentFileIndex && (
                      <span style={{ color: "var(--color-primary)", marginLeft: 8 }}>
                        {formatMessage({ id: "upload.selected.uploading" })}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {!uploading && (
                <Tooltip title={formatMessage({ id: "upload.selected.removeFile" })}>
                  <Button
                    type="text"
                    size="small"
                    icon={<CloseCircleOutlined />}
                    aria-label={formatMessage({ id: "upload.selected.removeFile" })}
                    onClick={() => onRemoveFile(file.uid)}
                    style={{ color: "var(--color-text-muted)", flexShrink: 0 }}
                  />
                </Tooltip>
              )}
            </motion.div>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
