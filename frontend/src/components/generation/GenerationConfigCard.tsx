import type { ReactNode } from "react";
import { Button, Card, Input, Radio, Select, Spin, Tag } from "antd";
import { PlusOutlined, ReadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { formatLocalTime } from "../../utils/time";
import type { SkillItem } from "../../api/client";
import type { MeetingGroup } from "./types";

interface GenerationConfigCardProps {
  loadingSkills: boolean;
  skills: SkillItem[];
  selectedSkillName?: string;
  selectedSkill?: SkillItem;
  onSelectSkill: (value: string) => void;
  onOpenCreateModal: () => void;
  skillIcons: Record<string, ReactNode>;
  meetingGroups: MeetingGroup[];
  selectedTitle?: string;
  onSelectMeeting: (value: string | undefined) => void;
  loadingMeetings: boolean;
  selectedGroup?: MeetingGroup;
  iconForFileType: (fileType: MeetingGroup["files"][number]["file_type"]) => ReactNode;
  extraInstructions: string;
  onChangeExtraInstructions: (value: string) => void;
  onGenerate: () => void;
  isLoading: boolean;
}

export function GenerationConfigCard({
  loadingSkills,
  skills,
  selectedSkillName,
  selectedSkill,
  onSelectSkill,
  onOpenCreateModal,
  skillIcons,
  meetingGroups,
  selectedTitle,
  onSelectMeeting,
  loadingMeetings,
  selectedGroup,
  iconForFileType,
  extraInstructions,
  onChangeExtraInstructions,
  onGenerate,
  isLoading,
}: GenerationConfigCardProps) {
  const { formatMessage } = useIntl();

  const groupOptions = meetingGroups.map((g) => ({
    value: g.title,
    label: (
      <span>
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 12,
            background: "var(--color-bg-muted)",
            fontSize: 11,
            fontWeight: 600,
            marginRight: 8,
            color: "var(--color-primary)",
          }}
        >
          {formatMessage({ id: "generation.config.meetingNumber" }, { number: g.number })}
        </span>
        {g.title}
        <span style={{ color: "var(--color-text-muted)", marginLeft: 8, fontSize: 12 }}>
          {formatLocalTime(g.earliestCreatedAt, { dateOnly: true })}
        </span>
      </span>
    ),
  }));

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.1 }}
    >
      <Card
        style={{
          borderRadius: 16,
          background: "var(--color-bg-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <div
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "var(--color-text-secondary)",
              }}
            >
              {formatMessage({ id: "generation.config.skill" })}
            </div>
            <Button type="text" size="small" icon={<PlusOutlined />} onClick={onOpenCreateModal}>
              {formatMessage({ id: "generation.config.create" })}
            </Button>
          </div>
          <Radio.Group
            value={selectedSkillName}
            onChange={(e) => onSelectSkill(e.target.value)}
            style={{ display: "flex", flexDirection: "column", gap: 10 }}
          >
            {loadingSkills && (
              <div style={{ textAlign: "center", padding: 24 }}>
                <Spin />
              </div>
            )}
            {!loadingSkills && skills.length === 0 && (
              <div style={{ color: "var(--color-text-muted)" }}>
                {formatMessage({ id: "generation.config.noSkills" })}
              </div>
            )}
            {skills.map((skill) => (
              <Radio key={skill.name} value={skill.name}>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {skillIcons[skill.name] || <ReadOutlined />}
                  {skill.display_name}
                </span>
              </Radio>
            ))}
          </Radio.Group>
          {selectedSkill && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--color-text-muted)" }}>
              {selectedSkill.description}
            </div>
          )}
        </div>

        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--color-text-secondary)",
              marginBottom: 8,
            }}
          >
            {formatMessage({ id: "generation.config.selectMeeting" })}
          </div>
          <Select
            placeholder={formatMessage({ id: "generation.config.chooseMeeting" })}
            value={selectedTitle}
            onChange={onSelectMeeting}
            options={groupOptions}
            style={{ width: "100%" }}
            loading={loadingMeetings}
            allowClear
          />
          {selectedGroup && (
            <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
              {selectedGroup.files.map((f) => (
                <Tag
                  key={f.id}
                  style={{
                    borderRadius: 20,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  {iconForFileType(f.file_type)}
                  {f.file_name}
                </Tag>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--color-text-secondary)",
              marginBottom: 8,
            }}
          >
            {formatMessage({ id: "generation.config.additionalInstructions" })}
          </div>
          <Input.TextArea
            rows={3}
            placeholder={formatMessage({ id: "generation.config.instructionsPlaceholder" })}
            value={extraInstructions}
            onChange={(e) => onChangeExtraInstructions(e.target.value)}
            style={{ borderRadius: 10 }}
          />
        </div>

        <Button
          type="primary"
          size="large"
          block
          icon={<ThunderboltOutlined />}
          loading={isLoading}
          disabled={!selectedSkill || !selectedGroup}
          onClick={onGenerate}
          style={{
            borderRadius: 12,
            background: "var(--gradient-primary)",
            border: "none",
            height: 44,
          }}
        >
          {formatMessage({ id: "generation.config.generate" })}
        </Button>
      </Card>
    </motion.div>
  );
}
