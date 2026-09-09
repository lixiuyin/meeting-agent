import { useState } from "react";
import { Modal, Input, InputNumber, Row, Col, Space, Select } from "antd";
import { Typography } from "antd";
import { useIntl } from "react-intl";

const { Text } = Typography;

type FactType = "fact" | "preference" | "project_fact" | "decision" | "action_item";
type AssertionStatus = "pending" | "confirmed" | "disputed" | "superseded" | "retracted";

export interface MemoryFormValues {
  key?: string;
  value?: string;
  category?: string;
  importance?: number;
  factType?: FactType;
  assertionStatus?: AssertionStatus;
  projectId?: string;
  validFrom?: string;
  validTo?: string;
  actionStatus?: "open" | "in_progress" | "blocked" | "done" | "cancelled";
  assignee?: string;
  dueAt?: string;
}

interface MemoryFormModalProps {
  title: string;
  open: boolean;
  onClose: () => void;
  onSubmit: (
    values: MemoryFormValues & { key: string; value: string; expiresInDays?: number },
  ) => void;
  initialValues?: MemoryFormValues;
  disableKey?: boolean;
  submitting?: boolean;
}

export default function MemoryFormModal({
  title,
  open,
  onClose,
  onSubmit,
  initialValues,
  disableKey,
  submitting,
}: MemoryFormModalProps) {
  const formKey = [
    open ? "open" : "closed",
    initialValues?.key ?? "",
    initialValues?.value ?? "",
    initialValues?.category ?? "",
    initialValues?.importance ?? 3,
    initialValues?.factType ?? "fact",
    initialValues?.assertionStatus ?? "confirmed",
    initialValues?.projectId ?? "",
    initialValues?.validFrom ?? "",
    initialValues?.validTo ?? "",
    initialValues?.actionStatus ?? "",
    initialValues?.assignee ?? "",
    initialValues?.dueAt ?? "",
  ].join(":");

  return (
    <MemoryFormModalInner
      key={formKey}
      title={title}
      open={open}
      onClose={onClose}
      onSubmit={onSubmit}
      initialValues={initialValues}
      disableKey={disableKey}
      submitting={submitting}
    />
  );
}

function MemoryFormModalInner({
  title,
  open,
  onClose,
  onSubmit,
  initialValues,
  disableKey,
  submitting,
}: MemoryFormModalProps) {
  const { formatMessage } = useIntl();
  const [key, setKey] = useState(initialValues?.key ?? "");
  const [value, setValue] = useState(initialValues?.value ?? "");
  const [category, setCategory] = useState(initialValues?.category ?? "");
  const [importance, setImportance] = useState<number | undefined>(initialValues?.importance ?? 3);
  const [expiresInDays, setExpiresInDays] = useState<number | undefined>(undefined);
  const [factType, setFactType] = useState<FactType>(initialValues?.factType ?? "fact");
  const [assertionStatus, setAssertionStatus] = useState<AssertionStatus>(
    initialValues?.assertionStatus ?? "confirmed",
  );
  const [projectId, setProjectId] = useState(initialValues?.projectId ?? "");
  const [validFrom, setValidFrom] = useState(initialValues?.validFrom ?? "");
  const [validTo, setValidTo] = useState(initialValues?.validTo ?? "");
  const [actionStatus, setActionStatus] = useState<MemoryFormValues["actionStatus"]>(
    initialValues?.actionStatus,
  );
  const [assignee, setAssignee] = useState(initialValues?.assignee ?? "");
  const [dueAt, setDueAt] = useState(initialValues?.dueAt ?? "");

  const keyLabel = formatMessage({ id: "memory.form.key" });
  const valueLabel = formatMessage({ id: "memory.form.value" });
  const categoryLabel = formatMessage({ id: "memory.form.category" });
  const importanceLabel = formatMessage({ id: "memory.form.importance" });
  const ttlLabel = formatMessage({ id: "memory.form.ttl" });
  const keyPlaceholder = formatMessage({ id: "memory.form.keyPlaceholder" });
  const valuePlaceholder = formatMessage({ id: "memory.form.valuePlaceholder" });
  const categoryPlaceholder = formatMessage({ id: "memory.form.categoryPlaceholder" });
  const ttlPlaceholder = formatMessage({ id: "memory.form.ttlPlaceholder" });
  const okText = formatMessage({ id: disableKey ? "memory.form.update" : "memory.form.create" });

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onClose}
      width="min(94vw, 700px)"
      styles={{ body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" } }}
      onOk={() =>
        onSubmit({
          key,
          value,
          category: category || undefined,
          importance,
          expiresInDays,
          factType,
          assertionStatus,
          projectId: projectId || undefined,
          validFrom: validFrom ? new Date(validFrom).toISOString() : undefined,
          validTo: validTo ? new Date(validTo).toISOString() : undefined,
          actionStatus: factType === "action_item" ? actionStatus || "open" : undefined,
          assignee: factType === "action_item" ? assignee || undefined : undefined,
          dueAt: factType === "action_item" && dueAt ? new Date(dueAt).toISOString() : undefined,
        })
      }
      okText={okText}
      confirmLoading={submitting}
      okButtonProps={{ disabled: submitting || !key.trim() || !value.trim() }}
    >
      <Space orientation="vertical" style={{ width: "100%" }} size="middle">
        <div>
          <Text strong style={{ display: "block", marginBottom: 4 }}>
            {keyLabel}
          </Text>
          <Input
            aria-label={keyLabel}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            disabled={disableKey}
            placeholder={keyPlaceholder}
          />
        </div>
        <div>
          <Text strong style={{ display: "block", marginBottom: 4 }}>
            {valueLabel}
          </Text>
          <Input.TextArea
            aria-label={valueLabel}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={4}
            placeholder={valuePlaceholder}
          />
        </div>
        <div>
          <Text strong style={{ display: "block", marginBottom: 4 }}>
            {categoryLabel}
          </Text>
          <Input
            aria-label={categoryLabel}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder={categoryPlaceholder}
          />
        </div>
        <Row gutter={16}>
          <Col span={12}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {importanceLabel}
            </Text>
            <InputNumber
              aria-label={importanceLabel}
              min={1}
              max={5}
              value={importance}
              onChange={(v) => setImportance(typeof v === "number" ? v : 3)}
              style={{ width: "100%" }}
            />
          </Col>
          <Col span={12}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {ttlLabel}
            </Text>
            <InputNumber
              aria-label={ttlLabel}
              min={-1}
              value={expiresInDays}
              onChange={(v) => setExpiresInDays(typeof v === "number" ? v : undefined)}
              style={{ width: "100%" }}
              placeholder={ttlPlaceholder}
            />
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={12}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {formatMessage({ id: "memory.form.factType" })}
            </Text>
            <Select
              aria-label={formatMessage({ id: "memory.form.factType" })}
              value={factType}
              onChange={setFactType}
              style={{ width: "100%" }}
              options={["fact", "preference", "project_fact", "decision", "action_item"].map(
                (value) => ({ value, label: value }),
              )}
            />
          </Col>
          <Col span={12}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {formatMessage({ id: "memory.form.status" })}
            </Text>
            <Select
              aria-label={formatMessage({ id: "memory.form.status" })}
              value={assertionStatus}
              onChange={setAssertionStatus}
              style={{ width: "100%" }}
              options={["pending", "confirmed", "disputed", "superseded", "retracted"].map(
                (value) => ({ value, label: value }),
              )}
            />
          </Col>
        </Row>
        <div>
          <Text strong style={{ display: "block", marginBottom: 4 }}>
            {formatMessage({ id: "memory.form.projectId" })}
          </Text>
          <Input
            aria-label={formatMessage({ id: "memory.form.projectId" })}
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          />
        </div>
        {factType === "action_item" && (
          <Row gutter={16}>
            <Col span={8}>
              <Text strong style={{ display: "block", marginBottom: 4 }}>
                {formatMessage({ id: "memory.form.actionStatus" })}
              </Text>
              <Select
                aria-label={formatMessage({ id: "memory.form.actionStatus" })}
                value={actionStatus}
                onChange={setActionStatus}
                style={{ width: "100%" }}
                options={["open", "in_progress", "blocked", "done", "cancelled"].map((value) => ({
                  value,
                  label: value,
                }))}
              />
            </Col>
            <Col span={8}>
              <Text strong style={{ display: "block", marginBottom: 4 }}>
                {formatMessage({ id: "memory.form.assignee" })}
              </Text>
              <Input
                aria-label={formatMessage({ id: "memory.form.assignee" })}
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
              />
            </Col>
            <Col span={8}>
              <Text strong style={{ display: "block", marginBottom: 4 }}>
                {formatMessage({ id: "memory.form.dueAt" })}
              </Text>
              <Input
                aria-label={formatMessage({ id: "memory.form.dueAt" })}
                type="datetime-local"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
              />
            </Col>
          </Row>
        )}
        <Row gutter={16}>
          <Col span={12}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {formatMessage({ id: "memory.form.validFrom" })}
            </Text>
            <Input
              aria-label={formatMessage({ id: "memory.form.validFrom" })}
              type="datetime-local"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </Col>
          <Col span={12}>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {formatMessage({ id: "memory.form.validTo" })}
            </Text>
            <Input
              aria-label={formatMessage({ id: "memory.form.validTo" })}
              type="datetime-local"
              value={validTo}
              onChange={(e) => setValidTo(e.target.value)}
            />
          </Col>
        </Row>
      </Space>
    </Modal>
  );
}
