import { useState } from "react";
import { Modal, Input, InputNumber, Row, Col, Space } from "antd";
import { Typography } from "antd";
import { useIntl } from "react-intl";

const { Text } = Typography;

interface MemoryFormValues {
  key?: string;
  value?: string;
  category?: string;
  importance?: number;
}

interface MemoryFormModalProps {
  title: string;
  open: boolean;
  onClose: () => void;
  onSubmit: (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    expiresInDays?: number;
  }) => void;
  initialValues?: MemoryFormValues;
  disableKey?: boolean;
}

export default function MemoryFormModal({
  title,
  open,
  onClose,
  onSubmit,
  initialValues,
  disableKey,
}: MemoryFormModalProps) {
  const formKey = [
    open ? "open" : "closed",
    initialValues?.key ?? "",
    initialValues?.value ?? "",
    initialValues?.category ?? "",
    initialValues?.importance ?? 3,
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
}: MemoryFormModalProps) {
  const { formatMessage } = useIntl();
  const [key, setKey] = useState(initialValues?.key ?? "");
  const [value, setValue] = useState(initialValues?.value ?? "");
  const [category, setCategory] = useState(initialValues?.category ?? "");
  const [importance, setImportance] = useState<number | undefined>(initialValues?.importance ?? 3);
  const [expiresInDays, setExpiresInDays] = useState<number | undefined>(undefined);

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
        })
      }
      okText={okText}
      okButtonProps={{ disabled: !key.trim() || !value.trim() }}
    >
      <Space orientation="vertical" style={{ width: "100%" }} size="middle">
        <div>
          <Text strong style={{ display: "block", marginBottom: 4 }}>
            {keyLabel}
          </Text>
          <Input
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
              min={-1}
              value={expiresInDays}
              onChange={(v) => setExpiresInDays(typeof v === "number" ? v : undefined)}
              style={{ width: "100%" }}
              placeholder={ttlPlaceholder}
            />
          </Col>
        </Row>
      </Space>
    </Modal>
  );
}
