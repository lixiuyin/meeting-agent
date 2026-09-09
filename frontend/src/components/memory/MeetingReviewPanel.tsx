import { useEffect, useState } from "react";
import { Alert, Button, Card, Dropdown, Empty, Space, Spin, Tag, Typography } from "antd";
import { LinkOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";
import ReactMarkdown from "react-markdown";
import { ApiError, api, formatApiErrorMessage, isRequestCanceled } from "../../api/client-core";
import {
  updateMemoryStatus,
  resolveMemoryConflict,
  type MemoryItem,
} from "../../api/client-memory";
import type { components } from "../../api/generated";
import { useEvidenceViewer } from "../../hooks/useEvidenceViewer";
import { normalizeLatexMathDelimiters, rehypePlugins, remarkPlugins } from "../../utils/markdown";
import FactEditor from "./FactEditor";

type EvidenceRef = Record<string, unknown>;

function normalizedReviewText(value?: string | null) {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function hasDistinctEvidenceExcerpt(fact: MemoryItem) {
  const excerpt = normalizedReviewText(fact.evidence_excerpt);
  return excerpt.length > 0 && excerpt !== normalizedReviewText(fact.value);
}

function ReviewMarkdown({ children }: { children: string }) {
  return (
    <div className="markdown-body meeting-review-markdown">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={{
          // Review text originates in uploaded material. Do not load images or
          // turn source-authored links into active navigation from this queue.
          img: () => null,
          a: ({ children: label }) => <span>{label}</span>,
        }}
      >
        {normalizeLatexMathDelimiters(children)}
      </ReactMarkdown>
    </div>
  );
}

function EvidenceActions({
  refs,
  meetingIds,
  excerpt,
  onOpen,
}: {
  refs: EvidenceRef[];
  meetingIds?: number[] | null;
  excerpt?: string | null;
  onOpen: (ref: EvidenceRef, meetingIds?: number[] | null, excerpt?: string | null) => void;
}) {
  const { formatMessage: t } = useIntl();
  if (!refs.length) return null;

  const sourceLabel = (ref: EvidenceRef, index: number) =>
    t({ id: "memory.list.sourceChoice" }, { n: index + 1, file: String(ref.file_id ?? "?") });

  if (refs.length === 1) {
    const label = sourceLabel(refs[0], 0);
    return (
      <Button
        icon={<LinkOutlined />}
        aria-label={label}
        onClick={() => onOpen(refs[0], meetingIds, excerpt)}
      >
        {label}
      </Button>
    );
  }

  const menuLabel = t({ id: "workflow.reviewSources" }, { count: refs.length });
  return (
    <Dropdown
      trigger={["click"]}
      menu={{
        style: { maxHeight: "min(60vh, 420px)", overflowY: "auto" },
        items: refs.map((ref, index) => ({
          key: `${String(ref.file_id ?? "source")}-${String(ref.chunk_index ?? index)}-${index}`,
          label: sourceLabel(ref, index),
          onClick: () => onOpen(ref, meetingIds, excerpt),
        })),
      }}
    >
      <Button icon={<LinkOutlined />} aria-label={menuLabel}>
        {menuLabel}
      </Button>
    </Dropdown>
  );
}

export default function MeetingReviewPanel({
  meetingId,
  projectId,
}: {
  meetingId?: number;
  projectId?: string;
}) {
  const { formatMessage: t } = useIntl();
  const openEvidence = useEvidenceViewer();
  const [result, setResult] = useState<components["schemas"]["ReviewQueryResponse"] | null>(null);
  const [page, setPage] = useState<{ offset: number; snapshot?: string }>({ offset: 0 });
  const [epoch, setEpoch] = useState(0);
  const requestKey = JSON.stringify([meetingId, projectId, page, epoch]);
  const [settledRequest, setSettledRequest] = useState("");
  const loading = settledRequest !== requestKey;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const refresh = () => {
    setPage({ offset: 0 });
    setEpoch((v) => v + 1);
  };
  useEffect(() => {
    const controller = new AbortController();
    api
      .post<components["schemas"]["ReviewQueryResponse"]>(
        "/memory/review/query",
        {
          meeting_id: meetingId,
          project_id: projectId,
          offset: page.offset,
          limit: 25,
          snapshot: page.offset ? page.snapshot : undefined,
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
        if (isRequestCanceled(err) || controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 409 && page.offset > 0) {
          setPage({ offset: 0 });
          setEpoch((value) => value + 1);
          return;
        }
        setError(formatApiErrorMessage(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setSettledRequest(requestKey);
      });
    return () => controller.abort();
  }, [meetingId, projectId, page, epoch, requestKey]);
  const act = async (fact: MemoryItem, confirm: boolean) => {
    setBusy(true);
    try {
      if (confirm && fact.conflicts_with?.length) {
        await resolveMemoryConflict(
          fact.key,
          fact.revision,
          fact.conflicts_with,
          Object.fromEntries(
            (result?.conflicts?.[fact.key] ?? []).map((item) => [item.key, item.revision]),
          ),
        );
      } else {
        await updateMemoryStatus(fact.key, fact.revision, confirm ? "confirmed" : "retracted");
      }
      refresh();
    } catch (err) {
      setError(formatApiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const reviewStatus = (fact: MemoryItem) => {
    if (
      fact.assertion_status === "confirmed" &&
      (fact.source === "auto_extracted" || fact.source === "consolidated")
    ) {
      return {
        color: "blue",
        label: t({ id: "workflow.autoRecordedAwaitingReview" }),
      };
    }
    return {
      color: fact.assertion_status === "disputed" ? "orange" : "default",
      label: t({
        id: `workflow.assertion.${fact.assertion_status}`,
        defaultMessage: fact.assertion_status,
      }),
    };
  };
  return (
    <div className="meeting-review-panel">
      <Alert type="info" showIcon title={t({ id: "workflow.reviewNotice" })} />
      <Space wrap className="meeting-review-toolbar">
        <Button onClick={refresh} loading={loading}>
          {t({ id: "workflow.refresh" })}
        </Button>
        {!loading && result && (
          <Typography.Text type="secondary">
            {t({ id: "workflow.reviewCount" }, { count: result.total })}
          </Typography.Text>
        )}
        {!loading &&
          Object.entries(result?.extraction_progress ?? {}).map(([stage, count]) =>
            count > 0 ? (
              <Tag
                key={stage}
                color={stage === "failed" ? "red" : stage === "completed" ? "green" : "blue"}
              >
                {t({ id: `workflow.extraction.${stage}` }, { count })}
              </Tag>
            ) : null,
          )}
      </Space>
      {!loading && !!result?.extraction_progress && (
        <Typography.Text type="secondary">
          {t({ id: "workflow.extraction.notice" })}
        </Typography.Text>
      )}
      {error && (
        <Alert
          type="error"
          title={error}
          action={<Button onClick={refresh}>{t({ id: "workflow.refresh" })}</Button>}
        />
      )}
      <Spin spinning={loading} className="meeting-review-scroll-region">
        {!loading && result?.total === 0 && (
          <Empty description={t({ id: "workflow.reviewEmpty" })} />
        )}
        <div className="meeting-review-list">
          {result?.items.map((fact) => {
            const status = reviewStatus(fact);
            return (
              <Card key={fact.key} size="small" className="meeting-review-card">
                <article>
                  <Space wrap size={[8, 8]} className="meeting-review-card-header">
                    <Tag color={status.color}>{status.label}</Tag>
                    {fact.project_id && <Tag>{fact.project_id}</Tag>}
                    <Typography.Text type="secondary" className="meeting-review-key">
                      {fact.key}
                    </Typography.Text>
                  </Space>
                  <Typography.Text type="secondary">
                    {t({ id: "workflow.proposedFact" })}
                  </Typography.Text>
                  <div className="meeting-review-value">
                    <ReviewMarkdown>{fact.value}</ReviewMarkdown>
                  </div>
                  {[fact.assignee, fact.due_at, fact.action_status].some(Boolean) && (
                    <Typography.Paragraph type="secondary" className="meeting-review-metadata">
                      {[fact.assignee, fact.due_at, fact.action_status].filter(Boolean).join(" · ")}
                    </Typography.Paragraph>
                  )}
                  {hasDistinctEvidenceExcerpt(fact) && (
                    <div className="meeting-review-evidence">
                      <Typography.Text type="secondary">
                        {t({ id: "workflow.sourceEvidence" })}
                      </Typography.Text>
                      <blockquote>
                        <ReviewMarkdown>{fact.evidence_excerpt ?? ""}</ReviewMarkdown>
                      </blockquote>
                    </div>
                  )}
                  {!!fact.conflicts_with?.length && (
                    <Alert
                      type="warning"
                      title={t({ id: "workflow.conflicts" })}
                      description={fact.conflicts_with.join(", ")}
                    />
                  )}
                  {(result?.conflicts?.[fact.key] ?? []).map((other) => (
                    <div key={other.key} className="meeting-review-conflict">
                      <Typography.Text type="secondary">
                        {t({ id: "workflow.existingConflict" })}
                      </Typography.Text>
                      <ReviewMarkdown>{other.value}</ReviewMarkdown>
                      {[other.assignee, other.due_at, other.action_status].some(Boolean) && (
                        <Typography.Paragraph type="secondary">
                          {[other.assignee, other.due_at, other.action_status]
                            .filter(Boolean)
                            .join(" · ")}
                        </Typography.Paragraph>
                      )}
                      {hasDistinctEvidenceExcerpt(other) && (
                        <blockquote>
                          <ReviewMarkdown>{other.evidence_excerpt ?? ""}</ReviewMarkdown>
                        </blockquote>
                      )}
                      <EvidenceActions
                        refs={other.evidence_refs ?? []}
                        meetingIds={other.meeting_ids}
                        excerpt={other.evidence_excerpt}
                        onOpen={openEvidence}
                      />
                    </div>
                  ))}
                  <Space wrap className="meeting-review-actions">
                    <EvidenceActions
                      refs={fact.evidence_refs ?? []}
                      meetingIds={fact.meeting_ids}
                      excerpt={fact.evidence_excerpt}
                      onOpen={openEvidence}
                    />
                    <Button disabled={busy || loading} onClick={() => setEditing(fact)}>
                      {t({ id: "workflow.edit" })}
                    </Button>
                    <Button
                      disabled={busy || loading}
                      type="primary"
                      onClick={() => void act(fact, true)}
                    >
                      {t({
                        id: fact.conflicts_with?.length
                          ? "workflow.acceptReplacement"
                          : fact.assertion_status === "confirmed"
                            ? "workflow.markReviewed"
                            : "workflow.confirm",
                      })}
                    </Button>
                    <Button disabled={busy || loading} onClick={() => void act(fact, false)}>
                      {t({ id: "workflow.reject" })}
                    </Button>
                  </Space>
                </article>
              </Card>
            );
          })}
        </div>
      </Spin>
      <Space>
        <Button
          disabled={!page.offset || loading}
          onClick={() => setPage({ offset: Math.max(0, page.offset - 25) })}
        >
          {t({ id: "memory.facts.previous" })}
        </Button>
        <Button
          disabled={result?.next_offset == null || loading}
          onClick={() => setPage({ offset: result?.next_offset ?? 0, snapshot: result?.snapshot })}
        >
          {t({ id: "memory.facts.next" })}
        </Button>
      </Space>
      {editing && (
        <FactEditor
          key={editing.key}
          fact={editing}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
