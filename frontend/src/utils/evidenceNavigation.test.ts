import { describe, expect, it } from "vitest";

import { buildEvidenceSearchParams, parseEvidenceViewerCoordinates } from "./evidenceNavigation";

describe("evidence navigation", () => {
  it("preserves exact source coordinates", () => {
    const params = buildEvidenceSearchParams(7, 9, {
      source_revision: "rev-1",
      page_number: 3,
      timestamp_start: 12.5,
      timestamp_end: 18,
      chunk_index: 4,
      window_start: 10,
      window_end: 42,
    });

    expect(params.toString()).toBe(
      "meetingId=7&fileId=9&sourceRevision=rev-1&pageNumber=3&timestampStart=12.5&timestampEnd=18&chunkIndex=4&windowStart=10&windowEnd=42",
    );
  });

  it("does not turn a missing timestamp into a zero-second media seek", () => {
    expect(parseEvidenceViewerCoordinates(new URLSearchParams("pageNumber=2"))).toEqual({
      page: 2,
      seekTo: undefined,
      seekEnd: undefined,
      sourceRevision: undefined,
      chunkIndex: undefined,
      windowStart: undefined,
      windowEnd: undefined,
    });
  });

  it("retains an explicit zero-second timestamp", () => {
    expect(parseEvidenceViewerCoordinates(new URLSearchParams("timestampStart=0"))).toEqual({
      page: 1,
      seekTo: 0,
      seekEnd: undefined,
      sourceRevision: undefined,
      chunkIndex: undefined,
      windowStart: undefined,
      windowEnd: undefined,
    });
  });

  it("parses revision and exact text coordinates", () => {
    expect(
      parseEvidenceViewerCoordinates(
        new URLSearchParams("sourceRevision=rev-2&chunkIndex=7&windowStart=10&windowEnd=42"),
      ),
    ).toMatchObject({
      sourceRevision: "rev-2",
      chunkIndex: 7,
      windowStart: 10,
      windowEnd: 42,
    });
  });

  it("retains the end of an exact media evidence range", () => {
    expect(
      parseEvidenceViewerCoordinates(new URLSearchParams("timestampStart=12.5&timestampEnd=18")),
    ).toMatchObject({ seekTo: 12.5, seekEnd: 18 });
  });

  it("rejects an end timestamp that does not follow the start", () => {
    expect(
      parseEvidenceViewerCoordinates(new URLSearchParams("timestampStart=12.5&timestampEnd=8")),
    ).toMatchObject({ seekTo: 12.5, seekEnd: undefined });
  });
});
