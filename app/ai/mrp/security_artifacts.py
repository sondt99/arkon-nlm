"""Deterministic preservation of high-value offensive-security artifacts.

LLM extraction is useful for concepts, but it must not be the only path for
commands, payloads, code and version-specific syntax.  This module extracts
those values directly from the source and routes them to relevant plan pages.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


_SECURITY_MARKERS = (
    "pentest", "penetration", "offensive", "redteam", "red-team", "exploit",
    "sqli", "injection", "bypass", "vulnerability", "cve", "ttp", "att&ck",
)

_COMMAND_OR_CODE = re.compile(
    r"(?ix)"
    r"(?:\b(?:select|union\s+all\s+select|exec(?:ute)?|"
    r"xp_cmdshell|openquery|sleep|benchmark|information_schema|into\s+outfile)\b)"
    r"|(?:^|\s)(?:nmap|sqlmap|curl|wget|nc|netcat|msfconsole|msfvenom|"
    r"powershell|python\d*|bash|sh|cmd(?:\.exe)?|rundll32|regsvr32)\s+(?=[\-/'\".\\\d])"
    r"|(?:\$[A-Za-z_][\w]*\s*=|[A-Za-z_][\w.]*\([^\n]{1,300}\))"
    r"|(?:\bCVE-\d{4}-\d{4,7}\b|\bT\d{4}(?:\.\d{3})?\b)"
)

_STOPWORDS = {
    "about", "advanced", "attack", "attacks", "concept", "entity", "from",
    "function", "page", "source", "system", "technique", "techniques", "the",
    "this", "tool", "using", "with",
}


def is_security_domain(kt_slug: str | None, extraction_hints: str | None = None) -> bool:
    value = f"{kt_slug or ''} {extraction_hints or ''}".lower()
    return any(marker in value for marker in _SECURITY_MARKERS)


def _artifact_kind(text: str, language: str = "") -> str:
    lowered = f"{language} {text}".lower()
    if re.search(r"\b(?:select|union|insert|update|delete|exec|sleep|benchmark)\b", lowered):
        return "payload"
    if re.search(r"(?:^|\s)(?:nmap|sqlmap|curl|wget|nc|powershell|bash|cmd)\s+", lowered):
        return "command"
    if re.search(r"\b(?:cve-\d{4}-\d+|t\d{4}(?:\.\d{3})?)\b", lowered):
        return "identifier"
    return "code"


def extract_security_artifacts(full_text: str, limit: int = 500) -> list[dict[str, Any]]:
    """Extract exact security artifacts with offsets and stable hashes."""
    if not full_text:
        return []

    candidates: list[tuple[int, str, str, str]] = []
    fenced_ranges: list[tuple[int, int]] = []
    for match in re.finditer(r"```([^\n`]*)\n(.*?)```", full_text, re.DOTALL):
        language = match.group(1).strip()[:30]
        value = match.group(2).strip()
        fenced_ranges.append((match.start(), match.end()))
        if 8 <= len(value) <= 12_000:
            candidates.append((match.start(2), value, _artifact_kind(value, language), language))

    offset = 0
    for raw_line in full_text.splitlines(keepends=True):
        line = raw_line.strip()
        inside_fence = any(start <= offset < end for start, end in fenced_ranges)
        if not inside_fence and 8 <= len(line) <= 2_000 and _COMMAND_OR_CODE.search(line):
            candidates.append((offset + raw_line.find(line), line, _artifact_kind(line), ""))
        offset += len(raw_line)

    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for start, text, kind, language in sorted(candidates, key=lambda item: item[0]):
        normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
        digest = hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        context_start = max(0, start - 600)
        context_end = min(len(full_text), start + len(text) + 600)
        artifacts.append({
            "kind": kind,
            "text": normalized,
            "language": language,
            "absolute_offset": start,
            "length": len(normalized),
            "sha256": digest,
            "context": full_text[context_start:context_end],
        })
        if len(artifacts) >= limit:
            break
    return artifacts


def artifacts_for_page(
    plan_item: dict[str, Any],
    artifacts: list[dict[str, Any]],
    limit: int = 12,
    char_budget: int = 20_000,
) -> list[dict[str, Any]]:
    """Route artifacts by lexical overlap with a planned page's subject."""
    if plan_item.get("page_type") == "source":
        return []
    subject = " ".join([
        str(plan_item.get("slug") or "").replace("-", " ").replace("/", " "),
        str(plan_item.get("title") or ""),
        " ".join(str(v) for v in plan_item.get("entity_names", [])),
    ]).lower()
    terms = {
        term for term in re.findall(r"[a-z0-9_$.-]{4,}", subject)
        if term not in _STOPWORDS
    }
    if not terms:
        return []

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for artifact in artifacts:
        haystack = f"{artifact.get('text', '')} {artifact.get('context', '')}".lower()
        hits = sum(1 for term in terms if term in haystack)
        if hits:
            ranked.append((-hits, int(artifact.get("absolute_offset") or 0), artifact))
    ranked.sort(key=lambda row: (row[0], row[1]))
    selected: list[dict[str, Any]] = []
    used = 0
    for _, _, artifact in ranked:
        size = len(str(artifact.get("text") or ""))
        if selected and used + size > char_budget:
            continue
        selected.append(artifact)
        used += size
        if len(selected) >= limit or used >= char_budget:
            break
    return selected


def route_artifacts_to_pages(
    pages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    per_page_limit: int = 12,
    per_page_char_budget: int = 20_000,
) -> dict[str, list[dict[str, Any]]]:
    """Assign each artifact to the single most relevant planned page.

    A page-local matcher causes the same command to be copied into many related
    articles. Global routing keeps the lossless guarantee without duplicating
    an artifact across the wiki.
    """
    candidates = [page for page in pages if page.get("page_type") != "source" and page.get("slug")]
    routes: dict[str, list[dict[str, Any]]] = {str(page["slug"]): [] for page in candidates}
    used_chars: dict[str, int] = {slug: 0 for slug in routes}

    for artifact in artifacts:
        ranked: list[tuple[int, int, str]] = []
        haystack = f"{artifact.get('text', '')} {artifact.get('context', '')}".lower()
        for page in candidates:
            subject = " ".join([
                str(page.get("slug") or "").replace("-", " ").replace("/", " "),
                str(page.get("title") or ""),
                " ".join(str(v) for v in page.get("entity_names", [])),
            ]).lower()
            terms = {
                term for term in re.findall(r"[a-z0-9_$.-]{4,}", subject)
                if term not in _STOPWORDS
            }
            hits = sum(1 for term in terms if term in haystack)
            if hits:
                ranked.append((-hits, int(page.get("priority") or 99), str(page["slug"])))
        ranked.sort()

        size = len(str(artifact.get("text") or ""))
        for _, _, slug in ranked:
            if len(routes[slug]) >= per_page_limit:
                continue
            if routes[slug] and used_chars[slug] + size > per_page_char_budget:
                continue
            routes[slug].append(artifact)
            used_chars[slug] += size
            break

    return routes


def format_artifacts_for_prompt(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "(no exact security artifacts assigned to this page)"
    blocks = []
    for index, artifact in enumerate(artifacts, 1):
        language = artifact.get("language") or "text"
        blocks.append(
            f"{index}. {str(artifact.get('kind', 'code')).upper()} "
            f"at source offset {artifact.get('absolute_offset')} "
            f"(sha256:{str(artifact.get('sha256', ''))[:12]})\n"
            f"```{language}\n{artifact.get('text', '')}\n```"
        )
    return "\n\n".join(blocks)


def preserve_missing_artifacts(content_md: str, artifacts: list[dict[str, Any]]) -> str:
    """Append exact assigned artifacts that the model omitted.

    This deterministic last mile is intentionally lossless.  It does not ask
    another model to reproduce syntax that already exists in the source.
    """
    missing = [a for a in artifacts if str(a.get("text") or "") not in content_md]
    if not missing:
        return content_md
    lines = [content_md.rstrip(), "", "## Exact commands and payloads", ""]
    for artifact in missing:
        language = artifact.get("language") or "text"
        lines.extend([
            f"Source offset `{artifact.get('absolute_offset')}` · "
            f"`sha256:{str(artifact.get('sha256', ''))[:12]}`",
            f"```{language}",
            str(artifact.get("text") or ""),
            "```",
            "",
        ])
    return "\n".join(lines).rstrip()
