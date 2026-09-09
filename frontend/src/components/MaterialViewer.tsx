import { useRef, useEffect, Suspense, lazy } from "react";
import { Button, Image } from "antd";
import { DownloadOutlined, FileOutlined } from "@ant-design/icons";
import type { ViewerRequest } from "../contexts/ViewerContext";
import { getKindCapabilities } from "../types/fileKinds";
import { AudioViewer, VideoViewer, TextPreview } from "./materials/file-views/MediaViewers";
import SlidesViewer from "./materials/file-views/SlidesViewer";
import { useMainContentScrollLock } from "../hooks/useMainContentScrollLock";
import { useMeetingFileUrl } from "../hooks/useMeetingFileUrl";

const PdfSplitViewer = lazy(() => import("./materials/file-views/PdfSplitViewer"));

interface Props {
  request: ViewerRequest;
}

export default function MaterialViewer({ request }: Props) {
  const { meetingId, fileId, fileName, fileType, seekTo, seekEnd, page } = request;
  const url = useMeetingFileUrl(meetingId, fileId);
  const audioRef = useRef<HTMLAudioElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const seekToRef = useRef(seekTo);
  useEffect(() => {
    seekToRef.current = seekTo;
  }, [seekTo]);
  const caps = getKindCapabilities(fileType);
  useMainContentScrollLock(true);

  // Seek on load — runs when the resolved URL changes (e.g. after signed URL arrives)
  useEffect(() => {
    const el = audioRef.current ?? videoRef.current;
    if (seekToRef.current == null || !el) return;

    const handleLoaded = () => {
      el.currentTime = seekToRef.current!;
      el.play().catch(() => {});
    };
    if (el.readyState >= 1) {
      handleLoaded();
    } else {
      el.addEventListener("loadedmetadata", handleLoaded, { once: true });
    }
    return () => el.removeEventListener("loadedmetadata", handleLoaded);
  }, [url]);

  useEffect(() => {
    const media = audioRef.current ?? videoRef.current;
    return () => {
      if (!media) return;
      media.pause();
      media.removeAttribute("src");
      media.load();
    };
  }, []);

  if (caps.viewerHint === "audio") {
    return (
      <AudioViewer
        url={url}
        fileName={fileName}
        seekTo={seekTo}
        seekEnd={seekEnd}
        meetingId={meetingId}
        fileId={fileId}
        audioRef={audioRef}
      />
    );
  }

  if (caps.viewerHint === "video") {
    return (
      <VideoViewer
        url={url}
        seekTo={seekTo}
        seekEnd={seekEnd}
        meetingId={meetingId}
        fileId={fileId}
        videoRef={videoRef}
      />
    );
  }

  if (caps.viewerHint === "slides") {
    return <SlidesViewer meetingId={meetingId} fileId={fileId} fileName={fileName} page={page} />;
  }

  if (caps.viewerHint === "pdf") {
    return (
      <Suspense
        fallback={<div style={{ padding: 40, textAlign: "center" }}>Loading PDF viewer…</div>}
      >
        <PdfSplitViewer
          url={url}
          page={page}
          meetingId={meetingId}
          fileId={fileId}
          evidenceExcerpt={request.evidenceExcerpt}
        />
      </Suspense>
    );
  }

  if (caps.viewerHint === "image") {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Image src={url} alt={fileName} style={{ maxHeight: "70vh" }} />
      </div>
    );
  }

  if (caps.viewerHint === "text") {
    return (
      <TextPreview
        url={url}
        fileName={fileName}
        windowStart={request.windowStart}
        windowEnd={request.windowEnd}
      />
    );
  }

  // Fallback: download
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        padding: 60,
      }}
    >
      <FileOutlined style={{ fontSize: 48, color: "var(--color-text-muted)" }} />
      <div style={{ fontSize: 15, fontWeight: 600 }}>{fileName}</div>
      <div style={{ fontSize: 13, color: "var(--color-text-muted)" }}>
        This file type cannot be previewed in the browser.
      </div>
      <Button
        icon={<DownloadOutlined />}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        download={fileName}
      >
        Download File
      </Button>
    </div>
  );
}
