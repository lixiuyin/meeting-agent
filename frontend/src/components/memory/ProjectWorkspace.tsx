import { useSearchParams } from "react-router-dom";
import { Alert, Button, Collapse, Empty, Select, Space, Tabs } from "antd";
import { useIntl } from "react-intl";
import { useProjects } from "./useProjects";
import ProjectDirectoryPanel from "./ProjectDirectoryPanel";
import RecordedFactsPanel from "./RecordedFactsPanel";
import MeetingReviewPanel from "./MeetingReviewPanel";
import FactChangesPanel from "./FactChangesPanel";
import MeetingPreparationPanel from "./MeetingPreparationPanel";

export default function ProjectWorkspace() {
  const { formatMessage: t } = useIntl();
  const { projects, error, refresh } = useProjects();
  const [search, setSearch] = useSearchParams();
  const project = search.get("project") || undefined;
  const tab = search.get("projectTab") || "actions";
  const updateSearch = (key: string, value: string) =>
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      next.set(key, value);
      return next;
    });
  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="middle">
      <Alert type="info" title={t({ id: "workflow.projectHint" })} />
      {error && (
        <Alert
          type="error"
          title={error}
          action={<Button onClick={refresh}>{t({ id: "workflow.refresh" })}</Button>}
        />
      )}
      <Select
        aria-label={t({ id: "workflow.overview" })}
        placeholder={t({ id: "workflow.overview" })}
        style={{ width: 300, maxWidth: "100%" }}
        value={project}
        onChange={(value) => updateSearch("project", value)}
        options={projects.map((p) => ({ value: p.project_id, label: p.name }))}
      />
      {!project && <Empty description={t({ id: "workflow.selectProject" })} />}
      {project && (
        <Tabs
          key={project}
          activeKey={tab}
          onChange={(value) => updateSearch("projectTab", value)}
          items={[
            {
              key: "preparation",
              label: t({ id: "workflow.preparation" }),
              children: <MeetingPreparationPanel projectId={project} />,
            },
            {
              key: "actions",
              label: t({ id: "memory.facts.tab" }),
              children: <RecordedFactsPanel projectId={project} />,
            },
            {
              key: "changes",
              label: t({ id: "memory.changes.tab" }),
              children: <FactChangesPanel projectId={project} />,
            },
            {
              key: "review",
              label: t({ id: "workflow.review" }),
              children: <MeetingReviewPanel projectId={project} />,
            },
          ]}
        />
      )}
      <Collapse
        items={[
          {
            key: "directory",
            label: t({ id: "workflow.manageProjects" }),
            children: <ProjectDirectoryPanel onSaved={refresh} />,
          },
        ]}
      />
    </Space>
  );
}
