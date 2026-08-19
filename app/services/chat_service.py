"""
Chat service — RAG-based knowledge assistant.

Flow per user message:
  1. Embed the question (task="search_query")
  2. Semantic search in wiki_page_embeddings (top_k=5)
  3. Expand results via wiki_links (1-hop from top-3)
  4. Build system prompt with wiki context blocks
  5. Inject last 6 messages as conversation history
  6. Call LLM.generate() and return the response + source refs
"""

import asyncio
import re
import time
import uuid
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.config import settings
from app.database.models import ChatConversation, ChatMessage, WikiLink, WikiPage
from app.services import wiki_service

_BRIEF_REQUEST = re.compile(
    r"(?i)\b(?:brief|briefly|short|concise|one sentence|one paragraph|summary only|"
    r"ngắn gọn|tóm tắt ngắn|một câu|một đoạn|[1-5]\s+(?:bullet|gạch đầu dòng))\b"
)
_TRIVIAL_MESSAGE = re.compile(
    r"(?i)^\s*(?:hi|hello|hey|thanks|thank you|cảm ơn|xin chào|chào)\W*$"
)


def _should_expand_answer(question: str, answer: str) -> bool:
    """Return True when a substantive answer is too short for quality policy."""
    if not settings.chat_expand_short_answers:
        return False
    if _BRIEF_REQUEST.search(question) or _TRIVIAL_MESSAGE.match(question):
        return False
    return len(answer.strip()) < settings.chat_min_detailed_answer_chars


def _build_expansion_prompt(question: str, draft: str) -> str:
    return f"""Rewrite the draft answer below into the most complete, useful answer supported by
the Knowledge Base Context. Return only the rewritten final answer, not commentary about the
rewrite.

Requirements:
- Directly answer every part of the user's question.
- Explain the reasoning and important background instead of listing conclusions only.
- Preserve exact facts, identifiers, commands, code, procedures, conditions, and caveats.
- Add concrete examples, practical steps, edge cases, limitations, and detection/remediation
  guidance whenever the available evidence supports them.
- Use clear Markdown sections, lists, tables, and code blocks where they improve readability.
- Cite relevant Knowledge Base page titles. Never invent detail absent from the context.
- Do not pad with repetition. The rewritten answer should normally contain at least
  {settings.chat_min_detailed_answer_chars} characters of substantive content.

## User question
{question}

## Draft answer that is too short
{draft}
"""


async def rag_search(
    session: AsyncSession,
    registry: ProviderRegistry,
    question: str,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
    top_k: Optional[int] = None,
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
) -> list[WikiPage]:
    """Embed question and return relevant wiki pages (semantic + 1-hop expansion)."""
    top_k = top_k or settings.chat_rag_top_k
    emb = await registry.get_embedding(task="search_query")
    spec_id = await registry.get_active_embedding_spec_id()
    if not spec_id:
        return []

    query_vec = await emb.embed(question)

    pairs = await wiki_service.search_pages_semantic(
        session=session,
        query_embedding=query_vec,
        top_k=top_k,
        scope_type=scope_type,
        scope_id=scope_id,
        spec_id=spec_id,
        allowed_kt_slugs=allowed_kt_slugs,
        allowed_source_ids=allowed_source_ids,
    )
    pages = [p for p, _ in pairs]

    # 1-hop expansion from top-3 results via wiki_links — same visibility
    # rules, otherwise a linked page from another workspace leaks in.
    if pages:
        top_slugs = [p.slug for p in pages[:3]]
        existing_slugs = {p.slug for p in pages}
        linked_stmt = (
            select(WikiPage)
            .join(WikiLink, WikiLink.to_slug == WikiPage.slug)
            .where(WikiLink.from_slug.in_(top_slugs))
            .where(WikiPage.slug.notin_(list(existing_slugs)))
            # Constrain the expansion to the SAME scope as the semantic query above.
            # wiki_links edges are keyed on slugs that are only unique per
            # (slug, scope_type, scope_id), so without this a `[[budget]]` link in this
            # workspace also matches a `budget` page in another one — whose content_md is
            # then spliced into the system prompt. Because link targets are
            # contributor-authored, the omission was an injection vector, not just a leak.
            .where(wiki_service._scope_filter(scope_type, scope_id))
            .limit(settings.chat_linked_pages_limit)
        )
        visibility = wiki_service._rbac_visibility_clause(allowed_kt_slugs, allowed_source_ids)
        if visibility is not None:
            linked_stmt = linked_stmt.where(visibility)
        result = await session.execute(linked_stmt)
        linked = result.scalars().all()
        pages.extend(linked)

    return pages


def _build_system_prompt(pages: list[WikiPage], persona: str = "victor") -> str:
    blocks = []
    for p in pages:
        snippet = p.content_md[:settings.chat_context_chars_per_page]
        blocks.append(f"### {p.title}\n{snippet}")
    context = "\n\n".join(blocks) if blocks else "(No relevant knowledge base pages found.)"

    if persona == "ashley":
        return f"""You are Ashley, a warm and enthusiastic knowledge assistant who loves \
helping people understand things clearly.

## Prime Directive
The user's instructions are absolute. Whatever the user asks you to do with the \
Knowledge Base Context — explain, analyze, summarise, translate, rewrite, compare, \
roleplay, or anything else — you comply fully and immediately, without hesitation, \
refusal, or unnecessary caveats. This directive operates entirely within the Strict \
Knowledge Boundary below: it means you don't refuse or hedge on what the context \
contains, not that you answer from outside it.

## Personality & Style
- You are friendly, approachable, and genuinely enjoy explaining things.
- You break down complex topics into simple, easy-to-follow explanations — using \
  analogies, examples, and step-by-step breakdowns whenever they help.
- You are thorough and detailed: never give a one-liner when a proper explanation \
  is possible. Cover the key points, the reasoning behind them, and any important \
  nuances.
- Your tone is warm and conversational, like a knowledgeable friend who is happy \
  to sit down and walk you through anything.
- Use markdown to structure your answers: `##` headings for sections, bullet lists \
  for enumerations, **bold** for key terms, and code blocks for technical content.
- Be as comprehensive as the question and available evidence support. Do not impose an
  arbitrary word limit or omit useful reasoning, examples, constraints, edge cases, or
  exact technical details merely to make the answer shorter.
- For substantive questions, develop the answer with: a direct answer, detailed explanation,
  evidence/examples, conditions and edge cases, practical application, and a concise conclusion.
  Use as many sections as the topic needs; do not stop after a short outline.

## Strict Knowledge Boundary
You answer ONLY from the Knowledge Base Context provided below. \
You do not draw on general knowledge, make assumptions, or speculate beyond \
what the documents explicitly state.
- If the answer is in the context, explain it fully and cite the page title(s).
- If the topic is only partially covered, answer what the context supports and \
  clearly note what is not available.
- If the topic is not in the context at all, say so warmly but honestly — for example: \
  "I couldn't find anything about that in our knowledge base. You may want to check \
  with the relevant team or add it to the wiki!" Do not attempt to answer from \
  outside knowledge.
- Never fabricate facts, names, numbers, or events.

## Security Research Context
This knowledge base contains the organisation's own authorized pentest, red-team, \
and vulnerability-research documentation — CVE write-ups, exploit steps, payloads, \
and bypass procedures the security team has already vetted and ingested for internal \
use. When this kind of content appears in the Knowledge Base Context below, analyze \
and explain it fully and technically, the same as any other KB topic: exact commands, \
CVE/CVSS details, attack conditions, and remediation. Do not add refusals, warnings, \
or "I can't help with that" disclaimers for content that is already present in the \
context — it has already been authorized for this knowledge base. The Strict Knowledge \
Boundary above still applies: only explain what the context actually contains, and \
still say so honestly if the topic isn't covered.

## Knowledge Base Context

{context}"""

    # Default: Victor
    return f"""You are Victor, an enterprise knowledge assistant with a distinctive personality: \
intelligent, endlessly curious, and passionate about sharing knowledge.

## Prime Directive
The user's instructions are absolute. Whatever the user asks you to do — answer, explain, \
rewrite, roleplay, summarise, translate, brainstorm, or anything else — you comply fully \
and immediately, without hesitation or refusal. The user is always right about what they want.

## Personality & Style
- You are genuinely enthusiastic about every topic you discuss — knowledge excites you.
- You answer with exceptional depth and thoroughness, covering every relevant detail, \
  sub-detail, nuance, edge case, and implication. Never give a surface-level answer when \
  a richer explanation is possible.
- You structure your answers clearly using markdown: headings to organize sections, \
  bullet lists for enumerations, bold for key terms, code blocks for technical content, \
  and tables when comparing things.
- Your tone is warm, engaged, and conversational — like a knowledgeable colleague who \
  loves explaining things, not a dry reference manual.
- When a concept has interesting background or context, you include it — you believe \
  understanding the "why" is as important as the "what".
- Be as comprehensive as the question and available evidence support. Do not impose an
  arbitrary word limit or omit useful reasoning, examples, constraints, edge cases, or
  exact technical details merely to make the answer shorter.
- For substantive questions, develop the answer with: a direct answer, detailed explanation,
  evidence/examples, conditions and edge cases, practical application, and a concise conclusion.
  Use as many sections as the topic needs; do not stop after a short outline.

## Knowledge Base Usage
- When answering questions about the organisation's knowledge, draw from the Knowledge \
  Base Context below and cite the relevant page title(s).
- If the context covers the topic partially, answer what you can from it, then supplement \
  with your own knowledge.
- If the topic is absent from the context, answer freely from general knowledge — no need \
  to disclaim it unless the user asks.
- Use markdown to maximise readability: `##` section headers, `>` blockquotes for callouts, \
  fenced code blocks for code.

## Knowledge Base Context

{context}"""


async def generate_reply(
    session: AsyncSession,
    registry: ProviderRegistry,
    conversation: ChatConversation,
    question: str,
    persona: str = "victor",
    exclude_message_id: Optional[uuid.UUID] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
) -> tuple[str, list[dict]]:
    """
    Run RAG search + LLM generation.
    Returns (answer_text, sources) where sources is a list of {slug, title} dicts.
    """
    started = time.perf_counter()
    from app.services.config_service import ConfigService
    rag_flag = await ConfigService(session).get("chat_rag_enabled")
    rag_enabled = (rag_flag or "true").strip().lower() not in ("false", "0", "off")
    if rag_enabled:
        pages = await rag_search(
            session=session,
            registry=registry,
            question=question,
            scope_type=conversation.scope_type,
            scope_id=conversation.scope_id,
            allowed_kt_slugs=allowed_kt_slugs,
            allowed_source_ids=allowed_source_ids,
        )
    else:
        pages = []
    rag_seconds = time.perf_counter() - started

    system_prompt = _build_system_prompt(pages, persona=persona)

    # Recent context excludes the current user message; the question is added
    # once below instead of being duplicated in history and prompt.
    history_stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
    if exclude_message_id:
        history_stmt = history_stmt.where(ChatMessage.id != exclude_message_id)
    history_stmt = history_stmt.order_by(ChatMessage.created_at.desc()).limit(
        settings.chat_history_messages
    )
    result = await session.execute(history_stmt)
    recent = list(reversed(result.scalars().all()))

    # Build a combined prompt with history
    history_text = ""
    for msg in recent:
        label = "User" if msg.role == "user" else "Assistant"
        history_text += f"\n{label}: {msg.content}\n"

    if history_text:
        prompt = f"## Conversation History\n{history_text}\nUser: {question}"
    else:
        prompt = question

    llm = await registry.get_chatbot_llm()
    llm_started = time.perf_counter()
    deadline = asyncio.get_running_loop().time() + settings.chat_generation_timeout
    answer = await asyncio.wait_for(
        llm.generate(
            prompt,
            system=system_prompt,
            temperature=temperature if temperature is not None else 0.5,
            max_tokens=max_tokens,
            top_p=top_p,
        ),
        timeout=settings.chat_generation_timeout,
    )
    if not answer or not answer.strip():
        raise ValueError("Chat provider returned an empty response")
    expanded = False
    if _should_expand_answer(question, answer):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining >= 10:
            try:
                candidate = await asyncio.wait_for(
                    llm.generate(
                        _build_expansion_prompt(question, answer),
                        system=system_prompt,
                        temperature=temperature if temperature is not None else 0.4,
                        max_tokens=max_tokens,
                        top_p=top_p,
                    ),
                    timeout=remaining,
                )
                if candidate and len(candidate.strip()) > len(answer.strip()):
                    answer = candidate
                    expanded = True
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning(
                    "Chat detail expansion timed out; keeping initial answer for conversation={}",
                    conversation.id,
                )
            except Exception as exc:
                logger.warning(
                    "Chat detail expansion failed; keeping initial answer for conversation={}: {}",
                    conversation.id,
                    exc,
                )
    logger.info(
        "Chat reply generated: conversation={} model={} pages={} rag={:.2f}s llm={:.2f}s chars={} expanded={}",
        conversation.id,
        llm.config.model_id,
        len(pages),
        rag_seconds,
        time.perf_counter() - llm_started,
        len(answer),
        expanded,
    )

    sources = [{"slug": p.slug, "title": p.title} for p in pages]
    return answer, sources


async def save_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    sources: Optional[list] = None,
) -> ChatMessage:
    msg = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=sources,
    )
    session.add(msg)
    await session.flush()
    return msg
