import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { message, Modal } from "antd";
import { getFileTimeline, getMeeting, formatApiErrorMessage } from "../api/client";
import MaterialViewer from "../components/MaterialViewer";
import TranscriptViewer from "../components/materials/TranscriptViewer";
import { getKindCapabilities } from "../types/fileKinds";

export interface ViewerRequest {
  meetingId: number;
  fileId: number;
  fileName: string;
  fileType: string;
  seekTo?: number;
  page?: number;
  meetingTitle?: string;
}

interface ViewerContextValue {
  openViewer: (req: ViewerRequest) => void;
  closeViewer: () => void;
}

const ViewerContext = createContext<ViewerContextValue | null>(null);
const UNNAMED_SPEAKER_CODE_PATTERN = /^[A-Z]$|^SPEAKER[_\s-]?\d+$/i;

// M-FE-3: Dedup concurrent fetches to the same meetingId+fileId combination.
const _fetchDedup = new Map<string, Promise<unknown>>();

function _dedupedFetch<T>(key: string, factory: () => Promise<T>): Promise<T> {
  const existing = _fetchDedup.get(key);
  if (existing) return existing as Promise<T>;
  const promise = factory().finally(() => {
    _fetchDedup.delete(key);
  });
  _fetchDedup.set(key, promise);
  return promise;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useViewer(): ViewerContextValue {
  const ctx = useContext(ViewerContext);
  if (!ctx) throw new Error("useViewer must be used within ViewerProvider");
  return ctx;
}

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [materialRequest, setMaterialRequest] = useState<ViewerRequest | null>(null);
  const [transcriptRequest, setTranscriptRequest] = useState<ViewerRequest | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptSegments, setTranscriptSegments] = useState<
    Array<{ start: number; end: number; text: string; speaker?: string | null }>
  >([]);
  const [transcriptSeekTo, setTranscriptSeekTo] = useState<number | undefined>(undefined);
  const [activeSegmentIndex, setActiveSegmentIndex] = useState<number | null>(null);
  const [transcriptPlayback, setTranscriptPlayback] = useState<{
    meetingId: number;
    fileId: number;
    fileName: string;
    fileType: string;
  } | null>(null);
  const transcriptListRef = useRef<HTMLDivElement>(null);

  const openViewer = useCallback((req: ViewerRequest) => {
    if (req.seekTo != null) {
      setMaterialRequest(null);
      setTranscriptRequest(req);
      setTranscriptLoading(true);
      setTranscriptSegments([]);
      setTranscriptPlayback(null);
      setTranscriptSeekTo(req.seekTo);
      setActiveSegmentIndex(null);
      return;
    }
    setTranscriptRequest(null);
    setMaterialRequest(req);
  }, []);

  const closeViewer = useCallback(() => {
    setMaterialRequest(null);
    setTranscriptRequest(null);
    setTranscriptLoading(false);
    setTranscriptSegments([]);
    setTranscriptPlayback(null);
    setTranscriptSeekTo(undefined);
    setActiveSegmentIndex(null);
  }, []);

  const closeTranscriptViewer = useCallback(() => {
    setTranscriptRequest(null);
    setTranscriptLoading(false);
    setTranscriptSegments([]);
    setTranscriptPlayback(null);
    setTranscriptSeekTo(undefined);
    setActiveSegmentIndex(null);
  }, []);

  const isUnnamedSpeaker = useCallback((speaker?: string | null) => {
    if (!speaker) return false;
    return UNNAMED_SPEAKER_CODE_PATTERN.test(speaker.trim());
  }, []);

  useEffect(() => {
    if (!transcriptRequest) return;

    const { meetingId, fileId } = transcriptRequest;
    if (!Number.isFinite(meetingId) || meetingId <= 0 || !Number.isFinite(fileId) || fileId <= 0) {
      message.warning("Unable to locate the source file");
      queueMicrotask(() => {
        setTranscriptLoading(false);
      });
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    const loadTimestamps = async () => {
      setTranscriptLoading(true);
      try {
        const dedupKey = `timeline:${meetingId}:${fileId}`;
        const [tsRes, detailRes] = await _dedupedFetch(dedupKey, () =>
          Promise.all([
            getFileTimeline(meetingId, fileId, { signal: controller.signal }),
            getMeeting(meetingId, { signal: controller.signal }),
          ]),
        );
        if (cancelled) return;

        const requestedPlayable = detailRes.data.files.find(
          (file) =>
            file.id === fileId &&
            getKindCapabilities(file.file_type).hasTimeline &&
            file.status === "ready",
        );
        const fallbackPlayable = detailRes.data.files.find(
          (file) => getKindCapabilities(file.file_type).hasTimeline && file.status === "ready",
        );
        const playableFile = requestedPlayable ?? fallbackPlayable;

        setTranscriptPlayback(
          playableFile
            ? {
                meetingId,
                fileId: playableFile.id,
                fileName: playableFile.file_name,
                fileType: playableFile.file_type,
              }
            : null,
        );
        setTranscriptSegments(tsRes.data.kind === "segments" ? tsRes.data.segments : []);
      } catch (err: unknown) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 404 || status === 410) {
          message.warning("Source file no longer available");
          setTranscriptSegments([]);
          setTranscriptPlayback(null);
          return;
        }
        message.error(formatApiErrorMessage(err, "Failed to load timestamps"));
        setTranscriptSegments([]);
        setTranscriptPlayback(null);
      } finally {
        if (!cancelled) setTranscriptLoading(false);
      }
    };

    void loadTimestamps();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [transcriptRequest]);

  useEffect(() => {
    if (activeSegmentIndex == null || !transcriptListRef.current) return;
    const node = transcriptListRef.current.querySelector<HTMLElement>(
      `[data-segment-index="${activeSegmentIndex}"]`,
    );
    node?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeSegmentIndex]);

  const contextValue = useMemo<ViewerContextValue>(
    () => ({ openViewer, closeViewer }),
    [openViewer, closeViewer],
  );

  return (
    <ViewerContext.Provider value={contextValue}>
      {children}
      <Modal
        open={materialRequest !== null}
        centered
        onCancel={closeViewer}
        footer={null}
        width="min(96vw, 1400px)"
        zIndex={1100}
        title={
          materialRequest
            ? `${materialRequest.meetingTitle ? `${materialRequest.meetingTitle} · ` : ""}${materialRequest.fileName || "File"}`
            : null
        }
        destroyOnHidden
        styles={{
          body: {
            padding: 0,
            height: "calc(100vh - 120px)",
            overflow: "hidden",
          },
        }}
      >
        {materialRequest && <MaterialViewer request={materialRequest} />}
      </Modal>
      <TranscriptViewer
        open={transcriptRequest !== null}
        loading={transcriptLoading}
        segments={transcriptSegments}
        playback={transcriptPlayback}
        seekTo={transcriptSeekTo}
        activeSegmentIndex={activeSegmentIndex}
        listRef={transcriptListRef}
        isUnnamedSpeaker={isUnnamedSpeaker}
        onSeek={setTranscriptSeekTo}
        onActiveSegmentChange={setActiveSegmentIndex}
        onClose={closeTranscriptViewer}
      />
    </ViewerContext.Provider>
  );
}
