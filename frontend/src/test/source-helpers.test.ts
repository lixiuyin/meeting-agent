import { describe, expect, it } from "vitest";

import { sanitizeAgentAnswer } from "../components/home/chat-bubble/sourceHelpers";

describe("sanitizeAgentAnswer", () => {
  it("removes legacy timeout diagnostics and retrieval metadata", () => {
    const legacy =
      "The model exceeded the fast-path latency budget. Relevant source excerpts:\n\n" +
      "- [Retrieval context: meeting=Jobs; file=slides_8307.pdf; approval=unreviewed] | Use-case | Generation | 45.6%";

    expect(sanitizeAgentAnswer(legacy)).toBe("- | Use-case | Generation | 45.6%");
  });
});
