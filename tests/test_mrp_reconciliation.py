from app.ai.mrp.reducer import _lexical_similarity, _normalize, _slug_tail, enforce_reconciliation


def test_normalization_matches_title_and_slug_tail():
    assert _normalize("Open Source Intelligence (OSINT)") == "open source intelligence osint"
    assert _slug_tail("concept/open-source-intelligence-osint") == "open source intelligence osint"
    assert _lexical_similarity("Privilege Escalation", "Privilege Escalation Techniques") > 0.7


def test_confirmed_update_overrides_create_plan():
    plan = {
        "pages": [{
            "action": "CREATE",
            "slug": "concept/webshell-new",
            "title": "Webshell",
            "page_type": "concept",
            "entity_names": ["Webshell"],
            "priority": 1,
        }]
    }
    reconciliation = {
        "Webshell": {
            "action": "UPDATE",
            "page_slug": "concept/webshell",
            "page_title": "Webshell",
            "page_type": "concept",
            "confidence": 1.0,
            "match_method": "exact_title",
            "reason": "Exact match",
            "candidates": [],
        }
    }
    result = enforce_reconciliation(plan, reconciliation)
    assert result["pages"][0]["action"] == "UPDATE"
    assert result["pages"][0]["slug"] == "concept/webshell"


def test_mixed_group_splits_update_from_new_concept():
    plan = {
        "pages": [{
            "action": "CREATE",
            "slug": "concept/security-topics",
            "title": "Security Topics",
            "page_type": "concept",
            "entity_names": ["Webshell", "DangerScore"],
            "priority": 1,
        }]
    }
    reconciliation = {
        "Webshell": {
            "action": "UPDATE", "page_slug": "concept/webshell",
            "page_title": "Webshell", "page_type": "concept",
        },
        "DangerScore": {"action": "CREATE", "page_slug": None},
    }
    pages = enforce_reconciliation(plan, reconciliation)["pages"]
    assert [(p["action"], p["entity_names"]) for p in pages] == [
        ("UPDATE", ["Webshell"]),
        ("CREATE", ["DangerScore"]),
    ]


def test_unverified_planner_update_is_downgraded():
    plan = {"pages": [{
        "action": "UPDATE", "slug": "concept/wrong", "title": "Wrong",
        "page_type": "concept", "entity_names": ["New Thing"],
    }]}
    result = enforce_reconciliation(plan, {"New Thing": {"action": "CREATE"}})
    assert result["pages"][0]["action"] == "CREATE"


def test_multiple_names_for_same_target_become_one_update():
    plan = {"pages": [
        {"action": "CREATE", "slug": "new-a", "title": "A", "page_type": "concept", "entity_names": ["A"]},
        {"action": "CREATE", "slug": "new-b", "title": "B", "page_type": "concept", "entity_names": ["B"]},
    ]}
    reconciliation = {
        "A": {"action": "UPDATE", "page_slug": "concept/shared", "page_title": "Shared", "page_type": "concept"},
        "B": {"action": "UPDATE", "page_slug": "concept/shared", "page_title": "Shared", "page_type": "concept"},
    }
    updates = [p for p in enforce_reconciliation(plan, reconciliation)["pages"] if p["action"] == "UPDATE"]
    assert len(updates) == 1
    assert updates[0]["entity_names"] == ["A", "B"]
