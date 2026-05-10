import { Modal, Spin, Alert, Tag, Button, Input } from "antd";
import { SoundOutlined, UserOutlined } from "@ant-design/icons";
import type { SpeakersResponse } from "../../api/client";

interface SpeakerModalProps {
  open: boolean;
  loading: boolean;
  data: SpeakersResponse | null;
  names: Record<string, string>;
  playing: string | null;
  saving: boolean;
  meetingId: number | null;
  onNamesChange: (updater: (prev: Record<string, string>) => Record<string, string>) => void;
  onPlay: (meetingId: number, fileId: number, speakerCode: string) => void;
  onSave: () => void;
  onClose: () => void;
  onStopAll: () => void;
}

export default function SpeakerModal({
  open,
  loading,
  data,
  names,
  playing,
  saving,
  meetingId,
  onNamesChange,
  onPlay,
  onSave,
  onClose,
  onStopAll,
}: SpeakerModalProps) {
  const handleCancel = () => {
    onStopAll();
    onClose();
  };

  return (
    <Modal
      title="Identify Speakers"
      open={open}
      onCancel={handleCancel}
      centered
      width="min(96vw, 780px)"
      styles={{ body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" } }}
      zIndex={1230}
      footer={
        data
          ? [
              <Button key="cancel" onClick={handleCancel}>
                Cancel
              </Button>,
              <Button
                key="save"
                type="primary"
                loading={saving}
                onClick={() => {
                  onSave();
                }}
              >
                Save Names
              </Button>,
            ]
          : null
      }
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
          <div style={{ marginTop: 12, color: "var(--color-text-muted)" }}>Loading speakers...</div>
        </div>
      ) : data && data.speakers.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Alert
            message="Listen to each speaker's sample and enter their real name."
            type="info"
            showIcon
            style={{ marginBottom: 4 }}
          />
          {data.speakers.map((speaker) => {
            const isPlaying = playing === speaker.speaker_code;
            return (
              <div
                key={speaker.speaker_code}
                style={{
                  padding: 16,
                  borderRadius: 12,
                  background: "var(--color-bg-muted)",
                  border: "1px solid var(--color-border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    marginBottom: 8,
                  }}
                >
                  <Tag
                    style={{
                      borderRadius: 20,
                      fontWeight: 600,
                      fontSize: 13,
                      padding: "2px 12px",
                    }}
                    color="blue"
                  >
                    {speaker.speaker_code}
                  </Tag>
                  <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                    {speaker.utterance_count} utterance
                    {speaker.utterance_count !== 1 ? "s" : ""}
                  </span>
                </div>
                {speaker.sample && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 8,
                      marginBottom: 12,
                    }}
                  >
                    <Button
                      size="small"
                      type={isPlaying ? "primary" : "default"}
                      danger={isPlaying}
                      icon={<SoundOutlined />}
                      onClick={() => {
                        if (isPlaying) {
                          onStopAll();
                        } else if (meetingId != null) {
                          onPlay(meetingId, data.file_id, speaker.speaker_code);
                        }
                      }}
                    >
                      {isPlaying ? "Stop" : "Play Sample"}
                    </Button>
                    <div
                      style={{
                        flex: 1,
                        fontSize: 12,
                        color: "var(--color-text-secondary)",
                        fontStyle: "italic",
                        lineHeight: 1.5,
                        paddingTop: 2,
                      }}
                    >
                      "{speaker.sample.text}"
                    </div>
                  </div>
                )}
                <Input
                  prefix={<UserOutlined style={{ color: "var(--color-text-muted)" }} />}
                  placeholder={`Enter name for Speaker ${speaker.speaker_code}`}
                  value={names[speaker.speaker_code] ?? ""}
                  onChange={(e) =>
                    onNamesChange((prev) => ({
                      ...prev,
                      [speaker.speaker_code]: e.target.value,
                    }))
                  }
                />
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ textAlign: "center", padding: 24, color: "var(--color-text-muted)" }}>
          No speakers detected. This file may not have speaker diarization enabled.
        </div>
      )}
    </Modal>
  );
}
