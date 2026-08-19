import { slugify } from "@/lib/utils";

export type MarkdownHeading = {
  id: string;
  text: string;
  level: number;
  /** 1-based source line the heading starts on. */
  line: number;
};

/** Headings the wiki renderer gives an anchor id to (h2–h4). */
const MIN_LEVEL = 2;
const MAX_LEVEL = 4;

const ATX_RE = /^ {0,3}(#{2,4})[ \t]+(.*)$/;
/** Optional trailing `###` run that CommonMark treats as decoration, not text. */
const ATX_CLOSING_RE = /[ \t]+#+\s*$/;
const FENCE_OPEN_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE_RE = /^ {0,3}(`{3,}|~{3,})\s*$/;
const SETEXT_UNDERLINE_RE = /^ {0,3}(=+|-+)\s*$/;
const INDENTED_CODE_RE = /^ {4,}\S/;
const LIST_OR_QUOTE_RE = /^ {0,3}(>|([-*+]|\d{1,9}[.)])[ \t])/;

type Fence = { marker: string; length: number };

function fenceOpener(line: string): Fence | null {
  const match = line.match(FENCE_OPEN_RE);
  if (!match) return null;
  const marker = match[1][0];
  // CommonMark: a backtick fence's info string may not contain a backtick, so
  // ``` ``code`` ``` style inline spans do not open a block.
  if (marker === "`" && match[2].includes("`")) return null;
  return { marker, length: match[1].length };
}

function closesFence(line: string, fence: Fence): boolean {
  const match = line.match(FENCE_CLOSE_RE);
  return !!match && match[1][0] === fence.marker && match[1].length >= fence.length;
}

/**
 * Collect the headings the table of contents links to.
 *
 * Pure and line-oriented on purpose: the renderer matches each heading it emits
 * against `line` (via the hast node's source position) instead of counting
 * render order, so a heading this scanner gets wrong can only lose its own
 * anchor — it can no longer shift the ids of every heading after it.
 *
 * Fenced blocks are skipped because wiki pages are LLM-generated and shell/YAML
 * samples routinely contain `## …` comment lines, which used to show up as
 * phantom TOC entries.
 *
 * Headings nested inside a blockquote are deliberately not indexed: finding them
 * means tracking fence state per quote level, and losing one anchor is cheaper
 * than a phantom TOC entry that scrolls nowhere.
 */
export function extractHeadings(md: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = [];
  const seen = new Map<string, number>();

  const push = (rawText: string, level: number, line: number) => {
    if (level < MIN_LEVEL || level > MAX_LEVEL) return;
    const text = rawText.trim();
    // Non-ASCII text (e.g. Vietnamese diacritics) strips down to nothing or to
    // the same base for distinct headings — dedupe and fall back to a stable
    // placeholder so ids/keys never collide.
    const base = slugify(text) || "section";
    const occurrence = seen.get(base) ?? 0;
    seen.set(base, occurrence + 1);
    headings.push({ id: occurrence === 0 ? base : `${base}-${occurrence}`, text, level, line });
  };

  let fence: Fence | null = null;
  // Open paragraph, tracked only so a setext underline can retitle it.
  let paragraphStart = -1;
  let paragraphLines: string[] = [];
  const endParagraph = () => {
    paragraphStart = -1;
    paragraphLines = [];
  };

  const lines = md.split("\n");
  for (let i = 0; i < lines.length; i++) {
    // `.` and `$` do not cross a lone `\r`, so a CRLF document matched none of
    // the patterns below and produced no headings at all.
    const line = lines[i].replace(/\r$/, "");
    const lineNo = i + 1;

    if (fence) {
      // An unterminated fence swallows the rest of the document, matching how
      // CommonMark parses it — everything below the opener is code.
      if (closesFence(line, fence)) fence = null;
      continue;
    }

    const opener = fenceOpener(line);
    if (opener) {
      fence = opener;
      endParagraph();
      continue;
    }

    if (!line.trim()) {
      endParagraph();
      continue;
    }

    const atx = line.match(ATX_RE);
    if (atx) {
      endParagraph();
      push(atx[2].replace(ATX_CLOSING_RE, ""), atx[1].length, lineNo);
      continue;
    }

    const setext = paragraphStart > 0 ? line.match(SETEXT_UNDERLINE_RE) : null;
    if (setext) {
      // The underline turns the paragraph above it into the heading, so the
      // node starts on the paragraph's first line, not on the underline.
      push(paragraphLines.join(" "), setext[1][0] === "=" ? 1 : 2, paragraphStart);
      endParagraph();
      continue;
    }

    if (paragraphStart < 0 && INDENTED_CODE_RE.test(line)) continue;

    if (LIST_OR_QUOTE_RE.test(line)) {
      endParagraph();
      continue;
    }

    if (paragraphStart > 0) {
      paragraphLines.push(line.trim());
      continue;
    }

    paragraphStart = lineNo;
    paragraphLines = [line.trim()];
  }

  return headings;
}
