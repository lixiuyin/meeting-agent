import { Input, Button, Tooltip, Spin } from "antd";
import { SearchOutlined, ReloadOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";

interface SessionSearchProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  searchLoading: boolean;
  loading: boolean;
  onRefresh: () => void;
}

export default function SessionSearch({
  searchQuery,
  onSearchChange,
  searchLoading,
  loading,
  onRefresh,
}: SessionSearchProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.1 }}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 16px",
        background: "var(--color-bg-surface)",
        borderRadius: 16,
        border: "1px solid var(--color-border)",
        marginBottom: 20,
        flexWrap: "wrap",
        gap: 12,
      }}
    >
      <Input
        prefix={<SearchOutlined />}
        placeholder="Search across sessions..."
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        style={{ width: 280 }}
        allowClear
        variant="borderless"
        suffix={searchLoading ? <Spin size="small" /> : null}
      />

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Tooltip title="Refresh">
          <Button
            type="text"
            icon={<ReloadOutlined />}
            onClick={onRefresh}
            loading={loading}
            aria-label="Refresh sessions"
          />
        </Tooltip>
      </div>
    </motion.div>
  );
}
