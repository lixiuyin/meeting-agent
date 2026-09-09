import { useEffect, useState } from "react";
import { Alert, Button, Empty, Input, Space, Spin, Tag, Typography } from "antd";
import { useIntl } from "react-intl";
import { useEvidenceViewer } from "../../hooks/useEvidenceViewer";
import { compareRecordedFacts, formatApiErrorMessage, isRequestCanceled } from "../../api/client";
import { getEvidenceTarget } from "../../utils/evidenceNavigation";
import { MaterialSelect, ProjectSelect } from "./ProjectFields";

const localDateTime = (date: Date) =>
  new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

export default function FactChangesPanel({
  projectId,
  initialWeek = false,
}: { projectId?: string; initialWeek?: boolean } = {}) {
  const { formatMessage: t } = useIntl();
  const openEvidence = useEvidenceViewer();
  const [showFilters, setShowFilters] = useState(!initialWeek);
  const [initialRange] = useState(() => {
    const end = new Date();
    return { start: new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000), end };
  });
  const [before, setBefore] = useState(initialWeek ? localDateTime(initialRange.start) : "");
  const [after, setAfter] = useState(initialWeek ? localDateTime(initialRange.end) : "");
  const [knownAt, setKnownAt] = useState("");
  const [project, setProject] = useState(projectId ?? "");
  const [files, setFiles] = useState<number[]>([]);
  const [request, setRequest] = useState<Parameters<typeof compareRecordedFacts>[0] | null>(
    initialWeek
      ? {
          query: "",
          overdue: false,
          before: initialRange.start.toISOString(),
          after: initialRange.end.toISOString(),
          project_id: projectId,
          limit: 25,
          offset: 0,
        }
      : null,
  );
  const [result, setResult] = useState<
    Awaited<ReturnType<typeof compareRecordedFacts>>["data"] | null
  >(null);
  const [error, setError] = useState("");
  const [settledRequest, setSettledRequest] = useState<typeof request>(null);
  const loading = settledRequest !== request;
  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    compareRecordedFacts(request, { signal: controller.signal })
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
  }, [request]);
  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon title={t({ id: "memory.changes.coverage" })} />
      <Button
        onClick={() => {
          const end = new Date();
          const start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
          setBefore(localDateTime(start));
          setAfter(localDateTime(end));
          setKnownAt("");
          setRequest({
            query: "",
            overdue: false,
            before: start.toISOString(),
            after: end.toISOString(),
            project_id: project || undefined,
            file_ids: files.length ? files : undefined,
            limit: 25,
            offset: 0,
          });
        }}
      >
        {t({ id: "workflow.preset.week" })}
      </Button>
      {initialWeek && (
        <Button onClick={() => setShowFilters((value) => !value)} aria-expanded={showFilters}>
          {t({ id: showFilters ? "workflow.hideFilters" : "workflow.showFilters" })}
        </Button>
      )}
      {showFilters && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (new Date(before) >= new Date(after)) {
              setError(t({ id: "memory.changes.order" }));
              return;
            }
            setRequest({
              query: "",
              overdue: false,
              before: new Date(before).toISOString(),
              after: new Date(after).toISOString(),
              known_at: knownAt ? new Date(knownAt).toISOString() : undefined,
              project_id: project || undefined,
              file_ids: files.length ? files : undefined,
              limit: 25,
              offset: 0,
            });
          }}
        >
          <Space wrap align="end">
            {(
              [
                ["before", before, setBefore],
                ["after", after, setAfter],
                ["knownAt", knownAt, setKnownAt],
              ] as const
            ).map(([name, value, setter]) => (
              <label key={name}>
                {t({ id: `memory.changes.${name}` })}
                <Input
                  type="datetime-local"
                  required={name !== "knownAt"}
                  value={value}
                  onChange={(e) => setter(e.target.value)}
                />
              </label>
            ))}
            {!projectId && <ProjectSelect value={project} onChange={setProject} />}
            <MaterialSelect value={files} onChange={setFiles} />
            <Button htmlType="submit" loading={loading}>
              {t({ id: "memory.changes.compare" })}
            </Button>
          </Space>
        </form>
      )}
      {!loading && error && <Alert type="error" title={error} />}
      <Spin spinning={loading}>
        {!loading && result && (
          <>
            <Typography.Paragraph role="status">
              {t({ id: "memory.changes.total" }, { count: result.total })}
            </Typography.Paragraph>
            {!result.items.length && <Empty />}
            <div style={{ maxHeight: "55vh", overflow: "auto" }}>
              {result.items.map((change) => (
                <article
                  key={change.key}
                  style={{
                    padding: "16px 0",
                    borderBottom: "1px solid var(--color-border)",
                    overflowWrap: "anywhere",
                  }}
                >
                  <Typography.Text strong>{change.key}</Typography.Text>{" "}
                  <Tag>{t({ id: `memory.changes.${change.kind}` })}</Tag>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(min(240px, 100%), 1fr))",
                      gap: 16,
                    }}
                  >
                    {(["before", "after"] as const).map((side) => {
                      const fact = change[side];
                      return (
                        <section key={side}>
                          <Typography.Text type="secondary">
                            {t({ id: `memory.changes.${side}` })}
                          </Typography.Text>
                          <p>{fact?.value ?? "—"}</p>
                          {fact && (
                            <Space wrap>
                              <Tag>{fact.action_status || fact.fact_type}</Tag>
                              <span>{fact.assignee}</span>
                              <time>{fact.due_at}</time>
                            </Space>
                          )}
                          {fact?.evidence_excerpt && <p>{fact.evidence_excerpt}</p>}
                          {(fact?.evidence_refs || []).map((ref, index) => {
                            const target = getEvidenceTarget(ref, fact?.meeting_ids);
                            return (
                              target && (
                                <Button
                                  key={index}
                                  type="link"
                                  onClick={() =>
                                    openEvidence(ref, fact?.meeting_ids, fact?.evidence_excerpt)
                                  }
                                >
                                  {t({ id: "memory.facts.source" }, { index: index + 1 })}
                                </Button>
                              )
                            );
                          })}
                        </section>
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
            <Space style={{ marginTop: 12 }}>
              <Button
                disabled={!request?.offset || loading}
                onClick={() =>
                  request &&
                  setRequest({
                    ...request,
                    offset: Math.max(0, (request.offset || 0) - 25),
                    snapshot: result.snapshot,
                  })
                }
              >
                {t({ id: "memory.facts.previous" })}
              </Button>
              <Button
                disabled={result.next_offset == null || loading}
                onClick={() =>
                  request &&
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
