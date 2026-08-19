/**
 * #91 — the file preview stripped frontmatter and every `--xxxx--` sequence, stored the
 * *mutated* text, and the Copy button copied that.
 *
 * Copying `SKILL.md` therefore produced a file missing the YAML frontmatter block the format
 * requires, and any non-markdown file that happened to contain `--abcd--` came out altered.
 * The strip is a presentation concern; the stored value has to stay verbatim.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SkillFileExplorer } from "./skill-file-explorer";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const SKILL_MD = `---
name: pdf-extract
description: Pull tables out of PDFs
---

# PDF Extract

Body text with a --a1b2-- marker in it.
`;

let clipboard: string[] = [];

function install(files: { path: string; is_text: boolean; size: number }[], content: string) {
  mockApi.mockImplementation(((path: string) => {
    const p = String(path);
    if (p.includes("/files/content")) return Promise.resolve({ content });
    if (p.includes("/files")) return Promise.resolve(files);
    return Promise.resolve({});
  }) as unknown as typeof api);
}

beforeEach(() => {
  vi.clearAllMocks();
  clipboard = [];
});

/**
 * `userEvent.setup()` installs its own clipboard stub, so the spy has to go on afterwards.
 * jsdom exposes `navigator.clipboard` as a getter, hence defineProperty rather than assignment.
 */
function setupUser() {
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: vi.fn((text: string) => {
        clipboard.push(text);
        return Promise.resolve();
      }),
    },
  });
  return user;
}

describe("file preview vs. file content (#91)", () => {
  it("copies SKILL.md verbatim, frontmatter and markers included", async () => {
    install([{ path: "SKILL.md", is_text: true, size: SKILL_MD.length }], SKILL_MD);

    const user = setupUser();
    render(<SkillFileExplorer skillId="s1" version={1} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /Copy/i })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Copy/i }));

    expect(clipboard).toHaveLength(1);
    expect(clipboard[0]).toBe(SKILL_MD);
    expect(clipboard[0]).toContain("name: pdf-extract");
    expect(clipboard[0]).toContain("--a1b2--");
  });

  it("still hides frontmatter and markers from the rendered markdown", async () => {
    install([{ path: "SKILL.md", is_text: true, size: SKILL_MD.length }], SKILL_MD);
    render(<SkillFileExplorer skillId="s1" version={1} />);

    await waitFor(() => expect(screen.getByText("PDF Extract")).toBeInTheDocument());
    expect(screen.queryByText(/name: pdf-extract/)).not.toBeInTheDocument();
    expect(screen.queryByText(/--a1b2--/)).not.toBeInTheDocument();
  });

  it("shows a non-markdown file exactly as stored", async () => {
    // The old strip ran on every file type, so a shell script opening with `---` or holding a
    // `--abcd--` token was silently rewritten in the preview.
    const script = "---\nset -euo pipefail\necho --a1b2--\n";
    install([{ path: "run.sh", is_text: true, size: script.length }], script);

    const user = setupUser();
    render(<SkillFileExplorer skillId="s1" version={1} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /Copy/i })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Copy/i }));
    expect(clipboard[0]).toBe(script);
  });
});
