import { describe, expect, it } from "vitest";
import type { FileTimelineResponse } from "../api/client";
import { resolveEvidenceLocation } from "./resolveEvidenceLocation";

const pages: FileTimelineResponse = {
  kind: "pages",
  file_id: 9,
  file_name: "slides.pdf",
  page_count: 3,
  pages: [
    { page_num: 1, text: "😀Introduction", heading: null },
    { page_num: 2, text: "Earlier release (2022/11)", heading: null },
    { page_num: 3, text: "ChatGPT was released\nin 2022/11.", heading: null },
  ],
};
const source = pages.pages.map((p) => p.text).join("\n\n");
const start = Array.from(
  pages.pages
    .slice(0, 2)
    .map((p) => p.text)
    .join("\n\n") + "\n\n",
).length;
const coordinates = { page: 1, windowStart: start, windowEnd: Array.from(source).length };

describe("resolveEvidenceLocation", () => {
  it("maps Unicode source windows to the actual PDF page and narrows the quote", () => {
    expect(
      resolveEvidenceLocation(source, pages, coordinates, "released in 2022/11."),
    ).toMatchObject({
      page: 3,
      windowStart: start + 12,
      windowEnd: Array.from(source).length,
    });
  });
  it("uses the cited window to disambiguate repeated text", () => {
    expect(resolveEvidenceLocation(source, pages, coordinates, "2022/11")?.page).toBe(3);
    expect(resolveEvidenceLocation(source, pages, { page: 1 }, "2022/11")).toBeNull();
  });
  it("does not fuzzy-match a missing quote or stale offsets", () => {
    expect(resolveEvidenceLocation(source, pages, coordinates, "released in 2023/11")).toBeNull();
    expect(
      resolveEvidenceLocation(source, pages, { page: 1, windowStart: 999, windowEnd: 1000 }),
    ).toBeNull();
  });
  it("does not infer page offsets from a different parsed transcript", () => {
    expect(
      resolveEvidenceLocation("unrelated " + pages.pages[2].text, pages, { page: 1 }, "ChatGPT"),
    ).toBeNull();
  });
  it("resolves the first intersecting page for a source window without a quote", () => {
    expect(resolveEvidenceLocation(source, pages, coordinates)?.page).toBe(3);
  });
  it("maps audio evidence to the matching segment range including zero seconds", () => {
    const timeline: FileTimelineResponse = {
      kind: "segments",
      file_id: 4,
      file_name: "meeting.mp3",
      total_duration: 15,
      speaker_count: 1,
      segments: [
        { start: 0, end: 5, text: "Ship today", speaker: "Alice" },
        { start: 5, end: 15, text: "Owner Bob", speaker: "Alice" },
      ],
    };
    expect(
      resolveEvidenceLocation(
        "Alice: Ship today\nAlice: Owner Bob",
        timeline,
        { page: 1 },
        "Ship today",
      ),
    ).toMatchObject({ seekTo: 0, seekEnd: 5 });
    expect(
      resolveEvidenceLocation(
        "Alice: Ship today\nAlice: Owner Bob",
        timeline,
        { page: 1 },
        "Owner Bob",
      ),
    ).toMatchObject({ seekTo: 5, seekEnd: 15 });
  });
  it("escapes regular expression characters in quotations", () => {
    const text = "C++ [v2] costs $5.";
    const timeline: FileTimelineResponse = {
      kind: "text",
      file_id: 9,
      file_name: "note.txt",
      text,
      word_count: 4,
    };
    expect(resolveEvidenceLocation(text, timeline, { page: 1 }, "C++ [v2]")).toMatchObject({
      windowStart: 0,
      windowEnd: 8,
    });
  });
});
