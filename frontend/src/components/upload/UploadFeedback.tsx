import { useEffect, useState } from "react";
import { CheckCircleOutlined } from "@ant-design/icons";
import { Alert, Progress, Spin } from "antd";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";

export interface RetryInfo {
  fileName: string;
  countdown: number;
  attempt: number;
  maxAttempts: number;
}

interface Props {
  uploading: boolean;
  currentFileIndex: number;
  fileCount: number;
  uploadError: string | null;
  success: boolean;
  retryInfo?: RetryInfo | null;
}

export function UploadFeedback({
  uploading,
  currentFileIndex,
  fileCount,
  uploadError,
  success,
  retryInfo,
}: Props) {
  const { formatMessage } = useIntl();
  const retryKey = retryInfo
    ? `${retryInfo.fileName}:${retryInfo.countdown}:${retryInfo.attempt}:${retryInfo.maxAttempts}`
    : "none";

  return (
    <UploadFeedbackInner
      key={retryKey}
      {...{
        uploading,
        currentFileIndex,
        fileCount,
        uploadError,
        success,
        retryInfo,
        formatMessage,
      }}
    />
  );
}

function UploadFeedbackInner({
  uploading,
  currentFileIndex,
  fileCount,
  uploadError,
  success,
  retryInfo,
  formatMessage,
}: Props & { formatMessage: ReturnType<typeof useIntl>["formatMessage"] }) {
  const [countdownOffset, setCountdownOffset] = useState(0);

  useEffect(() => {
    if (!retryInfo) return;
    const interval = setInterval(() => {
      setCountdownOffset((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(interval);
  }, [retryInfo]);

  const countdownDisplay = retryInfo ? Math.max(retryInfo.countdown - countdownOffset, 0) : 0;

  return (
    <>
      {retryInfo && countdownDisplay > 0 && (
        <Alert
          type="warning"
          showIcon
          title={formatMessage({ id: "upload.feedback.rateLimited" })}
          description={formatMessage(
            { id: "upload.feedback.retrying" },
            {
              fileName: retryInfo.fileName,
              countdown: countdownDisplay,
              attempt: retryInfo.attempt,
              maxAttempts: retryInfo.maxAttempts,
            },
          )}
          style={{ marginBottom: 12 }}
        />
      )}

      {uploading && fileCount > 0 && !retryInfo && (
        <div
          style={{
            marginBottom: 12,
            padding: "12px",
            background: "var(--color-primary-alpha)",
            borderRadius: 8,
          }}
        >
          <div style={{ marginBottom: 8, color: "var(--color-primary)", fontWeight: 500 }}>
            <Spin size="small" style={{ marginRight: 8 }} />
            {formatMessage(
              { id: "upload.feedback.uploading" },
              { current: currentFileIndex + 1, total: fileCount },
            )}
          </div>
          <Progress
            percent={Math.min(100, Math.round(((currentFileIndex + 1) / fileCount) * 100))}
            strokeColor="var(--color-primary)"
            showInfo={false}
            size="small"
          />
        </div>
      )}

      {uploadError && (
        <Alert
          type="error"
          showIcon
          title={formatMessage({ id: "upload.feedback.error" })}
          description={<pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{uploadError}</pre>}
          style={{ marginBottom: 12 }}
        />
      )}

      {success && (
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            padding: 12,
            background: "var(--color-success-alpha)",
            borderRadius: 8,
            marginBottom: 12,
            color: "var(--color-success)",
          }}
        >
          <CheckCircleOutlined style={{ fontSize: 18 }} />
          <span>{formatMessage({ id: "upload.feedback.success" })}</span>
        </motion.div>
      )}
    </>
  );
}
