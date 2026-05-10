import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { message } from "antd";
import { useIntl } from "react-intl";
import {
  createSkill,
  formatApiErrorMessage,
  invokeSkill,
  listMeetings,
  listSkills,
  type MeetingInfo,
  type SkillItem,
} from "../api/client";
import { CreateSkillModal } from "../components/generation/CreateSkillModal";
import { GenerationConfigCard } from "../components/generation/GenerationConfigCard";
import { GenerationOutputCard } from "../components/generation/GenerationOutputCard";
import {
  iconForFileType,
  normalizeSkillName,
  normalizeSkills,
  SKILL_ICONS,
  type CreateSkillFormValues,
} from "../components/generation/skillDisplay";
import type { MeetingGroup } from "../components/generation/types";
import { reportNonCriticalError } from "../utils/monitoring";

export default function GenerationPage() {
  const { formatMessage } = useIntl();
  const [meetings, setMeetings] = useState<MeetingInfo[]>([]);
  const [loadingMeetings, setLoadingMeetings] = useState(false);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loadingSkills, setLoadingSkills] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [creatingSkill, setCreatingSkill] = useState(false);
  const [selectedSkillName, setSelectedSkillName] = useState<string | undefined>();
  const [selectedTitle, setSelectedTitle] = useState<string | undefined>();
  const [extraInstructions, setExtraInstructions] = useState("");
  const [result, setResult] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetOutput = useCallback(() => {
    setResult("");
    setError(null);
    setCopied(false);
  }, []);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setLoadingMeetings(true);
      listMeetings({ limit: 100, status: "ready" })
        .then((res) => setMeetings(res.data.meetings))
        .catch((err) =>
          message.error(
            formatApiErrorMessage(err, formatMessage({ id: "generation.loadMeetingsFailed" })),
          ),
        )
        .finally(() => setLoadingMeetings(false));
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [formatMessage]);

  const loadSkills = useCallback(async () => {
    setLoadingSkills(true);
    try {
      const res = await listSkills();
      const normalized = normalizeSkills(res.data.skills);
      setSkills(normalized);
      if (normalized.length > 0) {
        setSelectedSkillName((current) =>
          current && normalized.some((s) => s.name === current) ? current : normalized[0].name,
        );
      }
    } catch (err) {
      message.error(
        formatApiErrorMessage(err, formatMessage({ id: "generation.loadSkillsFailed" })),
      );
    } finally {
      setLoadingSkills(false);
    }
  }, [formatMessage]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadSkills();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadSkills]);

  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.name === selectedSkillName),
    [skills, selectedSkillName],
  );

  const meetingGroups = useMemo<MeetingGroup[]>(() => {
    const grouped = new Map<string, MeetingInfo[]>();
    meetings.forEach((meeting) => {
      const list = grouped.get(meeting.title) || [];
      list.push(meeting);
      grouped.set(meeting.title, list);
    });
    const groups = Array.from(grouped.entries()).map(([, files]) => {
      const sortedFiles = [...files].sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
      return {
        title: sortedFiles[0].title,
        files: sortedFiles,
        earliestCreatedAt: sortedFiles[0].created_at,
        ids: sortedFiles.map((f) => f.id),
        number: 0,
      };
    });
    groups.sort(
      (a, b) => new Date(a.earliestCreatedAt).getTime() - new Date(b.earliestCreatedAt).getTime(),
    );
    return groups.map((group, index) => ({ ...group, number: index + 1 }));
  }, [meetings]);

  const selectedGroup = useMemo(
    () => meetingGroups.find((group) => group.title === selectedTitle),
    [meetingGroups, selectedTitle],
  );

  const handleGenerate = async () => {
    if (!selectedSkill) {
      message.warning(formatMessage({ id: "generation.selectSkill" }));
      return;
    }
    if (!selectedGroup) {
      message.warning(formatMessage({ id: "generation.selectMeeting" }));
      return;
    }

    setIsLoading(true);
    setResult("");
    setError(null);

    try {
      const query = extraInstructions.trim() || selectedSkill.description;
      const res = await invokeSkill({
        skill_name: selectedSkill.name,
        query,
        meeting_ids: selectedGroup.ids,
      });
      setResult(res.data.content);
    } catch (err) {
      const msg = formatApiErrorMessage(err, formatMessage({ id: "generation.failed" }));
      setError(msg);
      message.error(msg);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(result);
      setCopied(true);
      message.success(formatMessage({ id: "generation.copied" }));
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      reportNonCriticalError("copy generation result", err);
      message.error(formatMessage({ id: "generation.copyFailed" }));
    }
  };

  const handleDownload = () => {
    const blob = new Blob([result], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedSkill?.name || "skill"}-${selectedGroup?.title || "meeting"}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const parseCommaSeparatedValues = (value: string): string[] =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  const parseLineSeparatedValues = (value: string): string[] =>
    value
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);

  const handleSelectSkill = useCallback(
    (value?: string) => {
      setSelectedSkillName(value);
      resetOutput();
    },
    [resetOutput],
  );

  const handleSelectTitle = useCallback(
    (value?: string) => {
      setSelectedTitle(value);
      resetOutput();
    },
    [resetOutput],
  );

  const handleCreateSkill = async (values: CreateSkillFormValues) => {
    try {
      setCreatingSkill(true);
      const normalizedName = normalizeSkillName(values.name);
      await createSkill({
        name: normalizedName,
        display_name: values.displayName.trim(),
        description: values.description.trim(),
        required_keywords: parseCommaSeparatedValues(values.requiredKeywords),
        optional_keywords: parseCommaSeparatedValues(values.optionalKeywords),
        examples: parseLineSeparatedValues(values.examples),
        category: "custom",
      });
      await loadSkills();
      setSelectedSkillName(normalizedName);
      setIsCreateModalOpen(false);
      message.success(formatMessage({ id: "generation.skillCreated" }));
    } catch (err) {
      message.error(
        formatApiErrorMessage(err, formatMessage({ id: "generation.createSkillFailed" })),
      );
      throw err;
    } finally {
      setCreatingSkill(false);
    }
  };

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "12px 16px 24px" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 20,
          alignItems: "start",
        }}
      >
        <GenerationConfigCard
          loadingSkills={loadingSkills}
          skills={skills}
          selectedSkillName={selectedSkillName}
          selectedSkill={selectedSkill}
          onSelectSkill={handleSelectSkill}
          onOpenCreateModal={() => setIsCreateModalOpen(true)}
          skillIcons={SKILL_ICONS}
          meetingGroups={meetingGroups}
          selectedTitle={selectedTitle}
          onSelectMeeting={handleSelectTitle}
          loadingMeetings={loadingMeetings}
          selectedGroup={selectedGroup}
          iconForFileType={iconForFileType}
          extraInstructions={extraInstructions}
          onChangeExtraInstructions={setExtraInstructions}
          onGenerate={() => void handleGenerate()}
          isLoading={isLoading}
        />
        <GenerationOutputCard
          result={result}
          isLoading={isLoading}
          error={error}
          copied={copied}
          selectedSkillDisplayName={selectedSkill?.display_name}
          onCopy={handleCopy}
          onDownload={handleDownload}
        />
      </div>

      <CreateSkillModal
        open={isCreateModalOpen}
        confirmLoading={creatingSkill}
        onCancel={() => setIsCreateModalOpen(false)}
        onSubmit={handleCreateSkill}
      />
    </div>
  );
}
