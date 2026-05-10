import { useState, useEffect, useCallback, useMemo } from "react";
import { message } from "antd";
import {
  listMeetings,
  getMeetingFiles,
  formatApiErrorMessage,
  type MeetingInfo,
  type MeetingFileInfo,
} from "../api/client";
import { reportNonCriticalError } from "../utils/monitoring";

export function useSessionSelection() {
  const [selectedMeetingIds, setSelectedMeetingIds] = useState<number[]>([]);
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);
  const [meetings, setMeetings] = useState<MeetingInfo[]>([]);
  const [meetingFilesMap, setMeetingFilesMap] = useState<Record<number, MeetingFileInfo[]>>({});
  const [loadingMeetings, setLoadingMeetings] = useState(true);
  const [loadingFiles, setLoadingFiles] = useState(false);

  const refreshMeetings = useCallback(() => {
    setLoadingMeetings(true);
    listMeetings({ limit: 50, status: "ready" })
      .then((res: { data: { meetings: MeetingInfo[] } }) => {
        if (res?.data?.meetings) setMeetings(res.data.meetings);
      })
      .catch((err: unknown) => {
        message.error(formatApiErrorMessage(err, "Failed to load meetings"));
      })
      .finally(() => setLoadingMeetings(false));
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      refreshMeetings();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refreshMeetings]);

  useEffect(() => {
    const onFocus = () => refreshMeetings();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshMeetings();
    };
    const onMeetingUploaded = () => refreshMeetings();

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("meeting-uploaded", onMeetingUploaded);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("meeting-uploaded", onMeetingUploaded);
    };
  }, [refreshMeetings]);

  useEffect(() => {
    if (selectedMeetingIds.length === 0) {
      queueMicrotask(() => {
        setMeetingFilesMap({});
        setSelectedFileIds([]);
      });
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      setLoadingFiles(true);
      Promise.allSettled(
        selectedMeetingIds.map(async (meetingId) => {
          const response = await getMeetingFiles(meetingId);
          return [meetingId, response.data] as const;
        }),
      )
        .then((results) => {
          if (cancelled) return;
          const nextMap: Record<number, MeetingFileInfo[]> = {};
          for (const result of results) {
            if (result.status === "fulfilled") {
              const [meetingId, files] = result.value;
              nextMap[meetingId] = files;
            } else {
              reportNonCriticalError("Failed to load meeting files", result.reason);
            }
          }
          setMeetingFilesMap(nextMap);
          const validFileIds = new Set(
            Object.values(nextMap)
              .flat()
              .map((file) => file.id),
          );
          setSelectedFileIds((prev) => prev.filter((id) => validFileIds.has(id)));
        })
        .finally(() => {
          if (!cancelled) setLoadingFiles(false);
        });
    }, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [selectedMeetingIds]);

  const removeSelectedMeeting = useCallback((meetingId: number) => {
    setSelectedMeetingIds((prev) => prev.filter((id) => id !== meetingId));
  }, []);

  const removeSelectedFile = useCallback((fileId: number) => {
    setSelectedFileIds((prev) => prev.filter((id) => id !== fileId));
  }, []);

  const meetingOptions = useMemo(
    () =>
      meetings.map((m) => ({
        value: m.id,
        label: m.title,
      })),
    [meetings],
  );

  const selectedMeetings = useMemo(
    () => meetings.filter((m) => selectedMeetingIds.includes(m.id)),
    [meetings, selectedMeetingIds],
  );

  const fileOptions = useMemo(
    () =>
      selectedMeetings
        .map((meeting) => {
          const options = (meetingFilesMap[meeting.id] ?? [])
            .filter((file) => file.status === "ready")
            .map((file) => ({
              value: file.id,
              label: file.file_name,
              meetingId: meeting.id,
              meetingTitle: meeting.title,
              fileName: file.file_name,
            }));
          if (options.length === 0) return null;
          return {
            label: `${meeting.title} (${options.length})`,
            options,
          };
        })
        .filter(
          (
            group,
          ): group is {
            label: string;
            options: {
              value: number;
              label: string;
              meetingId: number;
              meetingTitle: string;
              fileName: string;
            }[];
          } => group !== null,
        ),
    [meetingFilesMap, selectedMeetings],
  );

  const selectedFiles = useMemo(
    () =>
      selectedMeetings.flatMap((meeting) =>
        (meetingFilesMap[meeting.id] ?? [])
          .filter((file) => selectedFileIds.includes(file.id))
          .map((file) => ({
            value: file.id,
            fileName: file.file_name,
            meetingId: meeting.id,
            meetingTitle: meeting.title,
          })),
      ),
    [meetingFilesMap, selectedFileIds, selectedMeetings],
  );

  const resolveEffectiveFileIds = useCallback((): number[] | undefined => {
    // Only forward explicit file picks. When the user selects a meeting
    // without picking files, leave file_ids empty so the backend enters
    // broad-recall mode (summary router + fair retrieve guarantee
    // per-file coverage). Auto-expanding to every ready file in the
    // meeting bypasses that path and silently drops chunks.
    return selectedFileIds.length > 0 ? selectedFileIds : undefined;
  }, [selectedFileIds]);

  return {
    selectedMeetingIds,
    setSelectedMeetingIds,
    selectedFileIds,
    setSelectedFileIds,
    meetings,
    refreshMeetings,
    meetingFilesMap,
    loadingMeetings,
    loadingFiles,
    removeSelectedMeeting,
    removeSelectedFile,
    meetingOptions,
    selectedMeetings,
    fileOptions,
    selectedFiles,
    resolveEffectiveFileIds,
  };
}
