"""
Page merge logic for the MRP pipeline.

When an UPDATE targets a page that already has content from a different source,
the new content is LLM-merged with the existing content rather than overwriting.

Three layers of protection (inspired by LLM Wiki):
  1. Source IDs are always unioned — never lost.
  2. Body merge via LLM — produces a coherent unified page.
  3. Sanity check — reject if merged body is too short (truncation guard).

Fallback: any LLM failure or sanity-check rejection keeps both inputs in a
deterministic markdown document, so source knowledge is never dropped.
"""

import asyncio

from loguru import logger

from app.ai.providers.base import LLMProvider
from app.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# If the LLM's merged body is shorter than this fraction of the longest input,
# reject the merge — the LLM almost certainly stripped content.
BODY_SHRINK_THRESHOLD = settings.mrp_merge_min_body_ratio

MERGE_TIMEOUT = settings.mrp_merge_timeout

MERGE_SYSTEM = """\
You are a wiki page merger. You receive two versions of the same wiki page:
- EXISTING: the current version in the knowledge base (may contain content from earlier sources)
- INCOMING: a new version generated from a different source document

Your job is to produce a SINGLE unified page that preserves ALL factual content
from BOTH versions. Rules:

1. KEEP all facts, numbers, procedures, names from both versions.
2. REMOVE exact duplicates — if both versions state the same fact, keep it once.
3. ORGANIZE coherently — use clear H2 sections, opening paragraph, See also.
4. PRESERVE [[wikilinks]] from both versions.
5. PRESERVE image markers ![caption](image://<uuid>) from both versions.
6. Write in the SAME LANGUAGE as the existing content.
7. Do NOT summarize or condense — the merged page should be AT LEAST as long
   as the longer of the two inputs.
8. Do NOT add any facts not present in either version.

Return ONLY the merged markdown content, no other text.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lossless_merge_fallback(existing_content: str, new_content: str) -> str:
    """Combine both inputs without interpretation or data loss."""
    return f"{existing_content.rstrip()}\n\n---\n\n{new_content.lstrip()}"

async def merge_page_content(
    llm: LLMProvider,
    existing_content: str,
    new_content: str,
    slug: str,
) -> str:
    """
    Merge new_content into existing_content using LLM.

    Returns merged content on success, or both inputs on failure.
    """
    # Fast path: if existing is empty or very short, just use new content
    if not existing_content or len(existing_content.strip()) < 50:
        return new_content

    # Fast path: identical content
    if existing_content.strip() == new_content.strip():
        return new_content

    prompt = (
        f"Merge these two versions of wiki page `{slug}`:\n\n"
        f"## EXISTING VERSION\n\n{existing_content}\n\n"
        f"---\n\n"
        f"## INCOMING VERSION\n\n{new_content}\n\n"
        f"---\n\n"
        f"Produce the merged page now. Return ONLY the markdown content."
    )

    try:
        raw = await asyncio.wait_for(
            llm.generate(prompt, system=MERGE_SYSTEM, temperature=0.1),
            timeout=MERGE_TIMEOUT,
        )
        merged = raw.strip()

        # Sanity check: merged body must not be too short
        max_input_len = max(len(existing_content), len(new_content))
        min_acceptable = int(max_input_len * BODY_SHRINK_THRESHOLD)

        if len(merged) < min_acceptable:
            logger.warning(
                f"MRP MERGE rejected for '{slug}': merged={len(merged)} chars, "
                f"threshold={min_acceptable} (max input={max_input_len}). "
                f"Using lossless fallback."
            )
            return lossless_merge_fallback(existing_content, new_content)

        logger.info(
            f"MRP MERGE success for '{slug}': "
            f"existing={len(existing_content)}, new={len(new_content)}, "
            f"merged={len(merged)} chars"
        )
        return merged

    except Exception as exc:
        logger.warning(f"MRP MERGE failed for '{slug}': {exc}. Using lossless fallback.")
        return lossless_merge_fallback(existing_content, new_content)
