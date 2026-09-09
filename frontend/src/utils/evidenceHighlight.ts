import type { Element, Root, Text } from "hast";

/** Exact, whitespace-tolerant matching only; ambiguous passages are not selected. */
export function uniqueExcerptRange(text: string, excerpt: string) {
  if (!excerpt.trim()) return null;
  const pattern = excerpt
    .trim()
    .split(/\s+/)
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("\\s+");
  const matches = [...text.matchAll(new RegExp(pattern, "gu"))];
  return matches.length === 1
    ? { start: matches[0].index, end: matches[0].index + matches[0][0].length }
    : null;
}

/** Add safe markup after sanitization, including quotes spanning inline emphasis. */
export function rehypeEvidenceHighlight({ excerpt }: { excerpt?: string }) {
  return (tree: Root) => {
    if (!excerpt) return;
    let text = "";
    const entries: { node: Text; parent: Root | Element; start: number }[] = [];
    const visit = (parent: Root | Element) => {
      for (const node of parent.children) {
        if (node.type === "text") {
          entries.push({ node, parent, start: text.length });
          text += node.value;
        } else if (node.type === "element") {
          const block = /^(p|div|li|h[1-6]|tr|blockquote|pre|br)$/.test(node.tagName);
          if (block) text += "\n";
          visit(node);
          if (block) text += "\n";
        }
      }
    };
    visit(tree);
    const range = uniqueExcerptRange(text, excerpt);
    if (!range) return;
    for (const { node, parent, start } of entries) {
      const from = Math.max(0, range.start - start);
      const to = Math.min(node.value.length, range.end - start);
      if (from >= to) continue;
      const replacement: (Text | Element)[] = [];
      if (from) replacement.push({ type: "text", value: node.value.slice(0, from) });
      replacement.push({
        type: "element",
        tagName: "mark",
        properties: { "data-evidence-highlight": "true" },
        children: [{ type: "text", value: node.value.slice(from, to) }],
      });
      if (to < node.value.length) replacement.push({ type: "text", value: node.value.slice(to) });
      parent.children.splice(parent.children.indexOf(node), 1, ...replacement);
    }
  };
}

/** PDF.js splits a sentence into positioned spans; preserve a DOM range, not HTML. */
export function findPdfExcerptRange(page: HTMLElement, excerpt: string): Range | null {
  const layer = page.querySelector(".react-pdf__Page__textContent");
  if (!layer) return null;
  const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
  const entries: { node: Node; start: number; end: number }[] = [];
  let text = "";
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if (!node.textContent?.trim()) continue;
    if (entries.length) text += " ";
    const start = text.length;
    text += node.textContent;
    entries.push({ node, start, end: text.length });
  }
  const match = uniqueExcerptRange(text, excerpt);
  if (!match) return null;
  const first = entries.find((entry) => entry.end > match.start);
  const last = entries.find((entry) => entry.end >= match.end);
  if (!first || !last) return null;
  const range = document.createRange();
  range.setStart(first.node, match.start - first.start);
  range.setEnd(last.node, match.end - last.start);
  return range;
}
