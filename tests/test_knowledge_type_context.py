from app.ai.knowledge_type_context import build_effective_extraction_hints


def test_description_becomes_extraction_guidance():
    hints = build_effective_extraction_hints(
        "internal-playbook", "Internal Playbook",
        "Keep ordered procedures, prerequisites, and rollback conditions.", None,
    )
    assert "Category description as extraction guidance" in hints
    assert "Keep ordered procedures" in hints


def test_security_category_combines_all_guidance_layers():
    hints = build_effective_extraction_hints(
        "redteam", "Red Team",
        "Preserve target-specific operator procedures.",
        "Keep internal technique identifiers.",
    )
    assert "Built-in domain preservation profile" in hints
    assert "Preserve target-specific operator procedures" in hints
    assert "Administrator extraction instructions" in hints
    assert hints.index("Built-in domain") < hints.index("Category description")
    assert hints.index("Category description") < hints.index("Administrator extraction")


def test_description_can_activate_a_security_profile():
    hints = build_effective_extraction_hints(
        "field-notes", "Field Notes",
        "Pentest and redteam procedures with exact payloads.", None,
    )
    assert "Built-in domain preservation profile" in hints
    assert "platform/version-specific commands" in hints


def test_seeded_profile_is_not_duplicated():
    from app.scripts.seed_security_kt_hints import _match_hints

    profile = _match_hints("pentest")
    hints = build_effective_extraction_hints("pentest", "Pentest", None, profile)
    assert hints.count("These documents contain offensive security knowledge") == 1
    assert "Administrator extraction instructions" not in hints


def test_empty_category_context_returns_none():
    assert build_effective_extraction_hints(None, None, None, None) is None
