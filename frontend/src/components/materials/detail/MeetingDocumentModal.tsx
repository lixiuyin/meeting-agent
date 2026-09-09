import { Button, Modal, Spin } from "antd";
import { type FileTimelineResponse } from "../../../api/client";
import { useMeetingFileUrl } from "../../../hooks/useMeetingFileUrl";
import { getKindCapabilities } from "../../../types/fileKinds";
import DocFileView from "../file-views/DocFileView";
import ImageFileView from "../file-views/ImageFileView";
import TextFileView from "../file-views/TextFileView";
import { DOCUMENT_VIEWER_MODAL } from "../viewerModalPresets";
import type { DrawerFileItem } from "./types";

interface Props {
  meetingId: number;
  fileName: string;
  selectedFile: DrawerFileItem | undefined;
  timeline: FileTimelineResponse | undefined;
  loading: boolean;
  errorMessage: string | null | undefined;
  onClose: () => void;
  onRetry: () => void;
  onReprocessFile?: (meetingId: number, fileId: number) => void;
}

export function MeetingDocumentModal({
  meetingId,
  fileName,
  selectedFile,
  timeline,
  loading,
  errorMessage,
  onClose,
  onRetry,
  onReprocessFile,
}: Props) {
  const selectedFileUrl = useMeetingFileUrl(meetingId, selectedFile?.id ?? 0);
  return (
    <Modal
      title={fileName}
      open={true}
      onCancel={onClose}
      footer={null}
      width={DOCUMENT_VIEWER_MODAL.width}
      style={{ top: DOCUMENT_VIEWER_MODAL.top }}
      wrapClassName={DOCUMENT_VIEWER_MODAL.wrapClassName}
      styles={{
        body: {
          height: DOCUMENT_VIEWER_MODAL.bodyHeight,
          overflowY: "auto",
          padding: DOCUMENT_VIEWER_MODAL.bodyPadding,
        },
      }}
      zIndex={1210}
      destroyOnHidden
    >
      {(() => {
        if (!selectedFile) return null;
        const caps = getKindCapabilities(selectedFile.file_type);

        if (loading && !timeline) {
          return (
            <div style={{ textAlign: "center", padding: 20 }}>
              <Spin size="small" />
            </div>
          );
        }

        if (!timeline) {
          return (
            <div
              style={{
                fontSize: 13,
                color: errorMessage ? "#dc2626" : "var(--color-text-muted)",
                padding: 8,
              }}
            >
              {errorMessage ?? "No detail data available for this file."}
              {errorMessage ? (
                <>
                  <Button
                    size="small"
                    type="link"
                    style={{ paddingInline: 0, marginLeft: 8 }}
                    onClick={onRetry}
                  >
                    Retry
                  </Button>
                  {onReprocessFile && selectedFile ? (
                    <Button
                      size="small"
                      type="link"
                      style={{ paddingInline: 0, marginLeft: 4 }}
                      onClick={() => {
                        onReprocessFile(meetingId, selectedFile.id);
                        onClose();
                      }}
                    >
                      Reprocess
                    </Button>
                  ) : null}
                </>
              ) : null}
            </div>
          );
        }

        if (timeline.kind === "pages") {
          return (
            <DocFileView
              fileName={selectedFile.file_name}
              kind={caps.kind === "pptx" ? "pptx" : "pdf"}
              pages={timeline.pages}
              loading={loading}
            />
          );
        }
        if (timeline.kind === "captions") {
          if (!selectedFileUrl) {
            return (
              <div style={{ textAlign: "center", padding: 20 }}>
                <Spin size="small" />
              </div>
            );
          }
          return (
            <ImageFileView
              fileUrl={selectedFileUrl}
              fileName={selectedFile.file_name}
              captions={timeline.captions}
            />
          );
        }
        if (timeline.kind === "text") {
          return (
            <TextFileView
              fileName={selectedFile.file_name}
              text={timeline.text}
              wordCount={timeline.word_count}
            />
          );
        }
        return null;
      })()}
    </Modal>
  );
}
