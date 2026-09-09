import { useEffect, useState } from "react";
import { Alert, Button } from "antd";
import { useIntl } from "react-intl";
import { api, formatApiErrorMessage, isRequestCanceled } from "../../api/client-core";
import type { components } from "../../api/generated";

type Status = components["schemas"]["VectorRebuildStatus"];

export default function VectorRebuildStatus({ submitting }: { submitting: boolean }) {
  const { formatMessage: t } = useIntl();
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const { data } = await api.get<Status>("/settings/rebuild-status", {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setStatus(data);
        setError("");
        if (data.active || submitting) timer = setTimeout(read, 2000);
      } catch (err) {
        if (!isRequestCanceled(err)) setError(formatApiErrorMessage(err));
      }
    };
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [submitting, retry]);
  if (error)
    return (
      <Alert
        type="error"
        title={error}
        action={
          <Button onClick={() => setRetry(retry + 1)}>{t({ id: "workflow.refresh" })}</Button>
        }
      />
    );
  if (!status || status.result === "idle") return null;
  return (
    <Alert
      type={
        status.result === "completed"
          ? "success"
          : ["failed", "cancelled"].includes(status.result)
            ? "error"
            : "info"
      }
      title={t({ id: `workflow.rebuild.${status.result}` })}
    />
  );
}
