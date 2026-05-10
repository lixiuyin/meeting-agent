import { useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import {
  normalizeLatexMathDelimiters,
  rehypePlugins,
  remarkPlugins,
  resolveMarkdownImageSrc,
} from "../../utils/markdown";
import { isSafeExternalUrl } from "../../utils/url";

const MATH_PLACEHOLDER_PREFIX = "__MATH_PRESERVE_";

/**
 * Replace citation markers like [1] with [1](#cite-1) links,
 * but skip text inside inline ($...$) and block ($$...$$) math delimiters.
 * Consecutive citations like [1][2][3] are merged into [1-3].
 */
const preprocessCitations = (content: string, skipMerge: boolean = false): string => {
  const normalized = normalizeLatexMathDelimiters(content);
  const mathBlocks: string[] = [];
  let tokenized = normalized;

  // Block math $$...$$
  tokenized = tokenized.replace(/\$\$([\s\S]*?)\$\$/g, (match) => {
    const idx = mathBlocks.length;
    mathBlocks.push(match);
    return `${MATH_PLACEHOLDER_PREFIX}${idx}__`;
  });

  // Inline math $...$
  tokenized = tokenized.replace(/(?<!\\)\$([^\s$][^$]*?)\$/g, (match) => {
    const idx = mathBlocks.length;
    mathBlocks.push(match);
    return `${MATH_PLACEHOLDER_PREFIX}${idx}__`;
  });

  const cited = tokenized.replace(/\[(\d+)\](?!\()/g, "[$1](#cite-$1)");

  // Merge consecutive citation links into ranges
  const mergeRun = (nums: number[]): string => {
    if (nums.length === 0) return "";
    const runs: string[] = [];
    let runStart = 0;
    for (let i = 1; i <= nums.length; i++) {
      if (i < nums.length && nums[i] === nums[i - 1] + 1) continue;
      if (runStart === i - 1) {
        runs.push(`[${nums[runStart]}](#cite-${nums[runStart]})`);
      } else {
        runs.push(`[${nums[runStart]}–${nums[i - 1]}](#cite-${nums[runStart]}~${nums[i - 1]})`);
      }
      runStart = i;
    }
    return runs.join("");
  };

  const merged = cited.replace(/(\[\d+\]\(#cite-\d+\))(?:\s*(\[\d+\]\(#cite-\d+\)))+/g, (match) => {
    const nums = [...match.matchAll(/\[(\d+)\]/g)].map((m) => parseInt(m[1], 10));
    if (nums.length < 2) return match;
    return mergeRun(nums);
  });

  let restored = skipMerge ? cited : merged;
  mathBlocks.forEach((block, idx) => {
    restored = restored.replace(`${MATH_PLACEHOLDER_PREFIX}${idx}__`, block);
  });

  return restored;
};

/** Strip citation markers whose numbers exceed the actual source count. */
const stripOutOfRangeCitations = (content: string, sourceCount: number): string =>
  content.replace(/\[(\d+)(?:[-–](\d+))?\](?!\()/g, (_, start, end) => {
    const startNum = parseInt(start, 10);
    const endNum = end ? parseInt(end, 10) : startNum;
    if (startNum > sourceCount && (!end || endNum > sourceCount)) return "";
    return `[${startNum}${end ? `–${endNum}` : ""}]`;
  });

/** Strip internal placeholder tokens that should never appear in the final answer. */
const stripInternalTokens = (content: string): string =>
  content
    .replace(
      /\[(?:user[_ ]memory|meeting[_ ]summar(?:y|ies)|file[_ ]summar(?:y|ies)|web[_ ]search|image\s*#?\d*)\]/gi,
      "",
    )
    .replace(/\[file:\d+\]/gi, "")
    .replace(/\bfile:\s*\d+\b/g, "");

// eslint-disable-next-line react-refresh/only-export-components
export { preprocessCitations, stripOutOfRangeCitations, stripInternalTokens };

interface Props {
  content: string;
  sourceCount: number;
  onCiteClick: (idx: number) => void;
  resolveAssetUrl: (path: string) => string;
  streaming?: boolean;
  /** Set true for simpler rendering without useMemo (history context). */
  simple?: boolean;
}

export function CitationMarkdown({
  content,
  sourceCount,
  onCiteClick,
  resolveAssetUrl,
  streaming = false,
  simple = false,
}: Props) {
  const processed = useMemo(() => {
    const cleaned = stripInternalTokens(content);
    const sanitized = stripOutOfRangeCitations(cleaned, sourceCount);
    return preprocessCitations(sanitized, streaming);
  }, [content, sourceCount, streaming]);

  const components = useMemo<Components>(
    () => ({
      a: ({ href, children }: { href?: string; children?: ReactNode }) => {
        const citeMatch = href?.match(/^#cite-(\d+)(?:~(\d+))?$/);
        if (citeMatch) {
          const start = parseInt(citeMatch[1], 10);
          const end = citeMatch[2] ? parseInt(citeMatch[2], 10) : start;
          const label = end > start ? `${start}–${end}` : `${start}`;
          // Merged ranges like [1-3] are rendered as a single button; clicking
          // it opens the FIRST source in the range. Looping over every index
          // would call onCiteClick rapidly and only the last call's UI state
          // would remain, making clicks land on the wrong source.
          return (
            <button
              type="button"
              aria-label={
                end > start
                  ? `Preview source ${start} (of ${start}–${end})`
                  : `Preview source ${start}`
              }
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (start >= 1 && start <= sourceCount) onCiteClick(start);
              }}
              style={{
                background: "transparent",
                border: "none",
                padding: 0,
                margin: 0,
                cursor: "pointer",
                color: "var(--color-primary)",
                fontWeight: 600,
                fontSize: 11,
                lineHeight: 1,
                verticalAlign: "super",
                userSelect: "none",
                outline: "none",
              }}
              onFocus={(e) => {
                (e.currentTarget as HTMLElement).style.outline = "2px solid var(--color-primary)";
              }}
              onBlur={(e) => {
                (e.currentTarget as HTMLElement).style.outline = "none";
              }}
            >
              [{label}]
            </button>
          );
        }
        if (!isSafeExternalUrl(href)) {
          return <span>{children}</span>;
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
      img: ({ src, alt }: { src?: string; alt?: string }) => {
        const resolved = resolveMarkdownImageSrc(src, resolveAssetUrl);
        if (!resolved || resolved === "data:,") return null;
        return <img src={resolved} alt={alt || ""} loading="lazy" />;
      },
    }),
    [onCiteClick, sourceCount, resolveAssetUrl],
  );

  return (
    <div className={simple ? undefined : "markdown-body"}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
