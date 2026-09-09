import { useState, useCallback, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { formatLocalTime } from "../../utils/time";
import { Button, Empty, Row, Space, Spin, Tag, message } from "antd";
import { MessageOutlined, ReloadOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";
import { Typography } from "antd";
import {
  listAllSessionSummaries,
  formatApiErrorMessage,
  isRequestCanceled,
  type SessionSummaryItem,
} from "../../api/client";

const { Text, Paragraph } = Typography;

interface SessionSummariesTabProps {
  userId: string;
}

export default function SessionSummariesTab({ userId }: SessionSummariesTabProps) {
  const { formatMessage } = useIntl();
  const navigate = useNavigate();
  const [summaries, setSummaries] = useState<SessionSummaryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const loadAbortRef = useRef<AbortController | null>(null);
  const noSummariesMsg = formatMessage({ id: "memory.summary.noSummaries" });
  const refreshBtnLabel = formatMessage({ id: "memory.summary.refresh" });
  const loadFailedMsg = formatMessage({ id: "memory.summary.loadFailed" });

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setLoading(true);
    try {
      const all = await listAllSessionSummaries(userId, { signal: controller.signal });
      if (!controller.signal.aborted) setSummaries(all);
    } catch (err) {
      if (!isRequestCanceled(err)) {
        message.error(formatApiErrorMessage(err, loadFailedMsg));
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [userId, loadFailedMsg]);

  useEffect(() => () => loadAbortRef.current?.abort(), []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [load]);

  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="middle">
      <Row justify="end">
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>
          {refreshBtnLabel}
        </Button>
      </Row>
      <Spin spinning={loading}>
        {summaries.length === 0 ? (
          <Empty description={noSummariesMsg} />
        ) : (
          <div className="session-summary-list" role="list">
            {summaries.map((s) => (
              <div
                key={s.session_id}
                role="listitem"
                className="session-summary-list-item"
                style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "12px 0" }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space size={4} wrap>
                    <Text strong>{s.session_title || s.session_id.slice(0, 12) + "..."}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {formatLocalTime(s.created_at, { dateOnly: true })} ·{" "}
                      {formatMessage({ id: "memory.summary.turnCount" }, { count: s.turn_count })}
                    </Text>
                  </Space>
                  <Space orientation="vertical" size={4} style={{ display: "flex", width: "100%" }}>
                    <Paragraph ellipsis={{ rows: 2 }} style={{ margin: 0 }}>
                      {s.summary}
                    </Paragraph>
                    <Space wrap size={4}>
                      {s.topics.map((t) => (
                        <Tag key={t} color="cyan">
                          {t}
                        </Tag>
                      ))}
                      {s.decisions.map((d) => (
                        <Tag key={d} color="orange">
                          {d}
                        </Tag>
                      ))}
                    </Space>
                  </Space>
                </div>
                <Button
                  type="link"
                  icon={<MessageOutlined />}
                  onClick={() => navigate(`/?sessionId=${encodeURIComponent(s.session_id)}`)}
                >
                  {formatMessage({ id: "memory.summary.continue" })}
                </Button>
              </div>
            ))}
          </div>
        )}
      </Spin>
    </Space>
  );
}
