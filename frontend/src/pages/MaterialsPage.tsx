import { useState, useCallback, useEffect } from "react";
import { Button, Modal, Divider, message } from "antd";
import { motion } from "framer-motion";
import UploadPanel from "../components/UploadPanel";
import { SearchToolbar, MaterialsContent } from "../components/materials/MaterialsContent";
import MeetingDetailDrawer from "../components/materials/MeetingDetailDrawer";
import TranscriptViewer from "../components/materials/TranscriptViewer";
import MeetingFormModal from "../components/materials/MeetingFormModal";
import SummaryModal from "../components/materials/SummaryModal";
import SpeakerModal from "../components/materials/SpeakerModal";
import { useMeetings } from "../hooks/useMeetings";
import { useFilteredMeetings } from "../hooks/useFilteredMeetings";
import { useSelection } from "../hooks/useSelection";
import { useLocalStorage } from "../hooks/useLocalStorage";
import { useMeetingDetail } from "../hooks/useMeetingDetail";
import { useViewer } from "../contexts/ViewerContext";
import { useSearchParams } from "react-router-dom";
import { parseEvidenceViewerCoordinates } from "../utils/evidenceNavigation";
import { getMeeting, isRequestCanceled, formatApiErrorMessage } from "../api/client";
import { useIntl } from "react-intl";

export default function MaterialsPage() {
  const { formatMessage } = useIntl();
  type UploadMode = "new" | "existing";
  const { meetings, loading, fetchMeetings, deleteMeetings } = useMeetings();
  const {
    filteredMeetings,
    searchQuery,
    setSearchQuery,
    sortField,
    sortOrder,
    toggleSortOrder,
    isSearching,
  } = useFilteredMeetings(meetings);
  const {
    selectedIds,
    isSelectionMode,
    toggleSelection,
    selectAll,
    clearSelection,
    exitSelectionMode,
    selectedCount,
  } = useSelection(filteredMeetings.map((m) => m.id));

  const [viewMode, setViewMode] = useLocalStorage<"grid" | "list">("materials-view-mode", "grid");
  const [uploadModalVisible, setUploadModalVisible] = useState(false);
  const [uploadMode, setUploadMode] = useState<UploadMode>("new");

  const [editModal, setEditModal] = useState<{
    id: number;
    title: string;
    description: string;
    meeting_date: string;
  } | null>(null);

  const detail = useMeetingDetail(fetchMeetings);
  const { handleOpenDetail } = detail;
  const { openViewer } = useViewer();
  const [searchParams, setSearchParams] = useSearchParams();
  const sourceQuery = searchParams.toString();

  useEffect(() => {
    const params = new URLSearchParams(sourceQuery);
    if (!params.has("meetingId")) return;
    const meetingId = Number(params.get("meetingId"));
    const fileId = Number(params.get("fileId"));
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const openSource = async () => {
      try {
        if (!Number.isInteger(meetingId) || meetingId <= 0)
          throw new Error(formatMessage({ id: "viewer.sourceUnavailable" }));
        const { data: meeting } = await getMeeting(meetingId, options);
        if (controller.signal.aborted) return;
        if (!params.has("fileId")) {
          await handleOpenDetail(meeting);
          return;
        }
        const file = meeting.files.find((item) => item.id === fileId);
        if (!file) throw new Error(formatMessage({ id: "viewer.sourceUnavailable" }));
        const coordinates = parseEvidenceViewerCoordinates(params);
        if (controller.signal.aborted) return;
        openViewer({
          ...coordinates,
          page:
            params.has("pageNumber") || params.has("slideNumber") ? coordinates.page : undefined,
          meetingId,
          fileId: file.id,
          fileName: file.file_name,
          fileType: file.file_type ?? "unknown",
          meetingTitle: meeting.title,
        });
      } catch (error) {
        if (!controller.signal.aborted && !isRequestCanceled(error)) {
          message.error(
            formatApiErrorMessage(error, formatMessage({ id: "viewer.sourceUnavailable" })),
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          for (const key of [
            "meetingId",
            "fileId",
            "pageNumber",
            "slideNumber",
            "timestampStart",
            "timestampEnd",
            "sourceRevision",
            "chunkIndex",
            "windowStart",
            "windowEnd",
            "evidenceExcerpt",
          ])
            params.delete(key);
          setSearchParams(params, { replace: true });
        }
      }
    };
    void openSource();
    return () => controller.abort();
  }, [sourceQuery, handleOpenDetail, openViewer, formatMessage, setSearchParams]);

  const handleUploadSuccess = useCallback(() => {
    setUploadModalVisible(false);
    window.dispatchEvent(new CustomEvent("meeting-uploaded"));
    fetchMeetings();
  }, [fetchMeetings]);

  const handleOpenNewMeetingUpload = useCallback(() => {
    setUploadMode("new");
    setUploadModalVisible(true);
  }, []);

  const handleOpenExistingMeetingUpload = useCallback(() => {
    setUploadMode("existing");
    setUploadModalVisible(true);
  }, []);

  const handleDeleteSingle = useCallback((id: number) => deleteMeetings([id]), [deleteMeetings]);

  const handleDeleteSelected = useCallback(async () => {
    const ids = Array.from(selectedIds);
    const success = await deleteMeetings(ids);
    if (success) exitSelectionMode();
  }, [selectedIds, deleteMeetings, exitSelectionMode]);

  return (
    <div style={{ padding: "12px 16px 24px", maxWidth: 1400, margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          marginBottom: 16,
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <SearchToolbar
          searchQuery={searchQuery}
          onSearchQueryChange={(e) => setSearchQuery(e.target.value)}
          isSearching={isSearching}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          sortField={sortField}
          sortOrder={sortOrder}
          onToggleSortOrder={toggleSortOrder}
          loading={loading}
          onRefresh={fetchMeetings}
          onNewMeeting={handleOpenNewMeetingUpload}
          onAddToExisting={handleOpenExistingMeetingUpload}
        />
      </div>

      {/* Selection toolbar */}
      {isSelectionMode && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 16px",
            background: "var(--color-primary-alpha, rgba(79, 70, 229, 0.08))",
            borderRadius: 12,
            marginBottom: 16,
          }}
        >
          <span style={{ fontWeight: 600 }}>{selectedCount} selected</span>
          <Button size="small" onClick={selectAll}>
            Select All
          </Button>
          <Button size="small" onClick={clearSelection}>
            Clear
          </Button>
          <Button size="small" danger onClick={handleDeleteSelected} disabled={selectedCount === 0}>
            Delete
          </Button>
          <Button size="small" onClick={exitSelectionMode}>
            Cancel
          </Button>
        </motion.div>
      )}

      <Divider style={{ margin: "16px 0" }} />

      {/* Content */}
      <MaterialsContent
        loading={loading}
        hasMeetings={meetings.length > 0}
        filteredMeetings={filteredMeetings}
        viewMode={viewMode}
        selectedIds={selectedIds}
        isSelectionMode={isSelectionMode}
        isSearching={isSearching}
        searchQuery={searchQuery}
        onOpenDetail={detail.handleOpenDetail}
        onToggleSelect={toggleSelection}
        onDelete={handleDeleteSingle}
        onReprocess={detail.handleReprocess}
      />

      {/* Upload Modal */}
      <Modal
        open={uploadModalVisible}
        // Keep hit targets stationary while the modal appears. The default
        // origin-based zoom can move Close between pointer-down/up in WebKit.
        transitionName="ant-fade"
        onCancel={() => setUploadModalVisible(false)}
        footer={null}
        width="min(96vw, 760px)"
        style={{ top: 20 }}
        styles={{ body: { maxHeight: "calc(100vh - 170px)", overflowY: "auto" } }}
        zIndex={1000}
        destroyOnHidden
      >
        <UploadPanel onSuccess={handleUploadSuccess} meetings={meetings} mode={uploadMode} />
      </Modal>

      {/* Detail Drawer */}
      {detail.detailMeeting && (
        <MeetingDetailDrawer
          detailMeeting={detail.detailMeeting}
          detailFull={detail.detailFull}
          detailLoading={detail.detailLoading}
          detailFileType={detail.detailFileType}
          exporting={detail.exporting}
          onClose={detail.handleCloseDetail}
          onEdit={() =>
            setEditModal({
              id: detail.detailMeeting!.id,
              title: detail.detailMeeting!.title,
              description: detail.detailMeeting!.description || "",
              meeting_date: detail.detailMeeting!.meeting_date || "",
            })
          }
          onExport={(format) => detail.handleExport(detail.detailMeeting!.id, format)}
          onViewTimestamps={detail.handleViewTimestamps}
          onGenerateSummary={detail.handleGenerateSummary}
          onDownloadFile={detail.handleDownloadFile}
          onDeleteFile={detail.handleDeleteFile}
          onOpenSpeakers={(meetingId, fileId) => detail.handleOpenSpeakers(meetingId, fileId)}
          onReprocessFile={detail.handleReprocessFile}
        />
      )}

      <TranscriptViewer
        open={detail.timestampsOpen}
        loading={detail.timestampsLoading}
        segments={detail.timestampsData}
        playback={detail.timestampsPlayback}
        seekTo={detail.timestampsSeekTo}
        seekEnd={undefined}
        activeSegmentIndex={detail.activeSegmentIndex}
        listRef={detail.timestampsListRef}
        isUnnamedSpeaker={detail.isUnnamedSpeaker}
        onSeek={detail.setTimestampsSeekTo}
        onActiveSegmentChange={detail.setActiveSegmentIndex}
        onClose={() => {
          detail.setTimestampsOpen(false);
          detail.setActiveSegmentIndex(null);
          detail.setTimestampsSeekTo(undefined);
        }}
      />

      <SummaryModal
        open={detail.summaryOpen}
        loading={detail.summaryLoading}
        data={detail.summaryData}
        streaming={detail.summaryStreaming}
        files={(detail.detailFull?.files ?? []) as { id: number; file_name: string }[]}
        onCopy={detail.handleCopySummary}
        onDownload={detail.handleDownloadSummary}
        onRegenerate={() => {
          if (detail.summaryMeetingId != null) {
            detail.handleRegenerateSummary(detail.summaryMeetingId);
          }
        }}
        onNavigateToFile={(fileId) => {
          // Try to find the file in the current meeting detail to open viewer
          const files = detail.detailFull?.files ?? [];
          const file = files.find((f) => f.id === fileId);
          if (file && detail.detailFull) {
            openViewer({
              meetingId: detail.detailFull.id,
              fileId: file.id,
              fileName: file.file_name,
              fileType: file.file_type ?? "unknown",
              page: 1,
              meetingTitle: detail.detailFull.title,
            });
          } else {
            // Fallback: scroll to the file card in the meeting detail
            const el = document.querySelector(`[data-meeting-file="${fileId}"]`);
            el?.scrollIntoView({ behavior: "smooth", block: "center" });
          }
        }}
        onClose={() => {
          detail.setSummaryOpen(false);
          detail.summaryAbortRef.current?.abort();
        }}
      />

      <MeetingFormModal
        open={!!editModal}
        data={editModal}
        onChange={setEditModal}
        onSave={(data) => detail.handleUpdateMeeting(data.id, data, setEditModal)}
        onCancel={() => setEditModal(null)}
      />

      <SpeakerModal
        open={detail.speakerModalOpen}
        loading={detail.speakerLoading}
        data={detail.speakerData}
        names={detail.speakerNames}
        playing={detail.speakerPlaying}
        saving={detail.speakerSaving}
        meetingId={detail.speakerMeetingId}
        onNamesChange={detail.setSpeakerNames}
        onPlay={detail.handlePlaySpeaker}
        onSave={detail.handleSaveSpeakers}
        onClose={() => detail.setSpeakerModalOpen(false)}
        onStopAll={() => {
          detail.stopAudio();
        }}
      />
    </div>
  );
}
