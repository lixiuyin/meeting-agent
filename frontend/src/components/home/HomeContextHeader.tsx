import {
  PlusOutlined,
  VideoCameraOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  FileImageOutlined,
  AudioOutlined,
} from "@ant-design/icons";
import { Badge, Button, Select, Space, Tag } from "antd";
import type { SelectProps } from "antd";
import { AnimatePresence, motion } from "framer-motion";
import type { IntlShape } from "react-intl";
import type { ReactNode } from "react";

import type { MeetingInfo } from "../../api/client";

const TYPE_ICONS: Record<string, ReactNode> = {
  video: <VideoCameraOutlined style={{ color: "var(--ant-color-error)" }} />,
  pdf: <FilePdfOutlined style={{ color: "var(--ant-color-warning)" }} />,
  ppt: <FileTextOutlined style={{ color: "var(--ant-color-success)" }} />,
  image: <FileImageOutlined style={{ color: "var(--ant-color-info)" }} />,
  audio: <AudioOutlined style={{ color: "var(--ant-color-primary)" }} />,
};

interface SelectedFileChip {
  value: number;
  meetingTitle: string;
  fileName: string;
}

type SelectOptions = NonNullable<SelectProps["options"]>;

export default function HomeContextHeader({
  formatMessage,
  selectedMeetingIds,
  setSelectedMeetingIds,
  meetingOptions,
  loadingMeetings,
  selectedFileIds,
  setSelectedFileIds,
  fileOptions,
  loadingFiles,
  selectedMeetings,
  selectedFiles,
  removeSelectedMeeting,
  removeSelectedFile,
  streamSessionId,
  onNewSession,
}: {
  formatMessage: IntlShape["formatMessage"];
  selectedMeetingIds: number[];
  setSelectedMeetingIds: (ids: number[]) => void;
  meetingOptions: SelectOptions;
  loadingMeetings: boolean;
  selectedFileIds: number[];
  setSelectedFileIds: (ids: number[]) => void;
  fileOptions: SelectOptions;
  loadingFiles: boolean;
  selectedMeetings: MeetingInfo[];
  selectedFiles: SelectedFileChip[];
  removeSelectedMeeting: (id: number) => void;
  removeSelectedFile: (id: number) => void;
  streamSessionId: string | undefined;
  onNewSession: () => void;
}) {
  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        style={{
          padding: "8px 0 16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Select
            mode="multiple"
            placeholder={formatMessage({ id: "home.selectMeetings" })}
            value={selectedMeetingIds}
            onChange={setSelectedMeetingIds}
            options={meetingOptions}
            optionFilterProp="label"
            style={{ minWidth: 280, maxWidth: 400 }}
            allowClear
            maxTagCount="responsive"
            variant="borderless"
            loading={loadingMeetings}
          />
          <Select
            mode="multiple"
            placeholder="Select files (optional)"
            value={selectedFileIds}
            onChange={setSelectedFileIds}
            options={fileOptions}
            optionFilterProp="label"
            style={{ minWidth: 320, maxWidth: 460 }}
            allowClear
            maxTagCount="responsive"
            variant="borderless"
            disabled={selectedMeetingIds.length === 0}
            loading={loadingFiles}
          />
          {selectedMeetingIds.length > 0 && (
            <Badge
              count={selectedMeetingIds.length}
              style={{ backgroundColor: "var(--color-primary)" }}
            />
          )}
          {selectedFileIds.length > 0 && (
            <Badge count={selectedFileIds.length} style={{ backgroundColor: "#0ea5e9" }} />
          )}
        </div>

        <Space>
          {streamSessionId && (
            <Tag
              style={{
                borderRadius: 20,
                background: "rgba(16, 185, 129, 0.1)",
                borderColor: "rgba(16, 185, 129, 0.2)",
                color: "var(--color-success)",
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "var(--color-success)",
                  marginRight: 6,
                }}
              />
              {formatMessage({ id: "home.sessionActive" })}
            </Tag>
          )}
          <Button icon={<PlusOutlined />} onClick={onNewSession} style={{ borderRadius: 10 }}>
            {formatMessage({ id: "home.newChat" })}
          </Button>
        </Space>
      </motion.div>

      <AnimatePresence>
        {selectedMeetings.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
              marginBottom: 12,
              padding: "8px 12px",
              background: "var(--color-bg-muted)",
              borderRadius: 12,
            }}
          >
            <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
              {formatMessage({ id: "home.context" })}
            </span>
            {selectedMeetings.map((m) => (
              <Tag
                key={m.id}
                closable
                onClose={() => removeSelectedMeeting(m.id)}
                style={{
                  borderRadius: 20,
                  background: "var(--color-bg-surface)",
                  borderColor: "var(--color-border)",
                }}
              >
                {TYPE_ICONS[m.file_type ?? "txt"]} {m.title}
              </Tag>
            ))}
            {selectedFiles.map((file) => (
              <Tag
                key={file.value}
                closable
                onClose={() => removeSelectedFile(file.value)}
                style={{
                  borderRadius: 20,
                  background: "var(--color-bg-surface)",
                  borderColor: "var(--color-border)",
                }}
              >
                <FileTextOutlined /> {file.meetingTitle} / {file.fileName}
              </Tag>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
