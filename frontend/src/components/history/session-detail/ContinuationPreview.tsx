import { useEffect, useState } from "react";
import { Alert, Button, Collapse, Space, Spin, Tag } from "antd";
import { useIntl } from "react-intl";
import {
  getContinuationPreview,
  formatApiErrorMessage,
  isRequestCanceled,
} from "../../../api/client";

export default function ContinuationPreview({ sessionId }: { sessionId: string }) {
  const { formatMessage: t } = useIntl();
  const [data, setData] = useState<
    Awaited<ReturnType<typeof getContinuationPreview>>["data"] | null
  >(null);
  const [error, setError] = useState("");
  const [serial, setSerial] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getContinuationPreview(sessionId, { signal: controller.signal })
      .then(({ data: value }) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((err) => {
        if (!controller.signal.aborted && !isRequestCanceled(err))
          setError(formatApiErrorMessage(err));
      });
    return () => controller.abort();
  }, [sessionId, serial]);
  const changed =
    data &&
    (data.files.some((file) => file.status !== "unchanged") || data.memory_changes.length > 0);
  const scope = data?.scope ?? {};
  const describeFiles = (value: unknown) => {
    const ids = Array.isArray(value) ? value.filter((id) => typeof id === "number" && id > 0) : [];
    return ids.length
      ? ids
          .map((id) => data?.files.find((file) => file.file_id === id)?.file_name || `File ${id}`)
          .join(", ")
      : t({ id: "history.resume.scopeUnrestricted" });
  };
  const documentEmpty =
    scope.empty_file_scope === true ||
    (scope.file_scope as { mode?: string } | undefined)?.mode === "empty";
  return (
    <Collapse
      style={{ marginBottom: 12 }}
      items={[
        {
          key: "preview",
          label: t({ id: "history.resume.preview" }),
          extra: changed ? (
            <Tag style={{ color: "#873800", background: "#fff7e6", borderColor: "#ffd591" }}>
              {t({ id: "history.resume.changed" })}
            </Tag>
          ) : undefined,
          children: error ? (
            <Alert
              type="warning"
              title={error}
              action={
                <Button
                  onClick={() => {
                    setData(null);
                    setError("");
                    setSerial(serial + 1);
                  }}
                >
                  {t({ id: "memory.facts.apply" })}
                </Button>
              }
            />
          ) : !data ? (
            <Spin />
          ) : (
            <Space orientation="vertical" style={{ width: "100%" }}>
              <Alert
                type={changed ? "warning" : "info"}
                title={t({ id: "history.resume.notice" })}
              />
              <div>
                {t(
                  { id: "history.resume.savedScope" },
                  {
                    files: documentEmpty
                      ? t({ id: "history.resume.scopeEmpty" })
                      : describeFiles(scope.file_ids),
                    projects:
                      Array.isArray(scope.project_ids) && scope.project_ids.length
                        ? scope.project_ids.filter((id) => typeof id === "string").join(", ")
                        : t({ id: "history.resume.scopeUnrestricted" }),
                    memoryFiles: describeFiles(scope.memory_scope_file_ids ?? scope.file_ids),
                  },
                )}
              </div>
              <div>{t({ id: "history.resume.scopeOverride" })}</div>
              <div>
                {t(
                  { id: "history.resume.snapshot" },
                  {
                    available: data.saved_snapshot_available
                      ? t({ id: "common.enabled" })
                      : t({ id: "common.disabled" }),
                  },
                )}
              </div>
              {data.files.map((file) => (
                <div key={file.file_id} data-testid="continuation-source-change">
                  {file.file_name || `File ${file.file_id}`}{" "}
                  <Tag>{t({ id: `history.resume.${file.status}` })}</Tag>
                </div>
              ))}
              {data.memory_changes.map((memory) => (
                <div key={memory.key}>
                  {memory.key} <Tag>{t({ id: "history.resume.changed" })}</Tag>
                </div>
              ))}
              {data.open_questions
                .filter((question) => typeof question === "string")
                .map((question, index) => (
                  <div key={index}>{question}</div>
                ))}
              <div>
                {t({ id: "history.resume.checkpoint" }, { count: data.messages_since_checkpoint })}
              </div>
            </Space>
          ),
        },
      ]}
    />
  );
}
