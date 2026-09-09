import { beforeEach, describe, expect, it, vi } from "vitest";
import { resolveViewerRequest, sourceToViewerRequest } from "./sourceLocation";
const { getMeeting, locateFileEvidence } = vi.hoisted(() => ({
  getMeeting: vi.fn(),
  locateFileEvidence: vi.fn(),
}));
vi.mock("../api/client", () => ({ getMeeting, locateFileEvidence }));
const request = {
  meetingId: 1,
  fileId: 2,
  fileName: "old",
  fileType: "pdf",
  page: 3,
  sourceRevision: "v1",
};
beforeEach(() => {
  vi.clearAllMocks();
  getMeeting.mockResolvedValue({
    data: {
      title: "m",
      files: [{ id: 2, file_name: "a.pdf", file_type: "pdf", source_revisions: ["v1"] }],
    },
  });
});
describe("unified source resolution", () => {
  it("rejects stale ordinary chat sources before locating", async () => {
    await expect(
      resolveViewerRequest({ ...request, sourceRevision: "old" }, new AbortController().signal),
    ).rejects.toThrow("viewer.sourceVersionChanged");
    expect(locateFileEvidence).not.toHaveBeenCalled();
  });
  it("validates explicit pages and preserves the server's precision", async () => {
    locateFileEvidence.mockResolvedValue({
      data: { status: "page_only", page: 3, source_revision: "v1" },
    });
    const resolved = await resolveViewerRequest(request, new AbortController().signal);
    expect(locateFileEvidence).toHaveBeenCalledWith(
      1,
      2,
      expect.objectContaining({ page: 3, source_revision: "v1" }),
      expect.anything(),
    );
    expect(resolved.warning).toBe("viewer.exactLocationUnavailable");
  });
  it("never retains an ambiguous quote highlight", async () => {
    locateFileEvidence.mockResolvedValue({ data: { status: "ambiguous" } });
    const { request: r } = await resolveViewerRequest(
      { ...request, evidenceExcerpt: "repeated" },
      new AbortController().signal,
    );
    expect(r.evidenceExcerpt).toBeUndefined();
  });
  it("maps history, image and memory provenance without dropping fields", () => {
    const r = sourceToViewerRequest({
      meeting_id: 1,
      meeting_title: "m",
      file_id: 2,
      file_type: "pdf",
      file_name: "a.pdf",
      chunk_index: null,
      page_number: null,
      timestamp_start: null,
      timestamp_end: null,
      speaker: null,
      source_kind: "page",
      content: "text",
      score: 0.2,
      slide_number: 4,
      document_revision: "v1",
      window_start: 2,
      window_end: 8,
      evidence_excerpt: "quote",
    });
    expect(r).toMatchObject({
      page: 4,
      sourceRevision: "v1",
      windowStart: 2,
      windowEnd: 8,
      evidenceExcerpt: "quote",
    });
  });
});
