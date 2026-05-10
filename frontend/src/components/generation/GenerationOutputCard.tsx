import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import { remarkPlugins, rehypePlugins, normalizeLatexMathDelimiters } from "../../utils/markdown";
import { Button, Card, Empty, Space, Tooltip } from "antd";
import { CopyOutlined, DownloadOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";

interface GenerationOutputCardProps {
  result: string;
  isLoading: boolean;
  error: string | null;
  copied: boolean;
  selectedSkillDisplayName?: string;
  onCopy: () => Promise<void>;
  onDownload: () => void;
}

export function GenerationOutputCard({
  result,
  isLoading,
  error,
  copied,
  selectedSkillDisplayName,
  onCopy,
  onDownload,
}: GenerationOutputCardProps) {
  const { formatMessage } = useIntl();

  return (
    <motion.div
      initial={{ opacity: 0, x: 10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.2 }}
    >
      <Card
        style={{
          borderRadius: 16,
          background: "var(--color-bg-surface)",
          borderColor: "var(--color-border)",
          minHeight: 400,
        }}
      >
        {result && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
            <Space>
              <Tooltip
                title={
                  copied
                    ? formatMessage({ id: "generation.output.copiedTooltip" })
                    : formatMessage({ id: "generation.output.copyTooltip" })
                }
              >
                <Button
                  type="text"
                  icon={<CopyOutlined />}
                  onClick={() => void onCopy()}
                  style={{ color: copied ? "var(--color-success)" : "var(--color-text-muted)" }}
                >
                  {copied
                    ? formatMessage({ id: "generation.output.copied" })
                    : formatMessage({ id: "generation.output.copy" })}
                </Button>
              </Tooltip>
              <Tooltip title={formatMessage({ id: "generation.output.downloadTooltip" })}>
                <Button
                  type="text"
                  icon={<DownloadOutlined />}
                  onClick={onDownload}
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {formatMessage({ id: "generation.output.download" })}
                </Button>
              </Tooltip>
            </Space>
          </div>
        )}
        {!result && !isLoading && !error && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={formatMessage({ id: "generation.output.empty" })}
          />
        )}

        {isLoading && !result && (
          <div style={{ textAlign: "center", padding: "40px 20px" }}>
            <div style={{ color: "var(--color-text-muted)" }}>
              {selectedSkillDisplayName
                ? formatMessage(
                    { id: "generation.output.generating" },
                    { skill: selectedSkillDisplayName.toLowerCase() },
                  )
                : formatMessage({ id: "generation.output.generatingFallback" })}
            </div>
          </div>
        )}

        {error && !isLoading && (
          <div
            style={{
              background: "var(--color-error-bg, #fff2f0)",
              borderRadius: 12,
              padding: 16,
              border: "1px solid var(--color-error-border, #ffccc7)",
              color: "var(--color-error, #ff4d4f)",
            }}
          >
            {error}
          </div>
        )}

        <AnimatePresence>
          {result && (
            <motion.div
              key="result"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              style={{
                background: "var(--color-bg-muted)",
                borderRadius: 12,
                padding: 20,
                border: "1px solid var(--color-border)",
              }}
            >
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
                  {normalizeLatexMathDelimiters(result)}
                </ReactMarkdown>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  );
}
