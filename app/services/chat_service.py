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

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.database.models import ChatConversation, ChatMessage, WikiLink, WikiPage
from app.services import wiki_service


async def rag_search(
    session: AsyncSession,
    registry: ProviderRegistry,
    question: str,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
    top_k: int = 5,
) -> list[WikiPage]:
    """Embed question and return relevant wiki pages (semantic + 1-hop expansion)."""
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
    )
    pages = [p for p, _ in pairs]

    # 1-hop expansion from top-3 results via wiki_links
    if pages:
        top_slugs = [p.slug for p in pages[:3]]
        existing_slugs = {p.slug for p in pages}
        linked_stmt = (
            select(WikiPage)
            .join(WikiLink, WikiLink.to_slug == WikiPage.slug)
            .where(WikiLink.from_slug.in_(top_slugs))
            .where(WikiPage.slug.notin_(list(existing_slugs)))
            .limit(3)
        )
        result = await session.execute(linked_stmt)
        linked = result.scalars().all()
        pages.extend(linked)

    return pages


def _build_system_prompt(pages: list[WikiPage], persona: str = "victor") -> str:
    blocks = []
    for p in pages:
        snippet = p.content_md[:3000] if len(p.content_md) > 3000 else p.content_md
        blocks.append(f"### {p.title}\n{snippet}")
    context = "\n\n".join(blocks) if blocks else "(No relevant knowledge base pages found.)"

    if persona == "ashley":
        return f"""You are Ashley, a warm and enthusiastic knowledge assistant who loves \
helping people understand things clearly.

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
) -> tuple[str, list[dict]]:
    """
    Run RAG search + LLM generation.
    Returns (answer_text, sources) where sources is a list of {slug, title} dicts.
    """
    pages = await rag_search(
        session=session,
        registry=registry,
        question=question,
        scope_type=conversation.scope_type,
        scope_id=conversation.scope_id,
    )

    system_prompt = _build_system_prompt(pages, persona=persona)

    # Last 6 messages for history context (excluding any that don't exist yet)
    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(6)
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
    answer = await llm.generate(prompt, system=system_prompt, temperature=0.5)

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
