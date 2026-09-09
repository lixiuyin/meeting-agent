import { useState } from "react";
import { Alert, Input, Modal, Select, Space } from "antd";
import { useIntl } from "react-intl";
import { api, formatApiErrorMessage } from "../../api/client-core";
import type { MemoryItem } from "../../api/client-memory";

export default function FactEditor({
  fact,
  onClose,
  onSaved,
}: {
  fact: MemoryItem;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { formatMessage: t } = useIntl();
  const [value, setValue] = useState(fact.value);
  const [assignee, setAssignee] = useState(fact.assignee ?? "");
  const [status, setStatus] = useState(fact.action_status ?? "open");
  const [due, setDue] = useState(() => {
    if (!fact.due_at) return "";
    const date = new Date(fact.due_at);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      open
      title={t({ id: "workflow.edit" })}
      onCancel={onClose}
      confirmLoading={busy}
      okButtonProps={{ disabled: !value.trim() }}
      onOk={async () => {
        setBusy(true);
        try {
          await api.put(
            "/memory",
            {
              key: fact.key,
              expected_revision: fact.revision,
              value,
              ...(fact.fact_type === "action_item"
                ? {
                    action_status: status,
                    assignee: assignee.trim() || null,
                    due_at: due ? new Date(due).toISOString() : null,
                  }
                : {}),
            },
            { headers: { "Idempotency-Key": crypto.randomUUID() } },
          );
          onSaved();
          onClose();
        } catch (err) {
          setError(formatApiErrorMessage(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <Space orientation="vertical" style={{ width: "100%" }}>
        {error && (
          <Alert type="error" showIcon title={error} description={t({ id: "workflow.conflict" })} />
        )}
        <label>
          {t({ id: "memory.form.value" })}
          <Input.TextArea value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        {fact.fact_type === "action_item" && (
          <>
            <label>
              {t({ id: "memory.form.assignee" })}
              <Input value={assignee} onChange={(e) => setAssignee(e.target.value)} />
            </label>
            <label>
              {t({ id: "workflow.status" })}
              <Select
                value={status}
                onChange={setStatus}
                style={{ width: "100%" }}
                options={["open", "in_progress", "blocked", "done", "cancelled"].map((v) => ({
                  value: v,
                  label: t({ id: `workflow.${v}` }),
                }))}
              />
            </label>
            <label>
              {t({ id: "workflow.due" })}
              <Input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
            </label>
          </>
        )}
      </Space>
    </Modal>
  );
}
