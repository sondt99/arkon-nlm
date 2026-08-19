/**
 * Fence-aware heading extraction (#80).
 *
 * The old extractor regex-scanned every line starting with `#`, so a `## install` comment
 * inside a bash snippet became a phantom TOC entry — and because the renderer assigned ids
 * from a shared render-order cursor, one phantom shifted the id of every heading after it.
 * Every TOC link below that point scrolled to the wrong section.
 */

import { describe, expect, it } from "vitest";

import { extractHeadings } from "./markdown-headings";

const texts = (md: string) => extractHeadings(md).map((h) => h.text);
const levels = (md: string) => extractHeadings(md).map((h) => h.level);

describe("the #80 regression", () => {
  it("ignores ATX-looking lines inside a fenced code block", () => {
    const md = [
      "## Setup",
      "",
      "```bash",
      "## install deps",
      "npm ci",
      "## build",
      "```",
      "",
      "## Deploy",
    ].join("\n");

    expect(texts(md)).toEqual(["Setup", "Deploy"]);
  });

  it("keeps ids aligned with real headings when a fence contains hashes", () => {
    // The actual failure: with a phantom between them, "Deploy" inherited the id that
    // belonged to the phantom, so #deploy scrolled to the code block.
    const md = ["## Setup", "```", "## not a heading", "```", "## Deploy"].join("\n");
    const ids = extractHeadings(md).map((h) => h.id);
    expect(ids).toEqual(["setup", "deploy"]);
  });

  it("handles tilde fences", () => {
    const md = ["## Real", "~~~python", "## fake", "~~~", "## Also real"].join("\n");
    expect(texts(md)).toEqual(["Real", "Also real"]);
  });

  it("treats an unterminated fence as running to the end of the document", () => {
    // A truncated paste must not re-enable heading parsing for the rest of the page.
    const md = ["## Real", "```", "## fake", "## also fake"].join("\n");
    expect(texts(md)).toEqual(["Real"]);
  });

  it("does not let a shorter inner run close a longer fence", () => {
    const md = ["````", "```", "## still code", "````", "## Real"].join("\n");
    expect(texts(md)).toEqual(["Real"]);
  });

  it("ignores hashes in an info string", () => {
    const md = ["```js title=## nope", "## fake", "```", "## Real"].join("\n");
    expect(texts(md)).toEqual(["Real"]);
  });

  it("handles indented fences", () => {
    const md = ["## Real", "  ```", "  ## fake", "  ```", "## Second"].join("\n");
    expect(texts(md)).toEqual(["Real", "Second"]);
  });
});

describe("ATX parsing", () => {
  it("extracts h2 through h4 with their levels", () => {
    const md = ["## Two", "### Three", "#### Four"].join("\n");
    expect(levels(md)).toEqual([2, 3, 4]);
  });

  it("skips h1 and h5+, which the renderer gives no anchor", () => {
    const md = ["# One", "## Two", "##### Five"].join("\n");
    expect(texts(md)).toEqual(["Two"]);
  });

  it("requires a space after the hashes", () => {
    expect(texts("##NoSpace")).toEqual([]);
  });

  it("strips a closing hash run, which CommonMark treats as decoration", () => {
    expect(texts("## Title ##")).toEqual(["Title"]);
  });

  it("allows up to three leading spaces but not four", () => {
    expect(texts("   ## Indented")).toEqual(["Indented"]);
    expect(texts("    ## Code block, not a heading")).toEqual([]);
  });

  it("reports 1-based source lines", () => {
    const md = ["intro", "", "## Second", "body", "### Third"].join("\n");
    expect(extractHeadings(md).map((h) => h.line)).toEqual([3, 5]);
  });
});

describe("CRLF", () => {
  it("parses CRLF documents", () => {
    // The old `$`-anchored regexes matched nothing at all on CRLF, so a page authored on
    // Windows silently rendered no TOC whatsoever.
    const md = "## First\r\n\r\ntext\r\n\r\n### Second\r\n";
    expect(texts(md)).toEqual(["First", "Second"]);
  });

  it("is fence-aware on CRLF too", () => {
    const md = "## Real\r\n```\r\n## fake\r\n```\r\n## Second\r\n";
    expect(texts(md)).toEqual(["Real", "Second"]);
  });
});

describe("slugs", () => {
  it("slugifies punctuation and case", () => {
    expect(extractHeadings("## Hello, World!")[0].id).toBe("hello-world");
  });

  it("gives duplicate titles distinct ids, so the second link is reachable", () => {
    const ids = extractHeadings(["## Notes", "## Notes"].join("\n")).map((h) => h.id);
    expect(new Set(ids).size).toBe(2);
  });

  it("never emits an empty id", () => {
    // slugify("###") is "" — an empty id makes href="#" jump to the top of the page.
    for (const h of extractHeadings(["## ---", "## 你好"].join("\n"))) {
      expect(h.id.length).toBeGreaterThan(0);
    }
  });
});

describe("edge cases", () => {
  it("returns nothing for empty or heading-free input", () => {
    expect(extractHeadings("")).toEqual([]);
    expect(extractHeadings("just a paragraph\n\nand another")).toEqual([]);
  });

  it("does not treat a `#` mid-line as a heading", () => {
    expect(texts("see ## below")).toEqual([]);
  });

  it("keeps inline markup out of the anchor text sensibly", () => {
    const [h] = extractHeadings("## The `api()` helper");
    expect(h.text).toContain("api()");
    expect(h.id).not.toContain("`");
  });
});
