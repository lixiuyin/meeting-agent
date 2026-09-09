import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PageLayoutView from "../components/materials/file-views/PageLayoutView";
import { findPdfExcerptRange, uniqueExcerptRange } from "./evidenceHighlight";

describe("evidence highlighting", () => {
  it("matches exact whitespace-normalized text and refuses ambiguity", () => {
    expect(uniqueExcerptRange("A C++ [x]\n costs $5.", "C++ [x] costs $5.")).toEqual({
      start: 2,
      end: 20,
    });
    expect(uniqueExcerptRange("quote and quote", "quote")).toBeNull();
    expect(uniqueExcerptRange("2022/11", "2023/11")).toBeNull();
  });
  it("highlights across emphasis without injecting HTML", () => {
    const { container } = render(
      <PageLayoutView
        pageNum={1}
        heading={null}
        text="ChatGPT was **released** in 2022/11."
        imageAssets={[]}
        evidenceExcerpt="released in 2022/11"
      />,
    );
    expect([...container.querySelectorAll("mark")].map((mark) => mark.textContent).join("")).toBe(
      "released in 2022/11",
    );
    expect(screen.getByText("released").closest("strong")).not.toBeNull();
  });
  it("leaves repeated quotations unhighlighted", () => {
    const { container } = render(
      <PageLayoutView
        pageNum={1}
        heading={null}
        text="Same quote. Same quote."
        imageAssets={[]}
        evidenceExcerpt="Same quote"
      />,
    );
    expect(container.querySelector("mark")).toBeNull();
  });
  it("locates a PDF quotation spanning separate text-layer spans", () => {
    const { container } = render(
      <div className="react-pdf__Page__textContent">
        <span>(Released in</span>
        <span>2022/11)</span>
      </div>,
    );
    const range = findPdfExcerptRange(container, "(Released in 2022/11)");
    expect(range?.startOffset).toBe(0);
    expect(range?.endOffset).toBe(8);
    expect(findPdfExcerptRange(container, "2023/11")).toBeNull();
  });
});
