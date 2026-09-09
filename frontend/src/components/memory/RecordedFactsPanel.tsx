import { useEffect, useState } from "react";
import { Alert, Button, Empty, Input, Select, Space, Spin, Tag, Typography } from "antd";
import { useIntl } from "react-intl";
import { useEvidenceViewer } from "../../hooks/useEvidenceViewer";
import { queryRecordedFacts, formatApiErrorMessage, isRequestCanceled } from "../../api/client";
import { getEvidenceTarget } from "../../utils/evidenceNavigation";
import FactEditor from "./FactEditor";
import type { MemoryItem } from "../../api/client-memory";
import { MaterialSelect, ProjectSelect } from "./ProjectFields";

type Result = Awaited<ReturnType<typeof queryRecordedFacts>>["data"];

export default function RecordedFactsPanel({
  projectId,
  meetingId,
  initialUnfinished = false,
}: { projectId?: string; meetingId?: number; initialUnfinished?: boolean } = {}) {
  const { formatMessage: t } = useIntl();
  const openEvidence = useEvidenceViewer();
  const [query, setQuery] = useState("");
  const [project, setProject] = useState(projectId ?? "");
  const [assignee, setAssignee] = useState("");
  const [statuses, setStatuses] = useState<string[]>(
    initialUnfinished ? ["open", "in_progress", "blocked"] : [],
  );
  const [overdue, setOverdue] = useState(false);
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [showFilters, setShowFilters] = useState(!initialUnfinished);
  const [files, setFiles] = useState<number[]>([]);
  const [validAt, setValidAt] = useState("");
  const [knownAt, setKnownAt] = useState("");
  const [kind, setKind] = useState<"decision" | "action_item" | "project_fact">("action_item");
  const [request, setRequest] = useState({
    query: "",
    project: projectId ?? "",
    assignee,
    statuses,
    overdue,
    files,
    validAt,
    knownAt,
    kind,
    offset: 0,
    snapshot: undefined as string | undefined,
    serial: 0,
  });
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [settledRequest, setSettledRequest] = useState<typeof request | null>(null);
  const loading = settledRequest !== request;
  useEffect(() => {
    const controller = new AbortController();
    queryRecordedFacts(
      {
        query: request.query,
        project_id: request.project || undefined,
        meeting_ids: meetingId ? [meetingId] : undefined,
        assignee: request.assignee || undefined,
        action_status: request.statuses as (
          | "open"
          | "in_progress"
          | "blocked"
          | "done"
          | "cancelled"
        )[],
        overdue: request.overdue,
        file_ids: request.files.length ? request.files : undefined,
        valid_at: request.validAt ? new Date(request.validAt).toISOString() : undefined,
        known_at: request.knownAt ? new Date(request.knownAt).toISOString() : undefined,
        fact_types: [request.kind],
        limit: 25,
        offset: request.offset,
        snapshot: request.snapshot,
      },
      { signal: controller.signal },
    )
      .then(({ data }) => {
        if (!controller.signal.aborted) {
          setResult(data);
          setError("");
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted && !isRequestCanceled(err)) {
          setError(formatApiErrorMessage(err));
          setResult(null);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setSettledRequest(request);
      });
    return () => controller.abort();
  }, [request, meetingId]);
  const refresh = () =>
    setRequest({
      query,
      project,
      assignee,
      statuses,
      overdue,
      files,
      validAt,
      knownAt,
      kind,
      offset: 0,
      snapshot: undefined,
      serial: request.serial + 1,
    });
  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t({ id: "memory.facts.coverage" })} />
      <Space wrap>
        {(["unfinished", "overdue"] as const).map((preset) => (
          <Button
            key={preset}
            onClick={() => {
              const nextStatuses = ["open", "in_progress", "blocked"];
              setKind("action_item");
              setStatuses(nextStatuses);
              setOverdue(preset === "overdue");
              setRequest({
                query,
                project,
                assignee,
                statuses: nextStatuses,
                overdue: preset === "overdue",
                files,
                validAt,
                knownAt,
                kind: "action_item",
                offset: 0,
                snapshot: undefined,
                serial: request.serial + 1,
              });
            }}
          >
            {t({ id: `workflow.preset.${preset}` })}
          </Button>
        ))}
      </Space>
      {initialUnfinished && (
        <Button onClick={() => setShowFilters((value) => !value)} aria-expanded={showFilters}>
          {t({ id: showFilters ? "workflow.hideFilters" : "workflow.showFilters" })}
        </Button>
      )}
      {showFilters && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            refresh();
          }}
        >
          <Space wrap>
            <Input
              aria-label={t({ id: "memory.facts.query" })}
              placeholder={t({ id: "memory.facts.query" })}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              style={{ width: 260, maxWidth: "100%" }}
            />
            {!projectId && <ProjectSelect value={project} onChange={setProject} />}
            <Input
              aria-label={t({ id: "memory.form.assignee" })}
              placeholder={t({ id: "memory.form.assignee" })}
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              style={{ width: 180 }}
            />
            <Select
              mode="multiple"
              aria-label={t({ id: "workflow.status" })}
              placeholder={t({ id: "workflow.status" })}
              value={statuses}
              onChange={setStatuses}
              style={{ minWidth: 200 }}
              options={["open", "in_progress", "blocked", "done", "cancelled"].map((value) => ({
                value,
                label: t({ id: `workflow.${value}` }),
              }))}
            />
            <label>
              <input
                type="checkbox"
                aria-label={t({ id: "workflow.overdue" })}
                checked={overdue}
                onChange={(e) => setOverdue(e.target.checked)}
              />{" "}
              {t({ id: "workflow.overdue" })}
            </label>
            <MaterialSelect value={files} onChange={setFiles} />
            <label>
              {t({ id: "memory.facts.validAt" })}
              <Input
                type="datetime-local"
                value={validAt}
                onChange={(e) => setValidAt(e.target.value)}
              />
            </label>
            <label>
              {t({ id: "memory.changes.knownAt" })}
              <Input
                type="datetime-local"
                value={knownAt}
                onChange={(e) => setKnownAt(e.target.value)}
              />
            </label>
            <Select
              aria-label={t({ id: "memory.facts.type" })}
              value={kind}
              onChange={setKind}
              style={{ width: 180 }}
              options={(["action_item", "decision", "project_fact"] as const).map((value) => ({
                value,
                label: t({ id: `memory.facts.${value}` }),
              }))}
            />
            <Button htmlType="submit" loading={loading}>
              {t({ id: "memory.facts.apply" })}
            </Button>
          </Space>
        </form>
      )}
      {!loading && error && (
        <Alert
          type="error"
          showIcon
          title={error}
          action={<Button onClick={refresh}>{t({ id: "memory.facts.apply" })}</Button>}
        />
      )}
      {editing && (
        <FactEditor
          key={editing.key}
          fact={editing}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
      <Spin spinning={loading}>
        {!loading && result && (
          <>
            <Typography.Paragraph role="status">
              {t(
                { id: "memory.facts.count" },
                { count: result.total, returned: result.returned, offset: request.offset },
              )}
            </Typography.Paragraph>
            {!result.items.length && <Empty />}
            <div role="list" style={{ maxHeight: "55vh", overflow: "auto" }}>
              {result.items.map((fact) => (
                <article
                  key={fact.key}
                  role="listitem"
                  style={{
                    padding: "16px 0",
                    borderBottom: "1px solid var(--color-border)",
                    overflowWrap: "anywhere",
                  }}
                >
                  <Typography.Paragraph strong style={{ marginBottom: 8 }}>
                    {fact.value}
                  </Typography.Paragraph>
                  <Space wrap>
                    <Tag>{fact.action_status || fact.fact_type}</Tag>
                    {fact.project_id && <Tag>{fact.project_id}</Tag>}
                    {fact.assignee && <span>{fact.assignee}</span>}
                    {fact.due_at && <time dateTime={fact.due_at}>{fact.due_at}</time>}
                  </Space>
                  {fact.evidence_excerpt && (
                    <Typography.Paragraph type="secondary">
                      {fact.evidence_excerpt}
                    </Typography.Paragraph>
                  )}
                  {!request.validAt && !request.knownAt && (
                    <Button onClick={() => setEditing(fact)}>{t({ id: "workflow.edit" })}</Button>
                  )}
                  {fact.archived_at && <Tag>{t({ id: "workflow.archived" })}</Tag>}
                  {(fact.evidence_refs || []).map((ref, index) => {
                    const target = getEvidenceTarget(ref, fact.meeting_ids);
                    return (
                      target && (
                        <Button
                          key={index}
                          type="link"
                          onClick={() => openEvidence(ref, fact.meeting_ids, fact.evidence_excerpt)}
                        >
                          {t({ id: "memory.facts.source" }, { index: index + 1 })}
                        </Button>
                      )
                    );
                  })}
                </article>
              ))}
            </div>
            <Space style={{ marginTop: 12 }}>
              <Button
                disabled={!request.offset || loading}
                onClick={() =>
                  setRequest({
                    ...request,
                    offset: Math.max(0, request.offset - 25),
                    snapshot: result.snapshot,
                  })
                }
              >
                {t({ id: "memory.facts.previous" })}
              </Button>
              <Button
                disabled={result.next_offset === null || loading}
                onClick={() =>
                  setRequest({
                    ...request,
                    offset: result.next_offset ?? 0,
                    snapshot: result.snapshot,
                  })
                }
              >
                {t({ id: "memory.facts.next" })}
              </Button>
            </Space>
          </>
        )}
      </Spin>
    </Space>
  );
}
