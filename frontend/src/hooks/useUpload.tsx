import { useReducer, useRef, useEffect, useMemo, useState } from "react";
import { Form, Modal, message } from "antd";
import type { UploadFile } from "antd";
import type { RcFile } from "antd/es/upload/interface";
import { useIntl } from "react-intl";
import {
  ApiError,
  createMeeting,
  uploadMeeting,
  deleteMeeting,
  formatApiErrorMessage,
  type MeetingInfo,
} from "../api/client";
import { reportNonCriticalError } from "../utils/monitoring";
import { ALLOWED_UPLOAD_EXTENSIONS } from "../constants";
import {
  MAX_FILE_SIZE_BYTES,
  MAX_FILES,
  createPreviewUrl,
  getPreviewMode,
  revokePreviewUrl,
} from "../components/upload/fileUtils";
import type { RetryInfo } from "../components/upload/UploadFeedback";

// ---------------------------------------------------------------------------
// Upload lifecycle state (uploading, success, progress, error, retry)
// ---------------------------------------------------------------------------

interface UploadState {
  uploading: boolean;
  success: boolean;
  currentFileIndex: number;
  uploadError: string | null;
  retryInfo: RetryInfo | null;
}

type UploadAction =
  | { type: "START_UPLOAD" }
  | { type: "FINISH_SUCCESS" }
  | { type: "FINISH_PARTIAL"; error: string }
  | { type: "FINISH_FAILURE"; error: string }
  | { type: "SET_FILE_INDEX"; index: number }
  | { type: "SET_RETRY_INFO"; info: RetryInfo | null }
  | { type: "CLEAR_ERROR" }
  | { type: "RESET_PROGRESS" };

const INITIAL_UPLOAD_STATE: UploadState = {
  uploading: false,
  success: false,
  currentFileIndex: 0,
  uploadError: null,
  retryInfo: null,
};

function uploadReducer(state: UploadState, action: UploadAction): UploadState {
  switch (action.type) {
    case "START_UPLOAD":
      return { ...state, uploading: true, success: false, currentFileIndex: 0, uploadError: null };
    case "FINISH_SUCCESS":
      return { ...state, success: true };
    case "FINISH_PARTIAL":
      return { ...state, uploadError: action.error };
    case "FINISH_FAILURE":
      return { ...state, success: false, uploadError: action.error };
    case "SET_FILE_INDEX":
      return { ...state, currentFileIndex: action.index };
    case "SET_RETRY_INFO":
      return { ...state, retryInfo: action.info };
    case "CLEAR_ERROR":
      return { ...state, uploadError: null };
    case "RESET_PROGRESS":
      return { ...INITIAL_UPLOAD_STATE };
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Panels state (file list, previews, meeting selection)
// ---------------------------------------------------------------------------

interface PanelsState {
  fileList: UploadFile[];
  previewUrls: Record<string, string>;
  existingMeetingId: number | null;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

interface UseUploadOptions {
  mode: "new" | "existing";
  meetings: MeetingInfo[];
  onSuccess: () => void;
}

export function useUpload({ mode, meetings, onSuccess }: UseUploadOptions) {
  const { formatMessage } = useIntl();
  const [form] = Form.useForm();
  const [modal, modalContextHolder] = Modal.useModal();

  const [uploadState, dispatch] = useReducer(uploadReducer, INITIAL_UPLOAD_STATE);
  const { uploading, success, currentFileIndex, uploadError, retryInfo } = uploadState;

  const [panels, setPanels] = useState<PanelsState>({
    fileList: [],
    previewUrls: {},
    existingMeetingId: null,
  });
  const { fileList, previewUrls, existingMeetingId } = panels;
  const previewUrlsRef = useRef<Record<string, string>>({});
  const abortControllerRef = useRef<AbortController | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!success) return;
    onSuccess();
    dispatch({ type: "RESET_PROGRESS" });
  }, [success, onSuccess]);

  useEffect(() => {
    previewUrlsRef.current = previewUrls;
  }, [previewUrls]);

  useEffect(() => {
    return () => {
      Object.values(previewUrlsRef.current).forEach((url) => revokePreviewUrl(url));
      if (retryTimerRef.current) {
        clearInterval(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      abortControllerRef.current?.abort();
    };
  }, []);

  const existingMeetingOptions = useMemo(
    () =>
      meetings
        .map((m) => ({
          value: m.id,
          label: `${m.title} (#${m.id})`,
        }))
        .sort((a, b) => b.value - a.value),
    [meetings],
  );

  const handleUpload = async () => {
    if (!fileList.length) return message.warning(formatMessage({ id: "upload.selectFile" }));

    const title = form.getFieldValue("title");
    if (mode === "new" && !title)
      return message.warning(formatMessage({ id: "upload.enterTitle" }));
    if (mode === "existing" && !existingMeetingId) {
      return message.warning(formatMessage({ id: "upload.selectMeeting" }));
    }

    dispatch({ type: "START_UPLOAD" });

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    const description = form.getFieldValue("description");
    const totalFiles = fileList.length;
    let meetingId: number | null = null;
    let createdNewMeeting = false;

    try {
      if (mode === "new") {
        message.loading({
          content: formatMessage({ id: "upload.creating" }),
          key: "create-meeting",
        });
        const meetingRes = await createMeeting(title, description);
        if (!meetingRes.data) throw new Error(formatMessage({ id: "upload.noData" }));
        meetingId = meetingRes.data.meeting_id;
        createdNewMeeting = true;
        message.success({
          content: formatMessage({ id: "upload.created" }),
          key: "create-meeting",
        });
      } else {
        meetingId = existingMeetingId;
      }

      if (!meetingId) throw new Error(formatMessage({ id: "upload.noTargetMeeting" }));

      let skippedCount = 0;
      const failedFiles: string[] = [];
      for (let i = 0; i < fileList.length; i++) {
        if (abortController.signal.aborted) break;
        dispatch({ type: "SET_FILE_INDEX", index: i });
        const file = fileList[i].originFileObj as File | undefined;
        if (!file) {
          skippedCount++;
          continue;
        }

        const RETRY_DELAYS = [15, 30, 60];
        const MAX_RETRIES = RETRY_DELAYS.length;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
          if (abortController.signal.aborted) break;
          try {
            await uploadMeeting(file, {
              meetingId,
              signal: abortController.signal,
              businessDomain: form.getFieldValue("businessDomain") ?? "unspecified",
              materialRole: form.getFieldValue("materialRole"),
            });
            break;
          } catch (err) {
            if (abortController.signal.aborted) break;
            if (err instanceof ApiError && err.status === 429 && attempt < MAX_RETRIES) {
              const delay = RETRY_DELAYS[attempt];
              dispatch({
                type: "SET_RETRY_INFO",
                info: {
                  fileName: file.name,
                  countdown: delay,
                  attempt: attempt + 1,
                  maxAttempts: MAX_RETRIES,
                },
              });
              await new Promise<void>((resolve) => {
                if (retryTimerRef.current) {
                  clearInterval(retryTimerRef.current);
                  retryTimerRef.current = null;
                }
                let remaining = delay;
                const timer = setInterval(() => {
                  if (abortController.signal.aborted) {
                    clearInterval(timer);
                    retryTimerRef.current = null;
                    resolve();
                    return;
                  }
                  remaining--;
                  dispatch({
                    type: "SET_RETRY_INFO",
                    info: {
                      fileName: file.name,
                      countdown: remaining,
                      attempt: attempt + 1,
                      maxAttempts: MAX_RETRIES,
                    },
                  });
                  if (remaining <= 0) {
                    clearInterval(timer);
                    retryTimerRef.current = null;
                    dispatch({ type: "SET_RETRY_INFO", info: null });
                    resolve();
                  }
                }, 1000);
                retryTimerRef.current = timer;
              });
            } else {
              const msg = formatApiErrorMessage(err, formatMessage({ id: "upload.uploadFailed" }));
              failedFiles.push(`${file.name}: ${msg}`);
              break;
            }
          }
        }
      }
      const successCount = totalFiles - skippedCount - failedFiles.length;
      const hasSuccessfulUploads = successCount > 0;
      const uploadErrors: string[] = [];
      if (skippedCount > 0) {
        uploadErrors.push(
          `${skippedCount} file(s) were skipped before upload. Please re-select the files and retry.`,
        );
        modal.warning({
          width: "min(92vw, 680px)",
          title: formatMessage({ id: "upload.filesSkipped" }),
          content: formatMessage({ id: "upload.filesSkippedContent" }, { count: skippedCount }),
        });
      }
      if (failedFiles.length > 0) {
        const detail = failedFiles.join("\n");
        uploadErrors.push(detail);
        modal.error({
          width: "min(92vw, 760px)",
          title: formatMessage({ id: "upload.filesFailedTitle" }, { count: failedFiles.length }),
          content: (
            <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{detail}</pre>
          ),
        });
      }
      if (uploadErrors.length > 0) {
        dispatch({ type: "FINISH_PARTIAL", error: uploadErrors.join("\n\n") });
      }
      if (hasSuccessfulUploads) {
        dispatch({ type: "FINISH_SUCCESS" });
        message.success(formatMessage({ id: "upload.uploadSuccess" }, { count: successCount }));
      } else {
        if (createdNewMeeting && meetingId !== null) {
          try {
            await deleteMeeting(meetingId);
          } catch (err) {
            reportNonCriticalError("cleanup failed meeting after upload failure", err, {
              meetingId,
            });
          }
        }
        if (skippedCount === 0 && failedFiles.length === 0) {
          dispatch({ type: "FINISH_FAILURE", error: formatMessage({ id: "upload.allFailed" }) });
          modal.error({
            width: "min(92vw, 680px)",
            title: formatMessage({ id: "upload.uploadFailed" }),
            content: formatMessage({ id: "upload.allFailed" }),
          });
        }
      }
    } catch (err: unknown) {
      if (createdNewMeeting && meetingId !== null) {
        deleteMeeting(meetingId).catch((cleanupErr: unknown) => {
          reportNonCriticalError("cleanup orphan meeting in upload catch", cleanupErr, {
            meetingId,
          });
        });
      }
      const errorMsg = formatApiErrorMessage(
        err,
        formatMessage({ id: "upload.uploadFailedRetry" }),
      );
      dispatch({ type: "FINISH_FAILURE", error: errorMsg });
      modal.error({
        width: "min(92vw, 680px)",
        title: formatMessage({ id: "upload.uploadFailed" }),
        content: errorMsg,
      });
    } finally {
      abortControllerRef.current = null;
      setTimeout(() => {
        dispatch({ type: "RESET_PROGRESS" });
      }, 400);
    }
  };

  const handleAddFile = (file: RcFile) => {
    if (file.size > MAX_FILE_SIZE_BYTES) {
      message.error(formatMessage({ id: "upload.fileTooLarge" }, { name: file.name }));
      return;
    }
    if (fileList.length >= MAX_FILES) {
      message.warning(formatMessage({ id: "upload.maxFiles" }, { count: MAX_FILES }));
      return;
    }
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!ALLOWED_UPLOAD_EXTENSIONS.has(ext)) {
      message.error(formatMessage({ id: "upload.unsupportedFormat" }, { name: file.name }));
      return;
    }
    const uid =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const previewMode = getPreviewMode(file.name);
    const previewUrl = previewMode ? createPreviewUrl(file) : null;
    setPanels((prev) => ({
      ...prev,
      fileList: [
        ...prev.fileList,
        {
          uid,
          name: file.name,
          status: "done",
          originFileObj: file,
        },
      ],
      previewUrls: previewUrl ? { ...prev.previewUrls, [uid]: previewUrl } : prev.previewUrls,
    }));
  };

  const handleRemoveFile = (uid: string) => {
    const existing = previewUrls[uid];
    revokePreviewUrl(existing);
    setPanels((prev) => {
      const nextPreviews = { ...prev.previewUrls };
      delete nextPreviews[uid];
      return {
        ...prev,
        fileList: prev.fileList.filter((f) => f.uid !== uid),
        previewUrls: nextPreviews,
      };
    });
  };

  return {
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
    setExistingMeetingId: (id: number | null) =>
      setPanels((prev) => ({ ...prev, existingMeetingId: id })),
  };
}
