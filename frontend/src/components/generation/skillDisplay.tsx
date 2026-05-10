import type React from "react";
import {
  ClockCircleOutlined,
  ReadOutlined,
  LinkOutlined,
  CheckSquareOutlined,
  AlertOutlined,
  TeamOutlined,
  VideoCameraOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  FileImageOutlined,
} from "@ant-design/icons";
import type { MeetingInfo, SkillItem } from "../../api/client";
import { getKindCapabilities } from "../../types/fileKinds";

export const SKILL_ICONS: Record<string, React.ReactNode> = {
  transcript: <ClockCircleOutlined />,
  summary: <ReadOutlined />,
  references: <LinkOutlined />,
  meeting_minutes_generator: <FileTextOutlined />,
  action_items_tracker: <CheckSquareOutlined />,
  risk_register_generator: <AlertOutlined />,
  stakeholder_update_generator: <TeamOutlined />,
};

const SKILL_TEXT_OVERRIDES: Record<string, Pick<SkillItem, "display_name" | "description">> = {
  tech_proposal_generator: {
    display_name: "MOST (PRC) Technical Proposal Generator",
    description:
      "Organize meeting content into a technical proposal aligned with the Ministry of Science and Technology of the People's Republic of China (MOST, PRC).",
  },
  meeting_minutes_generator: {
    display_name: "Meeting Minutes Generator",
    description:
      "Convert meeting discussions into structured minutes with agenda, decisions, action items, owners, and deadlines.",
  },
  action_items_tracker: {
    display_name: "Action Items Tracker",
    description:
      "Extract and organize follow-up tasks into an accountability tracker with owners, priorities, and due dates.",
  },
  risk_register_generator: {
    display_name: "Risk Register Generator",
    description:
      "Build a structured risk register from meeting content, including impact, likelihood, mitigation, and ownership.",
  },
  stakeholder_update_generator: {
    display_name: "Stakeholder Update Generator",
    description:
      "Create concise stakeholder-facing updates covering status, progress, risks, and next-step asks.",
  },
};

export interface CreateSkillFormValues {
  name: string;
  displayName: string;
  description: string;
  requiredKeywords: string;
  optionalKeywords: string;
  examples: string;
}

export function normalizeSkillName(rawName: string): string {
  const trimmed = rawName.trim();
  return trimmed.endsWith("_generator") ? trimmed : `${trimmed}_generator`;
}

export function normalizeSkills(skills: SkillItem[]): SkillItem[] {
  return skills.map((skill) => ({
    ...skill,
    ...SKILL_TEXT_OVERRIDES[skill.name],
  }));
}

export function iconForFileType(fileType: MeetingInfo["file_type"]): React.ReactNode {
  const hint = getKindCapabilities(fileType).viewerHint;
  if (hint === "video") return <VideoCameraOutlined style={{ color: "#f43f5e" }} />;
  if (hint === "pdf") return <FilePdfOutlined style={{ color: "#f59e0b" }} />;
  if (hint === "slides" || hint === "text")
    return <FileTextOutlined style={{ color: "#10b981" }} />;
  if (hint === "image") return <FileImageOutlined style={{ color: "#3b82f6" }} />;
  return <FileTextOutlined />;
}
