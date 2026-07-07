from app.ai.mrp.security_artifacts import (
    artifacts_for_page,
    extract_security_artifacts,
    is_security_domain,
    preserve_missing_artifacts,
    route_artifacts_to_pages,
)


def test_security_artifacts_preserve_exact_platform_specific_syntax():
    source = """# SQL injection via ATutor
The sanitizer is selected dynamically:
$addslashes = 'trim';
The exact blind extraction probe is:
mysql> select/**/ascii(substring((select/**/version()),1,1))=53;
"""
    artifacts = extract_security_artifacts(source)
    texts = [item["text"] for item in artifacts]

    assert "$addslashes = 'trim';" in texts
    assert any("select/**/ascii(substring((select/**/version()),1,1))=53;" in text for text in texts)

    assigned = artifacts_for_page(
        {
            "slug": "concept/addslashes-bypass",
            "title": "addslashes bypass",
            "page_type": "concept",
            "entity_names": ["addslashes"],
        },
        artifacts,
    )
    assert any("$addslashes = 'trim';" == item["text"] for item in assigned)

    output = preserve_missing_artifacts("# addslashes bypass\n\nExplanation.", assigned)
    assert "$addslashes = 'trim';" in output
    assert "Exact commands and payloads" in output


def test_security_domain_detection_is_explicit():
    assert is_security_domain("pentest")
    assert is_security_domain("general", "KEEP exact exploit payloads")
    assert not is_security_domain("general", "Company handbook")


def test_global_routing_does_not_duplicate_an_artifact_across_pages():
    artifact = {
        "kind": "payload",
        "text": "select/**/version()",
        "context": "MySQL blind SQL injection version extraction",
        "absolute_offset": 100,
    }
    pages = [
        {"slug": "concept/sql-injection", "title": "SQL injection", "page_type": "concept", "priority": 2},
        {"slug": "concept/mysql-version-extraction", "title": "MySQL version extraction", "page_type": "concept", "priority": 1},
    ]
    routes = route_artifacts_to_pages(pages, [artifact])
    placements = sum(artifact in assigned for assigned in routes.values())
    assert placements == 1
    assert routes["concept/mysql-version-extraction"] == [artifact]
