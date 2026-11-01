"""Tests for ISSUE-02: Path traversal protection in cloning._path()."""

import pytest
import cloning


def test_path_traversal_attacks_rejected():
    """All path traversal and malformed clone IDs must raise ValueError."""
    bad_ids = [
        "../x",
        "..\\x",
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "/etc/passwd",
        "C:\\Windows\\System32\\calc.exe",
        "valid\x00evil",
        "",
        "   ",
        "a" * 10000,
        "clone/subdir",
        "clone\\subdir",
        "id with spaces",
        "id$with%symbols!",
    ]
    for bad_id in bad_ids:
        with pytest.raises(ValueError, match="(?i)invalid clone id|path traversal"):
            cloning._path(bad_id)


def test_valid_clone_ids_resolve_inside_clones_dir():
    """Valid alphanumeric, hyphen, underscore IDs up to 64 chars resolve cleanly."""
    good_ids = [
        "abc",
        "clone_123",
        "my-voice-01",
        "A" * 64,
        "0123456789abcdef",
    ]
    for good_id in good_ids:
        path = cloning._path(good_id)
        assert path.name == f"{good_id}.pt"
        assert path.parent == cloning._CLONES_DIR.resolve()
