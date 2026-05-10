import { Modal, Input } from "antd";

export interface EditModalData {
  id: number;
  title: string;
  description: string;
  meeting_date: string;
}

interface MeetingFormModalProps {
  open: boolean;
  data: EditModalData | null;
  onChange: (updater: (prev: EditModalData | null) => EditModalData | null) => void;
  onSave: (data: EditModalData) => void;
  onCancel: () => void;
}

export default function MeetingFormModal({
  open,
  data,
  onChange,
  onSave,
  onCancel,
}: MeetingFormModalProps) {
  return (
    <Modal
      title="Edit Meeting"
      open={open}
      onCancel={onCancel}
      centered
      width="min(96vw, 700px)"
      styles={{ body: { maxHeight: "calc(100vh - 240px)", overflowY: "auto" } }}
      onOk={() => {
        if (data) onSave(data);
      }}
      okText="Save"
      okButtonProps={{ disabled: !data?.title.trim() }}
      zIndex={1220}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Title</div>
          <Input
            value={data?.title ?? ""}
            onChange={(e) => onChange((prev) => (prev ? { ...prev, title: e.target.value } : null))}
            placeholder="Meeting title"
          />
        </div>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Description</div>
          <Input.TextArea
            value={data?.description ?? ""}
            onChange={(e) =>
              onChange((prev) => (prev ? { ...prev, description: e.target.value } : null))
            }
            placeholder="Meeting description"
            rows={3}
          />
        </div>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Meeting Date</div>
          <Input
            type="date"
            value={data?.meeting_date ?? ""}
            onChange={(e) =>
              onChange((prev) => (prev ? { ...prev, meeting_date: e.target.value } : null))
            }
            style={{ width: "100%" }}
          />
        </div>
      </div>
    </Modal>
  );
}
