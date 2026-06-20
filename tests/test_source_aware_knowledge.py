from app.ai.mrp.merger import lossless_merge_fallback


def test_lossless_merge_fallback_preserves_both_sources():
    existing = "# Shared concept\n\nFact from source A."
    incoming = "# Shared concept\n\nFact from source B."

    merged = lossless_merge_fallback(existing, incoming)

    assert "Fact from source A." in merged
    assert "Fact from source B." in merged
    assert merged.index("Fact from source A.") < merged.index("Fact from source B.")


def test_lossless_merge_fallback_normalizes_boundary_whitespace():
    assert lossless_merge_fallback("A  \n", "\n  B") == "A\n\n---\n\nB"
