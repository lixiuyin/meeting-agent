import MeetingReviewPanel from "../memory/MeetingReviewPanel";
import { useIntl } from "react-intl";
import { useCallback, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Spin, Tag, Timeline, message } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import type { MeetingDetailInfo, MeetingInfo, FileTimelineResponse } from "../../api/client";
import {
  getFileTimeline,
  getMeetingFileSemanticHistory,
  getMeetingAssetUrl,
  getMeetingFileUrl,
  updateMeetingFileSemantics,
  type ApprovalStatus,
  type MaterialRole,
  type BusinessDomain,
  type MeetingFileSemanticEvent,
} from "../../api/client";
import {
  buildPerFileMarkdownBundle,
  createMarkdownZipBlob,
  rewriteMeetingAssetLinks,
  sanitizeMarkdownFileName,
  triggerBlobDownload,
} from "../../utils/exportMarkdown";
import PdfComparisonModal from "./file-views/PdfComparisonModal";
import type { PageItem } from "./file-views/types";
import { useMainContentScrollLock } from "../../hooks/useMainContentScrollLock";
import { MeetingDetailHeader } from "./detail/MeetingDetailHeader";
import { MeetingDocumentModal } from "./detail/MeetingDocumentModal";
import { MeetingFilesSection } from "./detail/MeetingFilesSection";
import type { DrawerFileItem } from "./detail/types";
import { formatLocalTime } from "../../utils/time";

const DETAIL_MODAL_WIDTH = "min(96vw, 980px)";
const DETAIL_MODAL_BODY_MAX_HEIGHT = "calc(100vh - 140px)";

interface MeetingDetailDrawerProps {
  detailMeeting: MeetingInfo;
  detailFull: MeetingDetailInfo | null;
  detailLoading: boolean;
  detailFileType: string | null;
  exporting: boolean;
  onClose: () => void;
  onEdit: () => void;
  onExport: (format: "json" | "markdown" | "txt") => void;
  onViewTimestamps: (meetingId: number, fileId: number) => void;
  onGenerateSummary: (id: number) => void;
  onDownloadFile: (meetingId: number, fileId: number, fileName: string) => void;
  onDeleteFile: (meetingId: number, fileId: number) => void;
  onOpenSpeakers: (meetingId: number, fileId: number) => void;
  onReprocessFile: (meetingId: number, fileId: number) => void;
}

export default function MeetingDetailDrawer({ detailMeeting, ...props }: MeetingDetailDrawerProps) {
  return (
    <MeetingDetailDrawerInner key={detailMeeting.id} detailMeeting={detailMeeting} {...props} />
  );
}

function MeetingDetailDrawerInner({
  detailMeeting,
  detailFull,
  detailLoading,
  detailFileType,
  exporting,
  onClose,
  onEdit,
  onExport,
  onViewTimestamps,
  onGenerateSummary,
  onDownloadFile,
  onDeleteFile,
  onOpenSpeakers,
  onReprocessFile,
}: MeetingDetailDrawerProps) {
  const [expandedSummaryFileId, setExpandedSummaryFileId] = useState<number | null>(null);
  const intl = useIntl();
  const [timelineCache, setTimelineCache] = useState<Record<number, FileTimelineResponse>>({});
  const [timelineLoading, setTimelineLoading] = useState<Record<number, boolean>>({});
  const [timelineError, setTimelineError] = useState<Record<number, string | null>>({});
  const inflightTimelineRef = useRef<Set<number>>(new Set());
  const [pdfModal, setPdfModal] = useState<{ fileId: number } | null>(null);
  const [docModal, setDocModal] = useState<{ fileId: number } | null>(null);
  const [semanticOverrides, setSemanticOverrides] = useState<Record<number, DrawerFileItem>>({});
  const [rejectionDraft, setRejectionDraft] = useState<{
    file: DrawerFileItem;
    reason: string;
  } | null>(null);
  const [semanticHistory, setSemanticHistory] = useState<{
    file: DrawerFileItem;
    events: MeetingFileSemanticEvent[];
    loading: boolean;
  } | null>(null);

  useMainContentScrollLock(Boolean(docModal));

  const fetchFileTimeline = useCallback(
    async (fileId: number) => {
      if (inflightTimelineRef.current.has(fileId)) return;
      inflightTimelineRef.current.add(fileId);
      setTimelineError((prev) => ({ ...prev, [fileId]: null }));
      setTimelineLoading((prev) => ({ ...prev, [fileId]: true }));
      try {
        const resp = await getFileTimeline(detailMeeting.id, fileId);
        setTimelineCache((prev) => ({ ...prev, [fileId]: resp.data }));
      } catch {
        setTimelineError((prev) => ({ ...prev, [fileId]: "Failed to load file details." }));
      } finally {
        inflightTimelineRef.current.delete(fileId);
        setTimelineLoading((prev) => ({ ...prev, [fileId]: false }));
      }
    },
    [detailMeeting.id],
  );

  const handleDownloadFileMarkdown = useCallback(
    async (file: DrawerFileItem) => {
      if (file.status !== "ready") {
        message.warning("This file is not ready yet.");
        return;
      }
      let timeline: FileTimelineResponse | null = timelineCache[file.id] ?? null;
      try {
        if (!timeline) {
          const resp = await getFileTimeline(detailMeeting.id, file.id);
          timeline = resp.data;
          setTimelineCache((prev) => ({ ...prev, [file.id]: timeline! }));
        }
      } catch {
        timeline = null;
        message.warning("Downloaded summary-only markdown because details could not be loaded.");
      }

      const bundle = buildPerFileMarkdownBundle({
        meetingTitle: detailMeeting.title,
        meetingId: detailMeeting.id,
        file,
        timeline,
        resolveAssetUrl: getMeetingAssetUrl,
        resolveFileUrl: getMeetingFileUrl,
      });
      const markdownFileName = `${sanitizeMarkdownFileName(file.file_name)}.md`;
      const rewritten = rewriteMeetingAssetLinks(bundle.markdown, {
        assetDir: "linked-assets",
        resolveAssetUrl: getMeetingAssetUrl,
      });
      const allAssets = [...bundle.assets];
      const seenAsset = new Set(
        allAssets.map((asset) => `${asset.sourceUrl}::${asset.archivePath}`),
      );
      for (const asset of rewritten.assets) {
        const key = `${asset.sourceUrl}::${asset.archivePath}`;
        if (seenAsset.has(key)) continue;
        seenAsset.add(key);
        allAssets.push(asset);
      }
      const { blob: zipBlob, failedAssets } = await createMarkdownZipBlob(
        markdownFileName,
        rewritten.markdown,
        allAssets,
      );
      triggerBlobDownload(`${sanitizeMarkdownFileName(file.file_name)}.zip`, zipBlob);
      if (failedAssets.length > 0) {
        message.warning(`Downloaded ZIP, but ${failedAssets.length} image(s) failed to include.`);
      }
    },
    [detailMeeting.id, detailMeeting.title, timelineCache],
  );

  const handleOpenDocumentViewer = useCallback(
    (file: DrawerFileItem) => {
      const kind = file.file_type;
      if (kind === "pdf") {
        if (!timelineCache[file.id]) {
          void fetchFileTimeline(file.id).then(() => setPdfModal({ fileId: file.id }));
        } else {
          setPdfModal({ fileId: file.id });
        }
      } else {
        setDocModal({ fileId: file.id });
        if (!timelineCache[file.id] && !timelineLoading[file.id]) {
          void fetchFileTimeline(file.id);
        }
      }
    },
    [fetchFileTimeline, timelineCache, timelineLoading],
  );

  const handleClose = () => {
    setExpandedSummaryFileId(null);
    setTimelineError({});
    setPdfModal(null);
    setDocModal(null);
    onClose();
  };

  const files = ((detailFull?.files ?? []) as DrawerFileItem[]).map(
    (file) => semanticOverrides[file.id] ?? file,
  );

  const persistFileSemantics = useCallback(
    async (
      file: DrawerFileItem,
      update: {
        material_role?: MaterialRole;
        business_domain?: BusinessDomain;
        approval_status?: ApprovalStatus;
        approval_reason?: string | null;
      },
    ) => {
      try {
        const response = await updateMeetingFileSemantics(detailMeeting.id, file.id, update);
        const updated = response.data as unknown as DrawerFileItem;
        setSemanticOverrides((previous) => ({
          ...previous,
          [file.id]: { ...file, ...updated },
        }));
        message.success("Evidence semantics updated; retrieval metadata rebuild was queued.");
      } catch {
        message.error("Failed to update evidence semantics.");
      }
    },
    [detailMeeting.id],
  );

  const handleUpdateFileSemantics = useCallback(
    async (
      file: DrawerFileItem,
      update: {
        material_role?: MaterialRole;
        approval_status?: ApprovalStatus;
        business_domain?: BusinessDomain;
      },
    ) => {
      if (update.approval_status === "rejected") {
        setRejectionDraft({ file, reason: file.approval_reason ?? "" });
        return;
      }
      await persistFileSemantics(file, update);
    },
    [persistFileSemantics],
  );

  const handleOpenSemanticHistory = useCallback(
    async (file: DrawerFileItem) => {
      setSemanticHistory({ file, events: [], loading: true });
      try {
        const response = await getMeetingFileSemanticHistory(detailMeeting.id, file.id);
        setSemanticHistory({ file, events: response.data, loading: false });
      } catch {
        setSemanticHistory(null);
        message.error("Failed to load evidence review history.");
      }
    },
    [detailMeeting.id],
  );

  return (
    <Modal
      open={!!detailMeeting}
      onCancel={handleClose}
      footer={null}
      width={DETAIL_MODAL_WIDTH}
      style={{ top: 20 }}
      styles={{ body: { maxHeight: DETAIL_MODAL_BODY_MAX_HEIGHT, overflowY: "auto" } }}
      zIndex={1100}
      destroyOnHidden
      title={null}
    >
      <div style={{ padding: "8px 0" }}>
        <MeetingDetailHeader
          title={detailMeeting.title}
          createdAt={detailMeeting.created_at}
          status={detailMeeting.status}
          detailFileType={detailFileType}
          exporting={exporting}
          onEdit={onEdit}
          onExport={onExport}
        />

        {(detailMeeting.status === "failed" || detailMeeting.status === "error") &&
          detailMeeting.error_message && (
            <Alert
              message={detailMeeting.error_message}
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
            />
          )}

        <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
          <Button icon={<FileTextOutlined />} onClick={() => onGenerateSummary(detailMeeting.id)}>
            Summary
          </Button>
        </div>

        <details style={{ marginBottom: 20 }}>
          <summary style={{ cursor: "pointer", padding: 12 }}>
            {intl.formatMessage({ id: "materials.reviewActions" })}
          </summary>
          <MeetingReviewPanel key={detailMeeting.id} meetingId={detailMeeting.id} />
        </details>

        <MeetingFilesSection
          meetingId={detailMeeting.id}
          files={files}
          detailLoading={detailLoading}
          expandedSummaryFileId={expandedSummaryFileId}
          onSetExpandedSummaryFileId={setExpandedSummaryFileId}
          onViewTimestamps={onViewTimestamps}
          onOpenSpeakers={onOpenSpeakers}
          onReprocessFile={onReprocessFile}
          onDownloadFile={onDownloadFile}
          onDeleteFile={onDeleteFile}
          onOpenDocumentViewer={handleOpenDocumentViewer}
          onDownloadFileMarkdown={handleDownloadFileMarkdown}
          onUpdateFileSemantics={handleUpdateFileSemantics}
          onOpenSemanticHistory={(file) => void handleOpenSemanticHistory(file)}
        />
      </div>

      {pdfModal && (
        <PdfComparisonModal
          open={!!pdfModal}
          meetingId={detailMeeting.id}
          fileId={pdfModal.fileId}
          fileName={files.find((f) => f.id === pdfModal.fileId)?.file_name ?? "Document"}
          pages={(() => {
            const timeline = timelineCache[pdfModal.fileId];
            return timeline?.kind === "pages" ? (timeline.pages as PageItem[]) : [];
          })()}
          loading={!!timelineLoading[pdfModal.fileId]}
          onClose={() => setPdfModal(null)}
        />
      )}

      {docModal && (
        <MeetingDocumentModal
          meetingId={detailMeeting.id}
          fileName={files.find((f) => f.id === docModal.fileId)?.file_name ?? "Document Preview"}
          selectedFile={files.find((f) => f.id === docModal.fileId)}
          timeline={timelineCache[docModal.fileId]}
          loading={!!timelineLoading[docModal.fileId]}
          errorMessage={timelineError[docModal.fileId]}
          onClose={() => setDocModal(null)}
          onRetry={() => {
            void fetchFileTimeline(docModal.fileId);
          }}
          onReprocessFile={onReprocessFile}
        />
      )}

      <Modal
        open={!!rejectionDraft}
        title="Reject meeting evidence"
        okText="Reject and rebuild"
        okButtonProps={{ danger: true, disabled: !rejectionDraft?.reason.trim() }}
        onCancel={() => setRejectionDraft(null)}
        onOk={() => {
          if (!rejectionDraft?.reason.trim()) return;
          const { file, reason } = rejectionDraft;
          setRejectionDraft(null);
          void persistFileSemantics(file, {
            approval_status: "rejected",
            approval_reason: reason.trim(),
          });
        }}
      >
        <p>Rejected evidence is excluded from current answers and automatic memory extraction.</p>
        <Input.TextArea
          aria-label="Rejection reason"
          value={rejectionDraft?.reason ?? ""}
          placeholder="Explain why this material must not support current conclusions"
          rows={4}
          onChange={(event) =>
            setRejectionDraft((current) =>
              current ? { ...current, reason: event.target.value } : current,
            )
          }
        />
      </Modal>

      <Modal
        open={!!semanticHistory}
        title={`Evidence history · ${semanticHistory?.file.file_name ?? ""}`}
        footer={null}
        onCancel={() => setSemanticHistory(null)}
      >
        {semanticHistory?.loading ? (
          <Spin />
        ) : semanticHistory?.events.length ? (
          <Timeline
            items={semanticHistory.events.map((event) => ({
              children: (
                <div>
                  <div>
                    <Tag>revision {event.source_revision}</Tag>
                    <Tag>{event.business_domain}</Tag>
                    <Tag>{event.material_role}</Tag>
                    <Tag color={event.approval_status === "rejected" ? "red" : "default"}>
                      {event.approval_status}
                    </Tag>
                  </div>
                  <div>{formatLocalTime(event.changed_at)}</div>
                  {event.approval_reason && <div>{event.approval_reason}</div>}
                </div>
              ),
            }))}
          />
        ) : (
          <p>No review changes have been recorded yet.</p>
        )}
      </Modal>
    </Modal>
  );
}
