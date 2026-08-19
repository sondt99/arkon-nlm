"""Robustness of the three places the pipeline trusts a model's arithmetic or syntax (#90).

Items 4 and 6 of the issue, plus the parts of item 2 that are checkable without a network:

  - JSON replies were unwrapped with `raw.strip("```json").strip("```")`. str.strip takes a
    *set of characters*, so that removes any leading or trailing backtick, `j`, `s`, `o` or
    `n` in any order, and neither site had a fallback for a reply with prose around the JSON.
  - `local_offset` was floored at 0 and never ceilinged, so a hallucinated number produced an
    empty evidence excerpt and skewed which sections the writer was shown.
  - `embedding_dedup_entities` returned a 4-tuple on success and a plain list on failure.
"""

import math

import pytest

from app.ai.mrp import mapper, reducer
from app.ai.mrp.mapper import DocumentChunk
from app.ai.mrp.writer import (
    _CHARS_PER_TOKEN,
    _MODEL_CONTEXT_TOKENS,
    _get_source_context_budget,
    _score_sections,
    _split_into_sections,
    assemble_evidence,
)
from app.ai.providers.base import parse_json_response, strip_json_fence

# ---------------------------------------------------------------------------
# JSON replies
# ---------------------------------------------------------------------------


def _legacy_unwrap(raw: str) -> str:
    """The expression this change removed, kept so the tests below can contrast with it."""
    return raw.strip().strip("```json").strip("```").strip()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"contradicts": true}', {"contradicts": True}),
        ('```json\n{"contradicts": true}\n```', {"contradicts": True}),
        ('```JSON\n[true, false]\n```', [True, False]),
        ('```\n[true]\n```', [True]),
        ('```json {"a": 1} ```', {"a": 1}),
        ('Here is the JSON:\n```json\n{"a": 1}\n```', {"a": 1}),
        ('{"a": 1}\n\nNo other contradictions were found.', {"a": 1}),
        ('[true, false]  ', [True, False]),
        ('{"a": 1, "b": [2, 3]} trailing', {"a": 1, "b": [2, 3]}),
    ],
    ids=[
        "bare", "fenced", "uppercase-fence", "unlabelled-fence", "fence-on-one-line",
        "prose-prefix", "prose-suffix", "array", "nested",
    ],
)
def test_the_shapes_a_provider_actually_returns_all_parse(raw, expected):
    assert parse_json_response(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ['```JSON\n[true, false]\n```', 'Here is the JSON:\n{"a": 1}', 'null'],
    ids=["uppercase-fence", "prose-prefix", "bare-null"],
)
def test_replies_the_character_set_expression_destroyed(raw):
    """Each of these used to reach json.loads mangled or unmangled-but-unparseable.

    The uppercase fence is the sharpest: `strip("```json")` stops at the `J`, leaving
    `JSON\\n[true, false]` for json.loads. In REDUCE that reply is the ambiguous-entity
    verdict, so the whole pair list was left unmerged with only a warning line to show it —
    duplicate entities reaching the planner with no signal that resolution never ran. `null`
    is the character-set bug proper: the leading `n` is in the set and is eaten.
    """
    with pytest.raises(ValueError):
        import json

        json.loads(_legacy_unwrap(raw))

    parse_json_response(raw)  # must not raise


@pytest.mark.parametrize(
    "raw",
    ["I think they disagree, actually.", "", "   ", "{unterminated", "not json at all"],
    ids=["prose", "empty", "blank", "truncated-object", "words"],
)
def test_an_unrecoverable_reply_raises_valueerror(raw):
    """Callers catch ValueError to mean "no verdict"; json.JSONDecodeError is one too."""
    with pytest.raises(ValueError):
        parse_json_response(raw)


def test_a_reply_truncated_mid_value_raises_instead_of_returning_the_wrong_type():
    """Recovering the inner array here would be worse than failing.

    reducer's MAYBE resolution branches on `isinstance(parsed, list)`, so returning the
    `pages` array as though it were the top-level object would silently take the wrong
    path. The planning call's caller wants an object or an exception, nothing in between.
    """
    raw = '{"pages": [{"slug": "a"}, {"slug": "b"}], "notes": "unterminat'

    with pytest.raises(ValueError):
        parse_json_response(raw)


def test_the_opening_container_wins_when_both_appear():
    """An object whose first value is an array must not be read as that array."""
    assert parse_json_response('prefix {"items": [1, 2]} suffix') == {"items": [1, 2]}
    assert parse_json_response('prefix [{"a": 1}] suffix') == [{"a": 1}]


def test_the_fence_stripper_is_anchored_to_the_ends():
    """An unanchored sub() would eat a fenced code block out of the middle of a payload."""
    body = '{"content": "```python\\nprint(1)\\n```"}'

    assert strip_json_fence(body) == body
    assert parse_json_response(body)["content"] == "```python\nprint(1)\n```"


def test_the_mapper_parser_delegates_rather_than_keeping_its_own_copy():
    """Three call sites had three different unwrapping rules; one is now shared."""
    assert mapper._parse_extract_json('```json\n{"entities": []}\n```') == {"entities": []}
    assert mapper._parse_extract_json('Sure:\n{"entities": [{"name": "A"}]}') == {
        "entities": [{"name": "A"}]
    }


# ---------------------------------------------------------------------------
# Offsets
# ---------------------------------------------------------------------------


def _chunk(start=1_000, end=2_000):
    return DocumentChunk(
        index=0, start_char=start, end_char=end, section_path="§1", text="x" * (end - start),
    )


@pytest.mark.parametrize(
    ("local_offset", "expected"),
    [
        (0, 1_000),
        (500, 1_500),
        (1_000, 2_000),
        (999_999, 2_000),   # hallucinated: clamped to the end of the chunk
        (-50, 1_000),       # the pre-existing floor
        (None, 1_000),
        ("garbage", 1_000),
        (12.7, 1_012),
    ],
    ids=["start", "middle", "end", "hallucinated", "negative", "missing", "text", "float"],
)
def test_an_offset_is_clamped_to_the_chunk_that_produced_it(local_offset, expected):
    assert mapper._absolute_offset(local_offset, _chunk()) == expected


def test_a_hallucinated_offset_no_longer_skews_which_sections_the_writer_sees():
    """The substantive consequence the clamp exists for.

    _score_sections scores a section by how many evidence offsets fall inside it. An offset
    past the end of the document lands in no section and contributes nothing, so one
    fabricated number quietly changes which part of a long source the writer is shown.
    Clamped to the chunk that produced the claim, it scores that chunk's section instead.
    """
    section_a = "# A\n" + "a" * 3_000 + "\n"
    section_b = "# B\n" + "b" * 3_000 + "\n"
    full_text = section_a + section_b
    chunk = _chunk(start=len(section_a), end=len(full_text))
    extract = {"claims": [{"statement": "from section B", "local_offset": 10_000_000}]}

    mapper._convert_offsets(extract, chunk)
    offset = extract["claims"][0]["absolute_offset"]

    assert chunk.start_char <= offset <= chunk.end_char

    scored = _score_sections(
        _split_into_sections(full_text),
        [{"absolute_offset": offset}],
    )
    best_index, best_text, _score = scored[0]
    assert best_text.startswith("# B"), (best_index, best_text[:20])


def test_an_unclamped_offset_would_have_scored_no_section_at_all():
    """The before-picture, so the test above is measuring something real."""
    full_text = "# A\n" + "a" * 3_000 + "\n# B\n" + "b" * 3_000

    scored = _score_sections(
        _split_into_sections(full_text),
        [{"absolute_offset": 10_000_000}],
    )

    # Nothing but the position bonus survives: no direct hit, no proximity.
    assert all(score <= 1.0 for _index, _text, score in scored)


def test_an_in_range_offset_still_produces_its_excerpt():
    """The clamp must not disturb the offsets that were correct all along."""
    full_text = "A" * 500 + "THE ANSWER IS 42" + "B" * 500
    chunk = _chunk(start=0, end=len(full_text))
    extract = {"claims": [{"statement": "answer", "local_offset": 500}]}

    mapper._convert_offsets(extract, chunk)

    evidence = assemble_evidence(
        {"entity_names": ["answer"]},
        [{"subject": "answer", "statement": "answer",
          "absolute_offset": extract["claims"][0]["absolute_offset"],
          "evidence_length": 16}],
        full_text,
    )
    assert evidence[0]["source_excerpt"] == "THE ANSWER IS 42"


def test_every_extract_list_is_converted():
    """entities, concepts and claims all carry offsets; a missed list is a silent zero."""
    chunk = _chunk()
    extract = {
        "entities": [{"local_offset": 10}],
        "concepts": [{"local_offset": 20}],
        "claims": [{"local_offset": 30}],
    }

    mapper._convert_offsets(extract, chunk)

    assert [extract[key][0]["absolute_offset"] for key in ("entities", "concepts", "claims")] == [
        1_010, 1_020, 1_030,
    ]
    assert all("local_offset" not in extract[key][0] for key in extract)


# ---------------------------------------------------------------------------
# Entity dedup return shape
# ---------------------------------------------------------------------------


class _Embedding:
    """Orthogonal-ish vectors unless two names are declared similar."""

    def __init__(self, vectors=None, fail=False):
        self.vectors = vectors or {}
        self.fail = fail

    async def embed_batch(self, texts, concurrency=5):
        if self.fail:
            raise RuntimeError("embedding provider 429")
        return [self.vectors.get(text, [1.0, 0.0, 0.0]) for text in texts]


def _entity(name, type_="person", mentions=1):
    return {"name": name, "type": type_, "mention_count": mentions, "aliases": []}


@pytest.mark.asyncio
async def test_the_return_shape_is_the_same_whether_the_embed_worked():
    """The bug itself: a 4-tuple on success, a plain list on failure.

    The sole caller told them apart with `isinstance(result, tuple)`. The failure branch
    happened to return the input list unchanged, so skipping it was accidentally harmless —
    the defect is the union return type, which permitted a future failure branch to return a
    *different* list that the caller would then silently discard. One type removes both the
    isinstance check and the guess.
    """
    entities = [_entity("Nguyễn Văn A"), _entity("Nguyen Van A")]

    ok = await reducer.embedding_dedup_entities(entities, _Embedding())
    failed = await reducer.embedding_dedup_entities(entities, _Embedding(fail=True))

    assert type(ok) is reducer.EntityDedupResult
    assert type(failed) is reducer.EntityDedupResult
    assert ok.embedded is True
    assert failed.embedded is False
    assert failed.entities is entities
    assert failed.merged_into == {}
    assert failed.ambiguous_pairs == []


@pytest.mark.asyncio
async def test_a_single_entity_short_circuits_without_calling_the_provider():
    class _Exploding:
        async def embed_batch(self, texts, concurrency=5):
            raise AssertionError("embed_batch must not be called for one entity")

    result = await reducer.embedding_dedup_entities([_entity("A")], _Exploding())

    assert result.embedded is False
    assert result.entities == [_entity("A")]


@pytest.mark.asyncio
async def test_near_identical_names_of_the_same_type_are_merged():
    """The success path still does its job — previously asserted nowhere."""
    entities = [_entity("Acme", mentions=5), _entity("Acme Corp", mentions=2)]
    vectors = {"Acme": [1.0, 0.0], "Acme Corp": [1.0, 0.0]}

    result = await reducer.embedding_dedup_entities(entities, _Embedding(vectors))
    merged = reducer._apply_merges(result.entities, result.merged_into)

    assert result.embedded is True
    assert len(merged) == 1
    assert merged[0]["name"] == "Acme"
    assert merged[0]["mention_count"] == 7


@pytest.mark.asyncio
async def test_a_middling_similarity_becomes_an_ambiguous_pair_for_the_llm():
    angle = math.acos(
        (reducer.MERGE_THRESHOLD + reducer.AMBIGUOUS_LOW) / 2
    )
    entities = [_entity("A"), _entity("B")]
    vectors = {"A": [1.0, 0.0], "B": [math.cos(angle), math.sin(angle)]}

    result = await reducer.embedding_dedup_entities(entities, _Embedding(vectors))

    assert result.merged_into == {}
    assert result.ambiguous_pairs == [(0, 1)]


@pytest.mark.asyncio
async def test_entities_of_different_types_are_never_compared():
    """A person and an organisation with the same name are two things, not one."""
    entities = [_entity("Sun", "organization"), _entity("Sun", "person")]
    vectors = {"Sun": [1.0, 0.0]}

    result = await reducer.embedding_dedup_entities(entities, _Embedding(vectors))

    assert result.merged_into == {}
    assert result.ambiguous_pairs == []


# ---------------------------------------------------------------------------
# Context budget table (#90 item 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5",
        "claude-fable-5", "claude-sonnet-4-6", "claude-haiku-4-5",
    ],
)
def test_every_current_claude_id_is_in_the_context_table(model_id):
    """The table's only Claude keys were once four invalid, date-suffixed ids.

    Lookup is exact-then-startswith, and `"claude-opus-4-8".startswith("claude-4.7-opus")`
    is False, so every real model fell through to the 60k fallback — about 1.5% of a 1M
    window.
    """
    assert model_id in _MODEL_CONTEXT_TOKENS
    assert _get_source_context_budget(model_id) > 60_000


def test_no_model_id_carries_a_date_suffix():
    """Current Claude ids are never date-suffixed; a suffixed key is a 404 waiting to happen."""
    import re

    for key in _MODEL_CONTEXT_TOKENS:
        assert not re.search(r"-20\d{6}$", key), key


def test_the_char_budget_stays_inside_the_share_it_claims():
    """A Vietnamese ratio, not an English one.

    At 4 chars/token a 128k-token model was granted 307k chars — roughly 154k tokens of
    Vietnamese, more than the whole window, for a share meant to be 60% of it.
    """
    assert _CHARS_PER_TOKEN == 2
    context_tokens = _MODEL_CONTEXT_TOKENS["gpt-4o"]
    budget_chars = _get_source_context_budget("gpt-4o")

    assert budget_chars / _CHARS_PER_TOKEN <= context_tokens * 0.60


def test_an_unknown_model_still_falls_back_rather_than_guessing_big():
    assert _get_source_context_budget("some-new-model") == 60_000
    assert _get_source_context_budget(None) == 60_000
