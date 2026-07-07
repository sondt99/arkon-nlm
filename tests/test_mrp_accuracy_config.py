import pytest

from app.config import Settings, settings


def test_accuracy_thresholds_have_safe_relationships():
    assert settings.mrp_chunk_overlap_chars < settings.mrp_chunk_target_chars
    assert settings.mrp_kb_maybe_threshold < settings.mrp_kb_update_threshold
    assert settings.mrp_entity_ambiguous_threshold < settings.mrp_entity_merge_threshold
    assert settings.mrp_entity_merge_threshold >= settings.mrp_kb_update_threshold
    assert 0.5 <= settings.mrp_merge_min_body_ratio <= 1.0
    assert settings.mrp_writer_max_attempts >= 1


def test_mrp_modules_read_central_settings():
    from app.ai.mrp import mapper, merger, reducer, verifier, writer

    assert mapper.CHUNK_TARGET_CHARS == settings.mrp_chunk_target_chars
    assert mapper.OVERLAP_CHARS == settings.mrp_chunk_overlap_chars
    assert mapper.EXTRACT_TIMEOUT == settings.mrp_extract_timeout
    assert reducer.MERGE_THRESHOLD == settings.mrp_entity_merge_threshold
    assert reducer.AMBIGUOUS_LOW == settings.mrp_entity_ambiguous_threshold
    assert reducer.KB_UPDATE_THRESHOLD == settings.mrp_kb_update_threshold
    assert writer.MAX_WRITER_CONCURRENCY == settings.mrp_writer_max_concurrency
    assert writer.WRITER_MAX_ATTEMPTS == settings.mrp_writer_max_attempts
    assert verifier.CONFLICT_SIM_THRESHOLD == settings.mrp_verify_conflict_threshold
    assert merger.BODY_SHRINK_THRESHOLD == settings.mrp_merge_min_body_ratio


@pytest.mark.parametrize(
    "overrides",
    [
        {"mrp_chunk_target_chars": 4_000, "mrp_chunk_overlap_chars": 4_000},
        {"mrp_kb_maybe_threshold": 0.80, "mrp_kb_update_threshold": 0.80},
        {"mrp_entity_ambiguous_threshold": 0.90, "mrp_entity_merge_threshold": 0.90},
    ],
)
def test_invalid_accuracy_relationships_fail_fast(overrides):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **overrides)
