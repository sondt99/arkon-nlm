"""
Security regression tests for storage path sanitization.

Covers safe_relative_path (app/services/storage_service.py), the guard used by
skill/skill-contribution file endpoints against MinIO key path traversal.
No DB or Redis required.
"""

import pytest

from app.services.storage_service import safe_relative_path


@pytest.mark.parametrize(
    "path,expected",
    [
        ("SKILL.md", "SKILL.md"),
        ("folder/file.py", "folder/file.py"),
        ("/leading/slash.md", "leading/slash.md"),
        ("a//b///c.txt", "a/b/c.txt"),
        ("./a/./b.md", "a/b.md"),
        ("back\\slash\\file.md", "back/slash/file.md"),
    ],
)
def test_safe_paths_pass_through_normalized(path, expected):
    assert safe_relative_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "../escape.md",
        "a/../../escape.md",
        "root/../../other-contribution/SKILL.md",
        "..\\windows\\escape.md",
        "a/..\\b/escape.md",
        "..",
        "",
        "/",
        "//",
        ".",
    ],
)
def test_traversal_and_empty_paths_rejected(path):
    with pytest.raises(ValueError):
        safe_relative_path(path)


def test_prefix_guard_bypass_shape_is_rejected():
    """The exact bypass shape from the audit: satisfies startswith(root/) but escapes."""
    with pytest.raises(ValueError):
        safe_relative_path("myskill/../../other-id/x")
