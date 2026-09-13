"""The entity-dedup comparison is O(n^2) pure Python, and it ran on the event loop.

`_cosine` is a Python generator sum over a 1536-dimension vector, called once per pair
with no `await` anywhere in the double loop. Measured at ~155 us/pair, 1,000 entities is
~77 s of fully blocked loop and 5,000 is roughly half an hour. arq's
`health_check_interval` is 30 s, so a busy worker stopped heartbeating and looked dead,
and its other in-flight jobs stalled behind it.

Two properties matter and are easy to break independently:
  * the loop stays free (the fix), and
  * the answer does not change (pre-normalising each vector once, rather than recomputing
    both norms per pair, must be arithmetically equivalent).
"""

import asyncio

import pytest

from app.ai.mrp.reducer import (
    AMBIGUOUS_LOW,
    MERGE_THRESHOLD,
    _cosine,
    _similar_entity_pairs,
    _unit,
)


def _entities(n: int, kind: str = "org"):
    return [{"type": kind, "name": f"e{i}", "mention_count": 1} for i in range(n)]


def _vectors(n: int, dims: int = 64):
    """Deterministic, non-degenerate, with some genuinely close pairs."""
    out = []
    for i in range(n):
        base = [((i * 7 + d * 13) % 97) / 97.0 for d in range(dims)]
        out.append(base)
    # Make 0 and 1 near-identical so at least one pair crosses MERGE_THRESHOLD.
    if n >= 2:
        out[1] = [x + 1e-9 for x in out[0]]
    return out


# ---------------------------------------------------------------------------
# Equivalence with the original per-pair _cosine
# ---------------------------------------------------------------------------

def _reference(entities, vectors):
    """The implementation this replaced, kept as the oracle."""
    auto, amb = [], []
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            if entities[i]["type"] != entities[j]["type"]:
                continue
            sim = _cosine(vectors[i], vectors[j])
            if sim >= MERGE_THRESHOLD:
                auto.append((i, j))
            elif sim >= AMBIGUOUS_LOW:
                amb.append((i, j))
    return auto, amb


@pytest.mark.parametrize("n", [0, 1, 2, 9, 40])
def test_pre_normalising_does_not_change_the_answer(n):
    entities, vectors = _entities(n), _vectors(n)
    assert _similar_entity_pairs(entities, vectors) == _reference(entities, vectors)


def test_entities_of_different_types_are_never_compared():
    entities = [
        {"type": "org", "name": "a", "mention_count": 1},
        {"type": "person", "name": "b", "mention_count": 1},
    ]
    vectors = [[1.0, 0.0], [1.0, 0.0]]  # identical direction
    auto, amb = _similar_entity_pairs(entities, vectors)
    assert auto == [] and amb == []


def test_a_zero_vector_is_skipped_rather_than_dividing_by_zero():
    """`_cosine` returned 0.0 for a zero-norm vector; `_unit` returns None for it."""
    assert _unit([0.0, 0.0, 0.0]) is None
    entities = _entities(2)
    auto, amb = _similar_entity_pairs(entities, [[0.0, 0.0], [1.0, 0.0]])
    assert auto == [] and amb == []


def test_unit_vectors_have_length_one():
    u = _unit([3.0, 4.0])
    assert u is not None
    assert abs(sum(x * x for x in u) - 1.0) < 1e-12


# ---------------------------------------------------------------------------
# The loop stays free
# ---------------------------------------------------------------------------

async def _ticks_during(coro):
    """Await `coro` while counting how often a 1 ms sleeper gets scheduled.

    Same helper as tests/test_blocking_io_offload.py: a coroutine that blocks the loop
    yields zero ticks, because the ticker never runs between create_task and the end of
    the blocking body.
    """
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.001)
            ticks += 1

    task = asyncio.create_task(ticker())
    result = await coro
    stop = True
    await task
    return result, ticks


@pytest.mark.asyncio
async def test_the_pairwise_comparison_does_not_block_the_event_loop():
    # Large enough that the comparison takes real time on any machine: 400 entities is
    # ~80k pairs over 256 dimensions.
    n, dims = 400, 256
    entities, vectors = _entities(n), _vectors(n, dims)

    (_auto, _amb), ticks = await _ticks_during(
        asyncio.to_thread(_similar_entity_pairs, entities, vectors)
    )
    assert ticks > 10, (
        f"the pairwise comparison ran on the event loop (only {ticks} ticks) — arq's "
        "30 s heartbeat cannot fire and sibling jobs stall"
    )


def test_the_caller_actually_offloads_it():
    """The helper being thread-safe is worth nothing if the caller still inlines it.

    Structural, by AST: `embedding_dedup_entities` must reach `_similar_entity_pairs`
    through `asyncio.to_thread`, not call it directly. A direct call still returns the
    right answer — it just does so while holding the event loop, which is the entire bug.
    """
    import ast
    import inspect
    import textwrap

    from app.ai.mrp import reducer

    tree = ast.parse(textwrap.dedent(inspect.getsource(reducer.embedding_dedup_entities)))

    offloaded = False
    direct = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "to_thread":
            names = [
                a.id for a in node.args
                if isinstance(a, ast.Name)
            ]
            if "_similar_entity_pairs" in names:
                offloaded = True
        if isinstance(f, ast.Name) and f.id == "_similar_entity_pairs":
            direct = True

    assert offloaded, (
        "embedding_dedup_entities no longer hands the pairwise comparison to "
        "asyncio.to_thread — it is back on the event loop"
    )
    assert not direct, "_similar_entity_pairs is also called directly, blocking the loop"
