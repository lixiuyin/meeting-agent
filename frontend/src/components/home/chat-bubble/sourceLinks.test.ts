import { describe, expect, it } from "vitest";
import type { SourceItem } from "../../../api/client";
import { canOpenSource } from "./sourceLinks";

function makeSource(overrides: Partial<SourceItem> = {}): SourceItem {
  return {
    meeting_id: 1,
    meeting_title: "Test Meeting",
    content: "snippet",
    score: 0.9,
    file_id: 10,
    file_name: "demo.pdf",
    file_type: "pdf",
    chunk_index: 0,
    page_number: 1,
    slide_number: null,
    timestamp_start: null,
    timestamp_end: null,
    speaker: null,
    source_kind: "page",
    content_type: "text",
    ...overrides,
  };
}

describe("canOpenSource", () => {
  it("opens file_summary with and without concrete file id", () => {
    const withFile = makeSource({ source_kind: "file_summary", file_id: 10, meeting_id: 1 });
    const withoutFile = makeSource({ source_kind: "file_summary", file_id: null, meeting_id: 1 });

    expect(canOpenSource(withFile)).toBe(true);
    expect(canOpenSource(withoutFile)).toBe(true);
  });

  it("still allows meeting_summary without file id", () => {
    const summary = makeSource({ source_kind: "meeting_summary", file_id: null, meeting_id: 1 });
    expect(canOpenSource(summary)).toBe(true);
  });
});
