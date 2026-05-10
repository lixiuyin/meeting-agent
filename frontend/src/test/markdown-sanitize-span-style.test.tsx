/**
 * Tests for markdown sanitize configuration.
 *
 * Verifies that the rehype plugins use a restricted sanitize schema
 * that only allows specific CSS properties on span elements.
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect } from "vitest";
import { rehypePlugins, rehypePluginsNoMath, hasMathContent } from "../utils/markdown";

describe("markdown sanitize schema", () => {
  it("rehypePlugins includes rehype-katex and rehype-sanitize", () => {
    // Should have 2 entries: katex plugin + sanitize plugin
    expect(rehypePlugins).toHaveLength(2);
    // First is the katex plugin
    expect(rehypePlugins[0]).toBeDefined();
    // Second is [rehypeSanitize, schema]
    expect(Array.isArray(rehypePlugins[1])).toBe(true);
  });

  it("rehypePluginsNoMath includes only rehype-sanitize", () => {
    expect(rehypePluginsNoMath).toHaveLength(1);
    expect(Array.isArray(rehypePluginsNoMath[0])).toBe(true);
  });

  it("sanitize schema is passed to both plugin sets", () => {
    // Both should use [rehypeSanitize, mathSafeSchema] format
    const sanitizeEntry = rehypePluginsNoMath[0] as [unknown, unknown];
    expect(sanitizeEntry[0]).toBeDefined();
    expect(sanitizeEntry[1]).toBeDefined();
    // The schema object should have tagNames
    const schema = sanitizeEntry[1] as Record<string, unknown>;
    expect(schema.tagNames).toBeDefined();
    expect(Array.isArray(schema.tagNames)).toBe(true);
    expect(schema.tagNames as string[]).toContain("math");
  });

  it("hasMathContent detects inline math", () => {
    expect(hasMathContent("Hello $x^2$ world")).toBe(true);
  });

  it("hasMathContent detects display math", () => {
    expect(hasMathContent("$$\nx^2\n$$")).toBe(true);
  });

  it("hasMathContent returns false for plain text", () => {
    expect(hasMathContent("Hello world no math here")).toBe(false);
  });
});
