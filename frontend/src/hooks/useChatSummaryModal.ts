import { useCallback, useRef, useState } from "react";
import { message } from "antd";
import {
  generateSummary,
  getSummary,
  sendMeetingSummaryStream,
  formatApiErrorMessage,
} from "../api/client";

interface SummaryFileRef {
  id: number;
  file_name: string;
  file_type: string;
}

export function useChatSummaryModal() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [meetingId, setMeetingId] = useState<number | null>(null);
  const [targetFileId, setTargetFileId] = useState<number | null>(null);
  const [files, setFiles] = useState<SummaryFileRef[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const openSummary = useCallback(
    async (mid: number, fileId?: number, readOnly?: boolean, fallbackContent?: string) => {
      setOpen(true);
      setMeetingId(mid);
      setTargetFileId(fileId ?? null);
      abortRef.current?.abort();

      // Seed the modal with the citation's source content immediately so the
      // user always sees something on click, even if the backend summary
      // endpoint is slow, returns empty, or doesn't include this file's
      // per-file summary. The API fetch below upgrades the data when fresh
      // content is available.
      if (fallbackContent && fallbackContent.trim()) {
        setData(fallbackContent);
        setFiles([]);
        setLoading(false);
        setStreaming(false);
      }

      // Try pre-generated summary first
      try {
        const existing = await getSummary(mid);
        const fileList =
          existing.data?.per_file_summaries?.map((f) => ({
            id: f.file_id,
            file_name: f.file_name,
            file_type: f.file_type,
          })) ?? [];

        if (fileId != null) {
          const perFile = existing.data?.per_file_summaries?.find((f) => f.file_id === fileId);
          if (perFile?.summary) {
            setData(perFile.summary);
            setFiles(fileList);
            setLoading(false);
            setStreaming(false);
            return;
          }
        } else if (existing.data?.summary) {
          setData(existing.data.summary);
          setFiles(fileList);
          setLoading(false);
          setStreaming(false);
          return;
        }
      } catch {
        // No cached summary — fall through to generation
      }

      // When opened from a citation click (readOnly), don't auto-regenerate.
      // If a fallback was seeded above we keep it visible; otherwise show the
      // empty state with a Generate button.
      if (readOnly) {
        if (!fallbackContent || !fallbackContent.trim()) {
          setData(null);
          setFiles([]);
        }
        setLoading(false);
        setStreaming(false);
        return;
      }

      setLoading(true);
      setData(null);
      setStreaming(false);
      setFiles([]);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        // Per-file summaries don't have a streaming endpoint; fall back to a
        // one-shot generate that returns per_file_summaries[] in the response.
        if (fileId != null) {
          const res = await generateSummary(mid);
          const perFile = res.data.per_file_summaries?.find((f) => f.file_id === fileId);
          setData(perFile?.summary ?? "");
          setFiles(
            res.data.per_file_summaries?.map((f) => ({
              id: f.file_id,
              file_name: f.file_name,
              file_type: f.file_type,
            })) ?? [],
          );
          setLoading(false);
          return;
        }
        let accumulated = "";
        setStreaming(true);
        for await (const event of sendMeetingSummaryStream(mid, { signal: controller.signal })) {
          if (event.type === "token") {
            accumulated += event.content;
            setData(accumulated);
            setLoading(false);
          } else if (event.type === "done") {
            setLoading(false);
            setStreaming(false);
            abortRef.current = null;
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
        }
        if (!accumulated) {
          const res = await generateSummary(mid);
          setData(res.data.summary ?? "");
        }
        // Populate file list after generation so file citations are resolvable.
        try {
          const summaryRes = await getSummary(mid);
          setFiles(
            summaryRes.data.per_file_summaries?.map((f) => ({
              id: f.file_id,
              file_name: f.file_name,
              file_type: f.file_type,
            })) ?? [],
          );
        } catch {
          // Non-critical — file citations just won't be navigable.
        }
      } catch (err) {
        if ((err as { name?: string })?.name !== "AbortError") {
          message.error(formatApiErrorMessage(err, "Failed to generate summary"));
        }
        setStreaming(false);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const regenerate = useCallback(async () => {
    if (meetingId == null) return;
    setLoading(true);
    setData(null);
    setStreaming(false);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      // Per-file regen has no streaming endpoint; use one-shot generate.
      if (targetFileId != null) {
        const res = await generateSummary(meetingId);
        const perFile = res.data.per_file_summaries?.find((f) => f.file_id === targetFileId);
        setData(perFile?.summary ?? "");
        setFiles(
          res.data.per_file_summaries?.map((f) => ({
            id: f.file_id,
            file_name: f.file_name,
            file_type: f.file_type,
          })) ?? [],
        );
        setLoading(false);
        return;
      }
      let accumulated = "";
      setStreaming(true);
      for await (const event of sendMeetingSummaryStream(meetingId, {
        signal: controller.signal,
      })) {
        if (event.type === "token") {
          accumulated += event.content;
          setData(accumulated);
          setLoading(false);
        } else if (event.type === "done") {
          setLoading(false);
          setStreaming(false);
          abortRef.current = null;
        } else if (event.type === "error") {
          throw new Error(event.message);
        }
      }
      if (!accumulated) {
        const res = await generateSummary(meetingId);
        setData(res.data.summary ?? "");
      }
      // Populate file list after generation so file citations are resolvable.
      try {
        const summaryRes = await getSummary(meetingId);
        setFiles(
          summaryRes.data.per_file_summaries?.map((f) => ({
            id: f.file_id,
            file_name: f.file_name,
            file_type: f.file_type,
          })) ?? [],
        );
      } catch {
        // Non-critical — file citations just won't be navigable.
      }
    } catch (err) {
      if ((err as { name?: string })?.name !== "AbortError") {
        message.error(formatApiErrorMessage(err, "Failed to regenerate summary"));
      }
      setStreaming(false);
    } finally {
      setLoading(false);
    }
  }, [meetingId, targetFileId]);

  const copy = useCallback(async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data);
      message.success("Summary copied");
    } catch {
      message.error("Failed to copy summary");
    }
  }, [data]);

  const download = useCallback(() => {
    if (!data) return;
    const blob = new Blob([data], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const scope =
      targetFileId != null
        ? `meeting-${meetingId ?? "unknown"}-file-${targetFileId}`
        : `meeting-${meetingId ?? "unknown"}`;
    a.download = `${scope}-summary-${ts}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [data, meetingId, targetFileId]);

  const close = useCallback(() => {
    setOpen(false);
    abortRef.current?.abort();
    setLoading(false);
    setStreaming(false);
    setData(null);
    setFiles([]);
  }, []);

  return {
    open,
    loading,
    data,
    streaming,
    meetingId,
    targetFileId,
    files,
    openSummary,
    regenerate,
    copy,
    download,
    close,
  };
}
