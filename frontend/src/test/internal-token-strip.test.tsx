import { describe, expect, it } from "vitest";

// Mirror the regex patterns from CitationMarkdown.tsx to test them directly
const INTERNAL_TOKEN_RE =
  /\[(?:user[_ ]memory|meeting[_ ]summar(?:y|ies)|file[_ ]summar(?:y|ies)|web[_ ]search|image\s*#?\d*)\]/gi;
const FILE_TOKEN_RE = /\[file:\d+\]/gi;

const stripInternalTokens = (content: string): string =>
  content.replace(INTERNAL_TOKEN_RE, "").replace(FILE_TOKEN_RE, "");

describe("stripInternalTokens", () => {
  it("strips lowercase [meeting_summaries]", () => {
    const result = stripInternalTokens("[meeting_summaries] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[meeting_summaries]");
  });

  it("strips capitalized [Meeting Summaries]", () => {
    const result = stripInternalTokens("[Meeting Summaries] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[Meeting Summaries]");
  });

  it("strips [Meeting Summary] singular", () => {
    const result = stripInternalTokens("[Meeting Summary] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[Meeting Summary]");
  });

  it("strips [File Summaries]", () => {
    const result = stripInternalTokens("[File Summaries] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[File Summaries]");
  });

  it("strips [File Summary] singular", () => {
    const result = stripInternalTokens("[File Summary] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[File Summary]");
  });

  it("strips [User Memory]", () => {
    const result = stripInternalTokens("[User Memory] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[User Memory]");
  });

  it("strips [Web Search]", () => {
    const result = stripInternalTokens("[Web Search] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[Web Search]");
  });

  it("strips [Image #1] and [Image #42]", () => {
    expect(stripInternalTokens("[Image #1] text")).not.toContain("[Image #1]");
    expect(stripInternalTokens("[Image #42] more")).not.toContain("[Image #42]");
  });

  it("strips bare [Image]", () => {
    const result = stripInternalTokens("[Image] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[Image]");
  });

  it("strips [file:N] tokens", () => {
    const result = stripInternalTokens("[file:11] text");
    expect(result).toContain("text");
    expect(result).not.toContain("[file:11]");
  });

  it("preserves numeric citations [3]", () => {
    const result = stripInternalTokens("See [3] for details");
    expect(result).toContain("[3]");
  });

  it("strips mixed tokens in one pass", () => {
    const result = stripInternalTokens(
      "[Meeting Summaries] The project [File Summary] is on track [file:3]. [Image #2]",
    );
    expect(result).not.toContain("[Meeting Summaries]");
    expect(result).not.toContain("[File Summary]");
    expect(result).not.toContain("[file:3]");
    expect(result).not.toContain("[Image #2]");
    expect(result).toContain("The project");
    expect(result).toContain("is on track");
  });
});
