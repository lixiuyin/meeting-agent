import { Button, InputNumber, Switch, Select, Input } from "antd";
import { SettingOutlined, GlobalOutlined } from "@ant-design/icons";
import { AnimatePresence, motion } from "framer-motion";

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
  topK: number;
  onTopKChange: (v: number) => void;
  useWebSearch: boolean;
  onUseWebSearchChange: (v: boolean) => void;
  selectedTypeFilters: string[];
  onTypeFiltersChange: (v: string[]) => void;
  dateFrom: string;
  onDateFromChange: (v: string) => void;
  dateTo: string;
  onDateToChange: (v: string) => void;
  ragMode: "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto";
  onRagModeChange: (v: "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto") => void;
  activeParamCount: number;
}

export default function ChatParameters({
  expanded,
  onToggle,
  topK,
  onTopKChange,
  useWebSearch,
  onUseWebSearchChange,
  selectedTypeFilters,
  onTypeFiltersChange,
  dateFrom,
  onDateFromChange,
  dateTo,
  onDateToChange,
  ragMode,
  onRagModeChange,
  activeParamCount,
}: ChatParametersProps) {
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
        Parameters
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
                  Retrieval mode
                </div>
                <Select
                  value={ragMode}
                  onChange={onRagModeChange}
                  options={[
                    { value: "auto", label: "Auto" },
                    { value: "native", label: "Native" },
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
                  Top K retrievals
                </div>
                <InputNumber
                  min={1}
                  max={50}
                  value={topK}
                  onChange={(v) => onTopKChange(typeof v === "number" ? v : 5)}
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
