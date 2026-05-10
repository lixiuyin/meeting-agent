import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import { describe, expect, it } from "vitest";

import { rehypePlugins, remarkPlugins } from "../utils/markdown";

describe("markdown sanitizer", () => {
  it("keeps katex output and style attributes used by KaTeX", () => {
    const { container } = render(
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {"$x^2$"}
      </ReactMarkdown>,
    );

    expect(container.querySelector(".katex")).toBeTruthy();
    expect(container.querySelector(".katex .katex-html")).toBeTruthy();
    expect(container.querySelector(".katex .mord")).toBeTruthy();
    expect(container.querySelector(".katex [style]")).toBeTruthy();
  });

  it("still strips script tags and event handlers", () => {
    const malicious = '$a$ \n<script>alert(1)</script> \n<img src="x" onerror="alert(2)"> \n$b$';
    const { container } = render(
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {malicious}
      </ReactMarkdown>,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector(".katex")).toBeTruthy();
  });

  // L-3: Additional XSS vectors that must be sanitized.
  it("strips javascript: URLs from links", () => {
    const malicious = "[click me](javascript:alert(1))";
    const { container } = render(
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {malicious}
      </ReactMarkdown>,
    );
    const link = container.querySelector("a");
    if (link) {
      const href = link.getAttribute("href");
      expect(href === null || !/^javascript:/i.test(href)).toBe(true);
    }
  });

  it("strips SVG with embedded script tags", () => {
    const malicious = '<svg><circle r="5"/><script>alert(1)</script></svg>';
    const { container } = render(
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {malicious}
      </ReactMarkdown>,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("svg")?.innerHTML?.includes("script")).toBeFalsy();
  });

  it("strips on* event handlers from arbitrary elements", () => {
    const malicious = '<div onmouseover="alert(1)">hover me</div>';
    const { container } = render(
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {malicious}
      </ReactMarkdown>,
    );
    expect(container.querySelector("[onmouseover]")).toBeNull();
  });

  it("strips data: URL based script injection", () => {
    const malicious = '<object data="data:text/html,<script>alert(1)</script>"></object>';
    const { container } = render(
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {malicious}
      </ReactMarkdown>,
    );
    const obj = container.querySelector("object");
    if (obj) {
      const data = obj.getAttribute("data");
      expect(data === null || !/^data:/i.test(data)).toBe(true);
    }
  });
});
