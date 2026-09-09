import { useViewer } from "../contexts/ViewerContext";
import {
  buildEvidenceSearchParams,
  getEvidenceTarget,
  parseEvidenceViewerCoordinates,
} from "../utils/evidenceNavigation";

export function useEvidenceViewer() {
  const { openViewer } = useViewer();
  return (ref: Record<string, unknown>, meetingIds?: number[] | null, excerpt?: string | null) => {
    const target = getEvidenceTarget(ref, meetingIds);
    if (!target) return;
    const params = buildEvidenceSearchParams(target.meetingId, target.fileId, {
      ...ref,
      evidence_excerpt: excerpt,
    });
    const coordinates = parseEvidenceViewerCoordinates(params);
    openViewer({
      ...coordinates,
      page: params.has("pageNumber") || params.has("slideNumber") ? coordinates.page : undefined,
      meetingId: target.meetingId,
      fileId: target.fileId,
      fileName: "",
      fileType: "unknown",
    });
  };
}
