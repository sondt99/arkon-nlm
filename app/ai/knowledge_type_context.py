"""Build consistent Knowledge Type guidance for document ingestion."""

from __future__ import annotations

from typing import Optional


DESCRIPTION_LIMIT = 4_000
CUSTOM_HINTS_LIMIT = 20_000


def build_effective_extraction_hints(
    slug: Optional[str],
    name: Optional[str],
    description: Optional[str],
    extraction_hints: Optional[str],
) -> Optional[str]:
    """Combine domain defaults, category description, and admin hints.

    Existing databases may contain a seeded profile directly in
    ``extraction_hints``; identical sections are therefore de-duplicated.
    """
    from app.scripts.seed_security_kt_hints import _match_hints

    profile = _match_hints(slug or "")
    if not profile and name:
        profile = _match_hints(name.lower().replace(" ", "-"))
    if not profile and description:
        profile = _match_hints(description)

    description_text = (description or "").strip()[:DESCRIPTION_LIMIT]
    custom_text = (extraction_hints or "").strip()[:CUSTOM_HINTS_LIMIT]
    profile_text = (profile or "").strip()

    sections: list[str] = []
    if profile_text:
        sections.append("## Built-in domain preservation profile\n" + profile_text)
    if description_text:
        category = name or slug or "Uncategorized"
        sections.append(
            "## Category description as extraction guidance\n"
            f'Category: "{category}"\n'
            "Use this administrator-authored description to determine which "
            "entities, procedures, constraints, and source details are important:\n\n"
            + description_text
        )
    if custom_text and custom_text != profile_text:
        sections.append(
            "## Administrator extraction instructions\n"
            "These explicit instructions have the highest category-level priority:\n\n"
            + custom_text
        )

    return "\n\n".join(sections) or None
