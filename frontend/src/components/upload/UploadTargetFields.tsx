import { Form, Input, Select } from "antd";
import { useIntl } from "react-intl";

interface Option {
  value: number;
  label: string;
}

interface Props {
  mode: "new" | "existing";
  existingMeetingId: number | null;
  onExistingMeetingChange: (meetingId: number) => void;
  existingMeetingOptions: Option[];
}

export function UploadTargetFields({
  mode,
  existingMeetingId,
  onExistingMeetingChange,
  existingMeetingOptions,
}: Props) {
  const { formatMessage } = useIntl();

  return (
    <>
      <Form.Item
        name="businessDomain"
        label={formatMessage({ id: "upload.businessDomain" })}
        initialValue="unspecified"
      >
        <Select
          options={["unspecified", "meeting", "course", "research"].map((value) => ({
            value,
            label: formatMessage({ id: `upload.domain.${value}` }),
          }))}
        />
      </Form.Item>
      <Form.Item name="materialRole" label={formatMessage({ id: "upload.materialRole" })}>
        <Select
          allowClear
          options={["transcript", "minutes", "agenda", "decision_log", "attachment"].map(
            (value) => ({ value, label: value }),
          )}
        />
      </Form.Item>
      {mode === "existing" && (
        <Form.Item
          label={
            <span style={{ fontWeight: 500, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "upload.target.existingMeeting" })}
            </span>
          }
          style={{ marginBottom: 12 }}
        >
          <Select
            placeholder={formatMessage({ id: "upload.target.selectMeeting" })}
            value={existingMeetingId ?? undefined}
            onChange={onExistingMeetingChange}
            options={existingMeetingOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
      )}

      {mode === "new" && (
        <Form.Item
          name="title"
          label={
            <span style={{ fontWeight: 500, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "upload.target.meetingTitle" })}
            </span>
          }
          rules={[{ required: true }]}
          style={{ marginBottom: 12 }}
        >
          <Input placeholder={formatMessage({ id: "upload.target.titlePlaceholder" })} />
        </Form.Item>
      )}

      {mode === "new" && (
        <Form.Item
          name="description"
          label={
            <span style={{ fontWeight: 500, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "upload.target.notes" })}
            </span>
          }
          style={{ marginBottom: 12 }}
        >
          <Input.TextArea
            rows={2}
            placeholder={formatMessage({ id: "upload.target.notesPlaceholder" })}
          />
        </Form.Item>
      )}
    </>
  );
}
