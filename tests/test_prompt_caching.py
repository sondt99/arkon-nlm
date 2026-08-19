"""Prompt caching for the REFINE writers (#90, item 1).

Every page in a compilation plan used to re-send the whole source document at full input
price — up to 30 times for one upload — and could not have been cached even with a
breakpoint added, because the volatile per-page fields (slug, title, evidence, existing
body) sat *in front of* the repeated block. A cache entry covers the prompt up to the
breakpoint, so a shared suffix is worth nothing.

Three things therefore have to hold, and each has its own test below:
  1. the invariant block is the head of the prompt, and the security boundary still
     precedes every untrusted block while the trusted instructions still close it;
  2. the head is byte-identical across the pages of one phase — which includes the
     envelope nonce, so the phase has to share one;
  3. the breakpoint is only requested when it can actually do something: the provider
     caches at all, the entry will be read back, and the prefix clears the model's minimum
     cacheable length (4096 tokens on Opus 4.8 — a breakpoint under it is silently ignored
     and only the cache-write premium remains).
"""

from types import SimpleNamespace

import pytest

from app.ai import agent_protocol
from app.ai.mrp import writer
from app.ai.providers.anthropic_provider import (
    AnthropicLLM,
    min_cacheable_prefix_tokens,
)
from app.ai.providers.base import LLMProvider, ProviderConfig, ProviderType

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


_WRITER_BODY = "# Page\n\nA body long enough to satisfy the writer's own validation check."


class _PlainLLM(LLMProvider):
    """Implements only what the ABC requires, so the base-class defaults are under test."""

    def __init__(self, model_id="gemini-3.1-pro"):
        super().__init__(ProviderConfig(provider=ProviderType.GOOGLE, model_id=model_id))
        self.prompts: list[str] = []

    async def generate(self, prompt, system=None, max_tokens=None, temperature=0.7, top_p=None):
        self.prompts.append(prompt)
        return _WRITER_BODY

    async def test_connection(self):
        return True, "stub"


class _RecordingLLM(LLMProvider):
    """Records the two call paths separately so a test can tell which one was taken."""

    def __init__(self, model_id="claude-opus-4-8", min_prefix_tokens=None,
                 fail_first_cached=False):
        super().__init__(ProviderConfig(provider=ProviderType.ANTHROPIC, model_id=model_id))
        self._min_prefix_tokens = min_prefix_tokens
        self.fail_first_cached = fail_first_cached
        self.plain_prompts: list[str] = []
        self.cached_calls: list[tuple[str, str]] = []

    def cacheable_prefix_min_tokens(self):
        return self._min_prefix_tokens

    async def generate(self, prompt, system=None, max_tokens=None, temperature=0.7, top_p=None):
        self.plain_prompts.append(prompt)
        return _WRITER_BODY

    async def generate_cached(
        self, cacheable_prefix, prompt, system=None, max_tokens=None,
        temperature=0.7, top_p=None,
    ):
        self.cached_calls.append((cacheable_prefix, prompt))
        if self.fail_first_cached and len(self.cached_calls) == 1:
            raise TimeoutError("gateway timeout")
        return _WRITER_BODY

    async def test_connection(self):
        return True, "stub"


class _ToolLLM(_RecordingLLM):
    """Complex-writer stand-in: finishes on the first turn."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tool_messages: list[list[dict]] = []

    async def generate_with_tools(
        self, messages, tools, system=None, max_tokens=None, temperature=0.2, top_p=None,
    ):
        from app.ai.agent_protocol import AssistantTurn, ToolCall

        self.tool_messages.append(messages)
        return AssistantTurn(
            text=None,
            tool_calls=[ToolCall(
                id="call-1",
                name="finish",
                arguments={
                    "content_md": "# Page\n\nA body long enough to pass validation checks.",
                    "summary": "One line.",
                },
            )],
            finish_reason="tool_use",
        )


class _Tracker:
    async def update(self, *_args, **_kwargs):
        return None


def _plan(pages):
    return SimpleNamespace(plan_json={"pages": pages, "_claims": []})


def _page(slug, page_type="concept"):
    return {
        "action": "CREATE", "slug": slug, "title": slug.split("/")[-1],
        "page_type": page_type, "entity_names": [], "priority": 1,
    }


def _source():
    return SimpleNamespace(id="source-id", scope_type="global", scope_id=None)


async def _refine(llm, full_text, pages, **kwargs):
    from unittest.mock import AsyncMock, patch

    with patch("app.services.wiki_service.list_pages", AsyncMock(return_value=[])):
        return await writer.run_refine_phase(
            session=object(), source=_source(), plan=_plan(pages), chunk_extracts=[],
            full_text=full_text, llm=llm, embedding_provider=None, kt_slug=None,
            tracker=_Tracker(), **kwargs,
        )


# Long enough to clear Opus 4.8's 4096-token minimum under the deliberately pessimistic
# 4-chars-per-token gate, and short enough to fit the per-page budget without pruning.
_CACHEABLE_DOC = "Đoạn văn bản nguồn. " * 900


def test_the_gate_document_is_the_size_this_module_assumes():
    """Guards the constants the rest of the file depends on."""
    assert len(_CACHEABLE_DOC) >= 4096 * writer._CACHE_GATE_CHARS_PER_TOKEN
    assert len(_CACHEABLE_DOC) <= writer._page_source_budget(
        "claude-opus-4-8", "concept", [],
    )


# ---------------------------------------------------------------------------
# 1. Ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_repeated_block_is_the_head_of_the_simple_prompt():
    """The invariant head has to come first or the breakpoint covers nothing reusable."""
    llm = _RecordingLLM()
    await writer._write_page_simple(
        llm, _page("concept/a"), [{"statement": "s", "subject": "Budget"}],
        "prior body", all_plan_slugs=["concept/a", "concept/b"],
        source_context="UNIQUE-SOURCE-MARKER", domain_hints="keep numbers",
    )

    prompt = llm.plain_prompts[0]
    assert prompt.index("UNIQUE-SOURCE-MARKER") < prompt.index("## Page specification")
    assert prompt.index("UNIQUE-SOURCE-MARKER") < prompt.index("## Evidence checklist")
    assert prompt.index("UNIQUE-SOURCE-MARKER") < prompt.index("prior body")
    # The two properties the injection-boundary suite pins, restated as a regression guard
    # for this reordering specifically.
    assert prompt.index("Security boundary") < prompt.index("UNIQUE-SOURCE-MARKER")
    assert prompt.index("UNIQUE-SOURCE-MARKER") < prompt.index("## Instructions")


def test_the_repeated_block_is_the_head_of_the_complex_prompt():
    prefix, suffix = writer._build_complex_initial_parts(
        plan_item=_page("concept/a"), nonce="deadbeef", evidence_count=0,
        evidence_blocks="", existing_content="prior body",
        all_plan_slugs=["concept/a", "concept/b"], source_context="UNIQUE-SOURCE-MARKER",
        domain_hints="keep numbers", security_artifacts_block="",
    )

    assert "UNIQUE-SOURCE-MARKER" in prefix
    assert "keep numbers" in prefix
    assert "Security boundary" in prefix
    assert prefix.index("Security boundary") < prefix.index("UNIQUE-SOURCE-MARKER")
    # Nothing page-specific may sit in the shared head.
    assert "concept/a" not in prefix
    assert "prior body" not in prefix
    assert "## Instructions" in suffix


def test_the_complex_initial_message_is_still_exactly_prefix_plus_suffix():
    """The uncached path must send byte-identical bytes to the cached one."""
    kwargs = dict(
        plan_item=_page("concept/a"), nonce="deadbeef", evidence_count=0,
        evidence_blocks="", existing_content=None, all_plan_slugs=["concept/a"],
        source_context="source text", domain_hints=None, security_artifacts_block="",
    )
    prefix, suffix = writer._build_complex_initial_parts(**kwargs)

    assert writer._build_complex_initial_msg(**kwargs) == prefix + suffix


# ---------------------------------------------------------------------------
# 2. Byte-identical head across pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_page_in_a_phase_sends_the_same_cached_prefix():
    llm = _RecordingLLM(min_prefix_tokens=4096)
    pages = [_page(f"concept/p{index}") for index in range(4)]

    await _refine(llm, _CACHEABLE_DOC, pages)

    assert len(llm.cached_calls) == 4
    prefixes = {prefix for prefix, _ in llm.cached_calls}
    assert len(prefixes) == 1, "the cacheable head differs between pages"
    suffixes = {suffix for _, suffix in llm.cached_calls}
    assert len(suffixes) == 4, "the per-page tail was accidentally shared"


@pytest.mark.asyncio
async def test_a_per_call_nonce_would_have_broken_the_shared_prefix():
    """The envelope delimiter lives inside the head, so the phase has to share one nonce.

    Asserted as a property of the prefix rather than of the nonce plumbing: any future
    change that reintroduces a per-page delimiter fails here.
    """
    llm = _RecordingLLM(min_prefix_tokens=4096)
    await _refine(llm, _CACHEABLE_DOC, [_page("concept/a"), _page("concept/b")])

    first, second = (prefix for prefix, _ in llm.cached_calls)
    assert "<untrusted_document_" in first
    assert first == second


@pytest.mark.asyncio
async def test_a_writer_called_directly_still_mints_its_own_nonce():
    """Nothing outside run_refine_phase inherits the shared delimiter."""
    llm = _RecordingLLM()

    async def _prompt():
        await writer._write_page_simple(
            llm, _page("concept/a"), [], None, all_plan_slugs=["concept/a"],
            source_context="source text",
        )
        return llm.plain_prompts[-1]

    def _tag(text):
        start = text.index("<untrusted_document_")
        return text[start:text.index(">", start)]

    assert _tag(await _prompt()) != _tag(await _prompt())


# ---------------------------------------------------------------------------
# 3. The gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_provider_without_prompt_caching_keeps_the_single_block_path():
    """Google/OpenAI/Ollama return None from the hook and must be untouched by all this."""
    llm = _RecordingLLM(min_prefix_tokens=None)

    await _refine(llm, _CACHEABLE_DOC, [_page("concept/a"), _page("concept/b")])

    assert llm.cached_calls == []
    assert len(llm.plain_prompts) == 2


@pytest.mark.asyncio
async def test_a_single_page_plan_does_not_pay_the_cache_write_premium():
    """A cache write costs 1.25x on the eligible tokens; with one page it is never read."""
    llm = _RecordingLLM(min_prefix_tokens=4096)

    await _refine(llm, _CACHEABLE_DOC, [_page("concept/only")])

    assert llm.cached_calls == []
    assert len(llm.plain_prompts) == 1


@pytest.mark.asyncio
async def test_a_document_below_the_models_minimum_is_not_marked():
    """Under 4096 tokens on Opus 4.8 the breakpoint is silently dropped and buys nothing."""
    llm = _RecordingLLM(min_prefix_tokens=4096)

    await _refine(llm, "Short source.", [_page("concept/a"), _page("concept/b")])

    assert llm.cached_calls == []
    assert len(llm.plain_prompts) == 2


@pytest.mark.asyncio
async def test_a_document_that_has_to_be_pruned_is_not_marked():
    """Once a page prunes, its extract is scored against its own evidence and is unique.

    A `source` page caps the budget at 30k chars, so a 60k-char document cannot produce a
    block every page shares.
    """
    llm = _RecordingLLM(min_prefix_tokens=4096)
    pages = [_page("concept/a"), _page("source/doc", page_type="source")]

    await _refine(llm, "Đoạn văn bản nguồn. " * 3_000, pages)

    assert llm.cached_calls == []


@pytest.mark.asyncio
async def test_the_shared_block_is_the_whole_document_untouched():
    """The saving only exists because the block is the full text, not a per-page extract."""
    llm = _RecordingLLM(min_prefix_tokens=4096)

    await _refine(llm, _CACHEABLE_DOC, [_page("concept/a"), _page("concept/b")])

    prefix = llm.cached_calls[0][0]
    assert _CACHEABLE_DOC in prefix
    assert "[…skipped sections…]" not in prefix


@pytest.mark.asyncio
async def test_a_retry_drops_back_to_the_uncached_page_specific_extract():
    """Retries deliberately shrink the budget, which makes the block page-specific."""
    from unittest.mock import patch

    llm = _RecordingLLM(min_prefix_tokens=4096, fail_first_cached=True)

    with patch.object(writer, "WRITER_RETRY_DELAYS", (0, 0)):
        await _refine(llm, _CACHEABLE_DOC, [_page("concept/a"), _page("concept/b")])

    # First attempt of each page takes the cached path; the one that failed retries uncached.
    assert len(llm.cached_calls) == 2
    assert len(llm.plain_prompts) == 1
    assert "[…skipped sections…]" not in llm.plain_prompts[0]


# ---------------------------------------------------------------------------
# 3b. The complex writer rides the same gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_complex_writer_puts_the_breakpoint_on_the_neutral_message():
    """The agent loop re-sends its opening turn on every step, so this is the biggest win."""
    llm = _ToolLLM(min_prefix_tokens=4096)
    pages = [_page("concept/a"), _page("concept/b")]
    evidence_heavy = {"_claims": [
        {"subject": "a", "statement": f"claim {index}", "absolute_offset": 0,
         "evidence_length": 10}
        for index in range(20)
    ]}
    plan_json = {"pages": [{**page, "entity_names": ["a"]} for page in pages],
                 **evidence_heavy}

    from unittest.mock import AsyncMock, patch

    with patch("app.services.wiki_service.list_pages", AsyncMock(return_value=[])):
        await writer.run_refine_phase(
            session=object(), source=_source(),
            plan=SimpleNamespace(plan_json=plan_json), chunk_extracts=[],
            full_text=_CACHEABLE_DOC, llm=llm, embedding_provider=None,
            kt_slug=None, tracker=_Tracker(),
        )

    assert len(llm.tool_messages) == 2
    prefixes = {messages[0]["cache_prefix"] for messages in llm.tool_messages}
    assert len(prefixes) == 1
    assert _CACHEABLE_DOC in prefixes.pop()


# ---------------------------------------------------------------------------
# Provider plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_base_class_default_is_byte_identical_to_generate():
    """Providers without caching must receive exactly what they received before.

    No separator is inserted, so the writer owns the exact bytes on both sides of the
    boundary and the cached and uncached prompts cannot drift apart.
    """
    llm = _PlainLLM()

    await llm.generate_cached("HEAD", "TAIL")

    assert llm.prompts == ["HEADTAIL"]
    assert llm.cacheable_prefix_min_tokens() is None


@pytest.mark.asyncio
async def test_anthropic_marks_only_the_first_block():
    """A breakpoint on the tail as well would cache the per-page fields and never hit."""
    captured = {}

    class _FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=10, output_tokens=2,
                    cache_read_input_tokens=8192, cache_creation_input_tokens=0,
                ),
            )

    llm = AnthropicLLM(ProviderConfig(
        provider=ProviderType.ANTHROPIC, model_id="claude-opus-4-8",
    ))
    llm._client = SimpleNamespace(messages=_FakeMessages())

    text = await llm.generate_cached("HEAD", "TAIL", system="SYS")

    assert text == "ok"
    blocks = captured["messages"][0]["content"]
    assert [block["text"] for block in blocks] == ["HEAD", "TAIL"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    # Opus 4.8 rejects sampling params with a 400; generate_cached must respect that too.
    assert "temperature" not in captured
    assert "top_p" not in captured


def test_the_minimum_prefix_table_matches_the_documented_numbers():
    """4096 on Opus 4.8; 2048 on Fable 5 and Sonnet 4.6. Wrong numbers buy nothing."""
    assert min_cacheable_prefix_tokens("claude-opus-4-8") == 4096
    assert min_cacheable_prefix_tokens("claude-opus-4-7") == 4096
    assert min_cacheable_prefix_tokens("claude-sonnet-5") == 4096
    assert min_cacheable_prefix_tokens("claude-fable-5") == 2048
    assert min_cacheable_prefix_tokens("claude-sonnet-4-6") == 2048
    # Unknown id falls back to the strictest bar rather than the loosest.
    assert min_cacheable_prefix_tokens("claude-something-new") == 4096
    assert min_cacheable_prefix_tokens("") == 4096


def test_the_cache_gate_ratio_under_counts_tokens():
    """Vietnamese runs ~2 chars/token; the gate divides by 4 on purpose.

    Dividing by the larger number can only refuse a breakpoint that would have worked. The
    reverse mistake requests one that is silently dropped, which is unobservable.
    """
    assert writer._CACHE_GATE_CHARS_PER_TOKEN > writer._CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Neutral protocol
# ---------------------------------------------------------------------------


def test_the_neutral_cache_prefix_becomes_two_anthropic_blocks():
    out = agent_protocol.neutral_to_anthropic_messages(
        [{"role": "user", "content": "TAIL", "cache_prefix": "HEAD"}]
    )

    assert out == [{
        "role": "user",
        "content": [
            {"type": "text", "text": "HEAD", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "TAIL"},
        ],
    }]


def test_a_message_without_a_cache_prefix_is_unchanged():
    out = agent_protocol.neutral_to_anthropic_messages(
        [{"role": "user", "content": "TAIL"}]
    )

    assert out == [{"role": "user", "content": "TAIL"}]


def test_openai_concatenates_the_cache_prefix_instead_of_dropping_it():
    """The prefix is an optimisation hint, never content the model may go without.

    A converter that ignored the key would silently send the page spec with no source
    document attached.
    """
    out = agent_protocol.neutral_to_openai_messages(
        [{"role": "user", "content": "TAIL", "cache_prefix": "HEAD"}]
    )

    assert out == [{"role": "user", "content": "HEADTAIL"}]


def test_the_gateway_wire_format_never_produces_a_cache_prefix():
    """claude_gateway feeds inbound requests through this; it must stay on the old path."""
    out = agent_protocol.anthropic_messages_to_neutral(
        [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    )

    assert out == [{"role": "user", "content": "hello"}]
