"""
Unit tests for app/ai/embedding_catalog.py.

These run without a DB or Redis: pure data-validity checks on the catalog.
"""

import pytest

from app.ai.embedding_catalog import (
    EMBEDDING_CATALOG,
    LEGACY_SPEC_ID_ALIASES,
    SUPPORTED_DIMENSIONS,
    UnknownEmbeddingModel,
    get_spec,
    list_specs,
    list_specs_by_provider,
    normalize_spec_id,
    specs_for_dimension,
)

# Dimensions for which the schema (migration 015) creates a
# wiki_page_embeddings_<dim> table.
SCHEMA_DIMENSIONS = {768, 1024, 1536, 3072}


def test_catalog_not_empty():
    assert len(EMBEDDING_CATALOG) > 0


def test_every_spec_has_a_schema_table():
    """Every catalog dimension MUST have a matching wiki_page_embeddings_<dim> table."""
    for spec in EMBEDDING_CATALOG.values():
        assert spec.dimension in SCHEMA_DIMENSIONS, (
            f"{spec.id} has dimension={spec.dimension} but no matching table. "
            f"Add a wiki_page_embeddings_{spec.dimension} table in a new "
            f"Alembic migration before adding this model."
        )


def test_spec_id_matches_provider_and_model():
    """spec.id must equal '<provider>/<model_id>' so the convention stays honest."""
    for spec_id, spec in EMBEDDING_CATALOG.items():
        assert spec.id == spec_id, "dict key must match spec.id"
        assert spec.id == f"{spec.provider}/{spec.model_id}", (
            f"id={spec.id!r} does not match provider/model_id"
        )


def test_get_spec_round_trip():
    for spec_id in EMBEDDING_CATALOG:
        assert get_spec(spec_id).id == spec_id


def test_get_spec_unknown_raises():
    with pytest.raises(UnknownEmbeddingModel):
        get_spec("nonexistent/foo")


def test_supported_dimensions_match_catalog():
    derived = sorted({s.dimension for s in EMBEDDING_CATALOG.values()})
    assert list(SUPPORTED_DIMENSIONS) == derived


def test_list_specs_returns_all():
    assert {s.id for s in list_specs()} == set(EMBEDDING_CATALOG.keys())


def test_list_specs_by_provider_filters():
    for provider in {s.provider for s in EMBEDDING_CATALOG.values()}:
        out = list_specs_by_provider(provider)
        assert all(s.provider == provider for s in out)


def test_specs_for_dimension_filters():
    for dim in SUPPORTED_DIMENSIONS:
        out = list(specs_for_dimension(dim))
        assert out, f"no specs for dimension {dim}"
        assert all(s.dimension == dim for s in out)


def test_legacy_aliases_resolve_to_canonical_specs():
    """Pre-rename 9Router IDs must keep resolving (installs persisted them)."""
    for legacy, canonical in LEGACY_SPEC_ID_ALIASES.items():
        assert legacy not in EMBEDDING_CATALOG, "alias must not shadow a real spec"
        assert canonical in EMBEDDING_CATALOG
        assert normalize_spec_id(legacy) == canonical
        assert get_spec(legacy).id == canonical


def test_normalize_spec_id_passes_through_unknown_ids():
    assert normalize_spec_id("custom/whatever") == "custom/whatever"


def test_no_duplicate_provider_model_pairs():
    """Two specs can't both target the same (provider, model_id) — would cause
    ambiguous resolution."""
    seen: set[tuple[str, str]] = set()
    for spec in EMBEDDING_CATALOG.values():
        pair = (spec.provider, spec.model_id)
        assert pair not in seen, f"duplicate provider/model_id: {pair}"
        seen.add(pair)
