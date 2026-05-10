import { Form } from "antd";
import { motion } from "framer-motion";
import type { MeetingInfo } from "../api/client";
import { useUpload } from "../hooks/useUpload";
import { UploadPanelHeader } from "./upload/UploadPanelHeader";
import { UploadTargetFields } from "./upload/UploadTargetFields";
import { UploadDropzone } from "./upload/UploadDropzone";
import { UploadSubmitButton } from "./upload/UploadSubmitButton";
import { UploadFeedback } from "./upload/UploadFeedback";

interface Props {
  onSuccess: () => void;
  meetings?: MeetingInfo[];
  mode?: "new" | "existing";
}

export default function UploadPanel({ onSuccess, meetings = [], mode = "new" }: Props) {
  const {
    form,
    modalContextHolder,
    uploading,
    success,
    currentFileIndex,
    uploadError,
    retryInfo,
    fileList,
    previewUrls,
    existingMeetingId,
    existingMeetingOptions,
    handleUpload,
    handleAddFile,
    handleRemoveFile,
    setExistingMeetingId,
  } = useUpload({ mode, meetings, onSuccess });

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      style={{
        background: "var(--color-bg-surface)",
        borderRadius: 16,
        border: "1px solid var(--color-border)",
        padding: "20px",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      {modalContextHolder}
      <UploadPanelHeader />

      <Form form={form} layout="vertical" size="middle" style={{ marginBottom: 0 }}>
        <UploadTargetFields
          mode={mode}
          existingMeetingId={existingMeetingId}
          onExistingMeetingChange={setExistingMeetingId}
          existingMeetingOptions={existingMeetingOptions}
        />

        <UploadDropzone
          fileList={fileList}
          uploading={uploading}
          currentFileIndex={currentFileIndex}
          previewByUid={previewUrls}
          onAddFile={handleAddFile}
          onRemoveFile={handleRemoveFile}
        />

        <UploadFeedback
          uploading={uploading}
          currentFileIndex={currentFileIndex}
          fileCount={fileList.length}
          uploadError={uploadError}
          success={success}
          retryInfo={retryInfo}
        />

        <UploadSubmitButton
          uploading={uploading}
          currentFileIndex={currentFileIndex}
          fileCount={fileList.length}
          mode={mode}
          onUpload={handleUpload}
        />
      </Form>
    </motion.div>
  );
}
