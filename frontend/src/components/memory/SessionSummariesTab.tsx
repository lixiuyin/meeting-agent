import { useState, useCallback, useEffect } from "react";
import { formatLocalTime } from "../../utils/time";
import { Button, Empty, List, Row, Space, Spin, Tag, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";
import { Typography } from "antd";
import {
  listSessionSummaries,
  formatApiErrorMessage,
  type SessionSummaryItem,
} from "../../api/client";

const { Text, Paragraph } = Typography;

interface SessionSummariesTabProps {
  userId: string;
}

export default function SessionSummariesTab({ userId }: SessionSummariesTabProps) {
  const { formatMessage } = useIntl();
  const [summaries, setSummaries] = useState<SessionSummaryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const noSummariesMsg = formatMessage({ id: "memory.summary.noSummaries" });
  const refreshBtnLabel = formatMessage({ id: "memory.summary.refresh" });
  const loadFailedMsg = formatMessage({ id: "memory.summary.loadFailed" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listSessionSummaries(userId);
      setSummaries(res.data.summaries);
    } catch (err) {
      message.error(formatApiErrorMessage(err, loadFailedMsg));
    } finally {
      setLoading(false);
    }
  }, [userId, loadFailedMsg]);

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
          <List
            dataSource={summaries}
            renderItem={(s) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <Space size={4}>
                      <Text strong>{s.session_title || s.session_id.slice(0, 12) + "..."}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {formatLocalTime(s.created_at, { dateOnly: true })} ·{" "}
                        {formatMessage({ id: "memory.summary.turnCount" }, { count: s.turn_count })}
                      </Text>
                    </Space>
                  }
                  description={
                    <Space orientation="vertical" size={4} style={{ width: "100%" }}>
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
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Spin>
    </Space>
  );
}
