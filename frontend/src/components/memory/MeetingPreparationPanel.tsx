import { Alert, Collapse, Space } from "antd";
import { useIntl } from "react-intl";
import RecordedFactsPanel from "./RecordedFactsPanel";
import FactChangesPanel from "./FactChangesPanel";
import MeetingReviewPanel from "./MeetingReviewPanel";

export default function MeetingPreparationPanel({ projectId }: { projectId: string }) {
  const { formatMessage: t } = useIntl();
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t({ id: "workflow.preparationHint" })} />
      <Collapse
        defaultActiveKey={["actions"]}
        items={[
          {
            key: "actions",
            label: t({ id: "workflow.preset.unfinished" }),
            children: <RecordedFactsPanel projectId={projectId} initialUnfinished />,
          },
          {
            key: "changes",
            label: t({ id: "workflow.preset.week" }),
            children: <FactChangesPanel projectId={projectId} initialWeek />,
          },
          {
            key: "review",
            label: t({ id: "workflow.review" }),
            children: <MeetingReviewPanel projectId={projectId} />,
          },
        ]}
      />
    </Space>
  );
}
