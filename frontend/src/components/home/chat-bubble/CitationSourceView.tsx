import { useEffect, useState } from "react";
import { Spin } from "antd";
import { getFileTimeline, type FileTimelineResponse, type SourceItem } from "../../../api/client";
import ImageAssetCard, { type ImageAsset } from "../../materials/file-views/ImageAssetCard";
import PageLayoutView from "../../materials/file-views/PageLayoutView";
import { reportNonCriticalError } from "../../../utils/monitoring";
import { FallbackSourceView } from "./SourcePreviewContent";
import { isImageDerivedSource } from "./sourceHelpers";
import { useMeetingFileUrl } from "../../../hooks/useMeetingFileUrl";

const TIMELINE_CACHE_MAX = 200;
const timelineCache = new Map<string, Promise<FileTimelineResponse>>();

async function fetchTimeline(
  meetingId: number,
  fileId: number,
  revision?: string | null,
): Promise<FileTimelineResponse> {
  const key = `${meetingId}:${fileId}:${revision ?? "legacy"}`;
  const cached = timelineCache.get(key);
  if (cached) {
    timelineCache.delete(key);
    timelineCache.set(key, cached);
    return cached;
  }
  const promise = getFileTimeline(meetingId, fileId)
    .then((res) => res.data)
    .catch((error) => {
      if (timelineCache.get(key) === promise) timelineCache.delete(key);
      throw error;
    });
  timelineCache.set(key, promise);
  if (timelineCache.size > TIMELINE_CACHE_MAX) {
    const oldestKey = timelineCache.keys().next().value;
    if (oldestKey) timelineCache.delete(oldestKey);
  }
  return promise;
}

interface Props {
  source: SourceItem;
}

export function CitationSourceView({ source }: Props) {
  if (source.meeting_id == null || source.file_id == null) {
    return <FallbackSourceView source={source} />;
  }

  return (
    <CitationSourceViewInner
      key={`${source.meeting_id}:${source.file_id}:${source.document_revision ?? "legacy"}`}
      source={source}
    />
  );
}

function CitationSourceViewInner({ source }: Props) {
  const [timeline, setTimeline] = useState<FileTimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const meetingId = source.meeting_id!;
  const fileId = source.file_id!;
  const fileUrl = useMeetingFileUrl(meetingId, fileId);

  useEffect(() => {
    let cancelled = false;
    fetchTimeline(meetingId, fileId, source.document_revision)
      .then((data) => {
        if (!cancelled) {
          setTimeline(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        reportNonCriticalError("Failed to fetch timeline for citation source", err);
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, fileId, source.document_revision]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 24 }}>
        <Spin />
      </div>
    );
  }

  if (!timeline) {
    return <FallbackSourceView source={source} />;
  }

  if (timeline.kind === "pages" && source.page_number != null) {
    const page = timeline.pages.find((p) => p.page_num === source.page_number);
    if (page) {
      return (
        <PageLayoutView
          pageNum={page.page_num}
          heading={page.heading}
          text={page.text}
          imageAssets={(page.image_assets ?? []) as ImageAsset[]}
          label="Page"
          variant="modal"
        />
      );
    }
  }

  if (timeline.kind === "captions" && isImageDerivedSource(source)) {
    if (fileUrl) {
      const asset: ImageAsset = {
        storage_path: fileUrl,
        thumbnail_path: null,
        caption: timeline.captions[0]?.caption ?? null,
        ocr_text: timeline.captions[0]?.ocr_text ?? null,
      };
      return <ImageAssetCard asset={asset} resolveUrl={() => fileUrl} />;
    }
  }

  return <FallbackSourceView source={source} />;
}
