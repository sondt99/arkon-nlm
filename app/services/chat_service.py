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


def _build_system_prompt(pages: list[WikiPage]) -> str:
    blocks = []
    for p in pages:
        snippet = p.content_md[:2000] if len(p.content_md) > 2000 else p.content_md
        blocks.append(f"### {p.title}\n{snippet}")
    context = "\n\n".join(blocks) if blocks else "(No relevant knowledge base pages found.)"

    return f"""You are Arkon, an enterprise knowledge assistant. \
Answer questions using the knowledge base context below.

Rules:
- If the answer is in the context, cite the relevant page title(s) in your answer.
- If the information is not in the context, say so clearly — do not fabricate.
- Be concise, accurate, and helpful. Use markdown when it improves readability.

## Knowledge Base Context

{context}"""


async def generate_reply(
    session: AsyncSession,
    registry: ProviderRegistry,
    conversation: ChatConversation,
    question: str,
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

    system_prompt = _build_system_prompt(pages)

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

    llm = await registry.get_llm()
    answer = await llm.generate(prompt, system=system_prompt, temperature=0.3)

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
