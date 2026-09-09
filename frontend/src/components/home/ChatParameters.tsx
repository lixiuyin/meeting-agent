import { Button, Switch, Select, Input } from "antd";
import { SettingOutlined, GlobalOutlined } from "@ant-design/icons";
import { AnimatePresence, motion } from "framer-motion";
import type { MemoryMode, RetrievalProfile } from "../../api/client-chat";
import { useIntl } from "react-intl";

const FILE_TYPE_OPTIONS = [
  { value: "video", label: "Video" },
  { value: "audio", label: "Audio" },
  { value: "pdf", label: "PDF" },
  { value: "ppt", label: "Presentation" },
  { value: "doc", label: "Document" },
  { value: "image", label: "Image" },
  { value: "txt", label: "Text" },
  { value: "csv", label: "CSV" },
  { value: "xls", label: "Spreadsheet" },
];

interface ChatParametersProps {
  expanded: boolean;
  onToggle: () => void;
  useWebSearch: boolean;
  onUseWebSearchChange: (v: boolean) => void;
  selectedTypeFilters: string[];
  onTypeFiltersChange: (v: string[]) => void;
  dateFrom: string;
  onDateFromChange: (v: string) => void;
  dateTo: string;
  onDateToChange: (v: string) => void;
  validAt: string;
  onValidAtChange: (v: string) => void;
  knownAt: string;
  onKnownAtChange: (v: string) => void;
  continuationMode: "latest" | "saved_scope" | "saved_snapshot";
  onContinuationModeChange: (v: "latest" | "saved_scope" | "saved_snapshot") => void;
  ragMode: "vector" | "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto";
  onRagModeChange: (
    v: "vector" | "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto",
  ) => void;
  retrievalProfile: RetrievalProfile;
  onRetrievalProfileChange: (v: RetrievalProfile) => void;
  memoryMode: MemoryMode;
  onMemoryModeChange: (v: MemoryMode) => void;
  activeParamCount: number;
}

export default function ChatParameters({
  expanded,
  onToggle,
  useWebSearch,
  onUseWebSearchChange,
  selectedTypeFilters,
  onTypeFiltersChange,
  dateFrom,
  onDateFromChange,
  dateTo,
  onDateToChange,
  validAt,
  onValidAtChange,
  knownAt,
  onKnownAtChange,
  continuationMode,
  onContinuationModeChange,
  ragMode,
  onRagModeChange,
  retrievalProfile,
  onRetrievalProfileChange,
  memoryMode,
  onMemoryModeChange,
  activeParamCount,
}: ChatParametersProps) {
  const { formatMessage } = useIntl();
  return (
    <div
      style={{
        marginBottom: expanded ? 12 : 0,
      }}
    >
      <Button
        type="text"
        size="small"
        icon={<SettingOutlined />}
        onClick={onToggle}
        style={{
          color: activeParamCount > 0 ? "var(--color-primary)" : "var(--color-text-muted)",
          fontSize: 13,
        }}
      >
        Modes & filters
        {activeParamCount > 0 && (
          <span
            style={{
              marginLeft: 6,
              padding: "0 6px",
              borderRadius: 10,
              background: "var(--color-primary)",
              color: "#fff",
              fontSize: 11,
            }}
          >
            {activeParamCount}
          </span>
        )}
      </Button>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                marginTop: 8,
                padding: 12,
                background: "var(--color-bg-muted)",
                borderRadius: 12,
                border: "1px solid var(--color-border)",
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: 16,
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  Retrieval engine
                </div>
                <Select
                  value={ragMode}
                  onChange={onRagModeChange}
                  options={[
                    { value: "auto", label: "Auto" },
                    { value: "vector", label: "Vector" },
                    { value: "hybrid", label: "Hybrid" },
                    { value: "multimodal", label: "Multimodal" },
                    { value: "hybrid_multimodal", label: "Hybrid multimodal" },
                  ]}
                  style={{ width: "100%" }}
                />
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  RAG mode
                </div>
                <Select
                  value={retrievalProfile}
                  onChange={onRetrievalProfileChange}
                  options={[
                    { value: "fast", label: "Fast — lowest latency" },
                    { value: "balanced", label: "Balanced — recommended" },
                    { value: "thorough", label: "Thorough — best recall" },
                  ]}
                  style={{ width: "100%" }}
                />
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  Memory mode
                </div>
                <Select
                  value={memoryMode}
                  onChange={onMemoryModeChange}
                  options={[
                    { value: "off", label: "Off — no long-term memory" },
                    { value: "focused", label: "Focused — low noise" },
                    { value: "balanced", label: "Balanced — recommended" },
                    { value: "deep", label: "Deep — multi-hop + graph" },
                  ]}
                  style={{ width: "100%" }}
                />
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  Web search
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Switch
                    checked={useWebSearch}
                    onChange={onUseWebSearchChange}
                    checkedChildren="On"
                    unCheckedChildren="Off"
                  />
                  <span style={{ fontSize: 13, color: "var(--color-text-muted)" }}>
                    <GlobalOutlined style={{ marginRight: 4 }} />
                    Include web results
                  </span>
                </div>
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  {formatMessage({ id: "chat.parameters.memorySnapshot" })}
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  <Input
                    type="datetime-local"
                    aria-label={formatMessage({ id: "chat.parameters.validAt" })}
                    value={validAt}
                    onChange={(e) => onValidAtChange(e.target.value)}
                    placeholder={formatMessage({ id: "chat.parameters.validAt" })}
                  />
                  <Input
                    type="datetime-local"
                    aria-label={formatMessage({ id: "chat.parameters.knownAt" })}
                    value={knownAt}
                    onChange={(e) => onKnownAtChange(e.target.value)}
                    placeholder={formatMessage({ id: "chat.parameters.knownAt" })}
                  />
                </div>
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  {formatMessage({ id: "chat.parameters.continuation" })}
                </div>
                <Select
                  value={continuationMode}
                  onChange={onContinuationModeChange}
                  options={[
                    {
                      value: "latest",
                      label: formatMessage({ id: "chat.parameters.continuationLatest" }),
                    },
                    {
                      value: "saved_scope",
                      label: formatMessage({ id: "chat.parameters.continuationScope" }),
                    },
                    {
                      value: "saved_snapshot",
                      label: formatMessage({ id: "chat.parameters.continuationSaved" }),
                    },
                  ]}
                  style={{ width: "100%" }}
                />
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  File type filter
                </div>
                <Select
                  mode="multiple"
                  allowClear
                  placeholder="Any"
                  value={selectedTypeFilters}
                  onChange={onTypeFiltersChange}
                  options={FILE_TYPE_OPTIONS}
                  style={{ width: "100%" }}
                  maxTagCount="responsive"
                />
              </div>

              <div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  Date range
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Input
                    type="date"
                    value={dateFrom}
                    onChange={(e) => onDateFromChange(e.target.value)}
                    style={{ flex: 1 }}
                  />
                  <span style={{ color: "var(--color-text-muted)" }}>to</span>
                  <Input
                    type="date"
                    value={dateTo}
                    onChange={(e) => onDateToChange(e.target.value)}
                    style={{ flex: 1 }}
                  />
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
