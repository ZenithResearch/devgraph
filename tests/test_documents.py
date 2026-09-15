from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devgraph.documents import (
    DOCUMENT_ROOTS_VARIABLE,
    MAX_DOCUMENT_BYTES,
    document_roots_from_env,
    read_artifact_document,
)


def _read(path: Path, roots: tuple[Path, ...], **metadata):
    return read_artifact_document("plan", {"uri": str(path), **metadata}, roots)


def test_explicit_roots_are_physical_absolute_paths_and_deduplicated(tmp_path):
    roots = [str(tmp_path), str(tmp_path / "other"), str(tmp_path)]
    assert document_roots_from_env({DOCUMENT_ROOTS_VARIABLE: json.dumps(roots)}) == (
        tmp_path, tmp_path / "other"
    )
    assert document_roots_from_env({}) == ()


@pytest.mark.parametrize("raw", [
    "", "not-json", '"/documents"', "{}", "[]", '["/"]', '["relative"]',
    '["/documents", 123]', '["/documents/../secret"]', '["/documents/./plans"]',
    '["//documents"]', '["/documents\\u0000"]', json.dumps(["/plans"] * 17),
])
def test_invalid_root_configuration_fails_closed(raw):
    assert document_roots_from_env({DOCUMENT_ROOTS_VARIABLE: raw}) == ()


def test_markdown_preserves_full_authored_text_without_interpreting_html(tmp_path):
    content = "# Plan\n\n1. Build\n2. Verify 👋\n<script>alert('test')</script>\n"
    path = tmp_path / "plan.md"
    path.write_text(content, encoding="utf-8")
    result = _read(path, (tmp_path,), id="plan")
    assert result == {
        "schema": "devgraph.work-document.v1", "artifact_id": "plan", "state": "readable",
        "message": "Document loaded.", "media_type": "text/markdown",
        "size_bytes": len(content.encode("utf-8")), "content": content,
    }


def test_file_uri_with_encoded_spaces_and_unicode_is_readable(tmp_path):
    path = tmp_path / "release plan é.markdown"
    path.write_text("Plan", encoding="utf-8")
    result = read_artifact_document("plan", {"uri": path.as_uri()}, (tmp_path,))
    assert result["state"] == "readable"
    assert result["content"] == "Plan"


def test_explicit_text_media_type_supports_extensionless_text(tmp_path):
    path = tmp_path / "README"
    path.write_bytes(b"\xef\xbb\xbfA plain text plan")
    result = _read(path, (tmp_path,), media_type="text/plain; charset=utf-8")
    assert result["state"] == "readable"
    assert result["content"] == "A plain text plan"
    assert result["media_type"] == "text/plain"


def test_empty_text_document_is_readable(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    result = _read(path, (tmp_path,))
    assert result["state"] == "readable"
    assert result["size_bytes"] == 0
    assert result["content"] == ""


@pytest.mark.parametrize("uri,state", [
    ("", "metadata_only"),
    ("https://example.com/plan.md", "metadata_only"),
    ("http://127.0.0.1/private", "metadata_only"),
    ("/somewhere/plan.md", "unconfigured"),
    ("file:///somewhere/plan.md", "unconfigured"),
    ("smb://example.com/plan.md", "unavailable"),
])
def test_no_roots_or_remote_metadata_never_open_files(monkeypatch, uri, state):
    def forbidden(*args, **kwargs):
        raise AssertionError("metadata-only state must not touch the filesystem")
    monkeypatch.setattr(os, "open", forbidden)
    result = read_artifact_document("plan", {"uri": uri}, ())
    assert result["state"] == state
    assert result["content"] is None


@pytest.mark.parametrize("uri", [
    "/documents/../secret.txt", "file:///documents/%2e%2e/secret.txt",
    "file://outside-host/documents/plan.md", "file:///documents/plan.md?query=1",
    "file:///documents/plan.md#fragment", "file:///documents/plan%00.md",
    "file:///documents/plan%zz.md", "file:///documents/plan%ff.md",
    "file:///documents/./plan.md", "file:relative.md", "relative.md",
    "/documents\x00/plan.md", "//documents/plan.md", "javascript:alert(1)",
])
def test_malformed_and_traversing_locations_never_open_files(monkeypatch, uri):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe path must be rejected before opening")
    monkeypatch.setattr(os, "open", forbidden)
    result = read_artifact_document("plan", {"uri": uri}, (Path("/documents"),))
    assert result["state"] == "unavailable"
    assert result["content"] is None


def test_prefix_collision_is_not_root_containment(tmp_path):
    root = tmp_path / "docs"
    sibling = tmp_path / "docs-private"
    root.mkdir()
    sibling.mkdir()
    path = sibling / "secret.md"
    path.write_text("SECRET")
    result = _read(path, (root,))
    assert result["state"] == "unavailable"
    assert "SECRET" not in str(result)


def test_artifact_identity_mismatch_is_not_read(tmp_path):
    path = tmp_path / "plan.md"
    path.write_text("Plan")
    result = _read(path, (tmp_path,), id="unrelated")
    assert result["state"] == "unavailable"
    assert result["content"] is None


def test_missing_linked_file_has_explicit_missing_state(tmp_path):
    result = _read(tmp_path / "missing.md", (tmp_path,))
    assert result["state"] == "missing"
    assert result["content"] is None


@pytest.mark.parametrize("name,media_type", [
    ("plan.pdf", "application/pdf"), ("plan.md", "text/html"), ("plan.bin", ""),
])
def test_unsupported_formats_do_not_return_bytes(tmp_path, name, media_type):
    path = tmp_path / name
    path.write_bytes(b"content")
    result = _read(path, (tmp_path,), media_type=media_type)
    assert result["state"] == "unsupported"
    assert result["content"] is None


@pytest.mark.parametrize("content", [b"a\x00b", b"\xff\xfeBad UTF8"])
def test_binary_and_invalid_utf8_are_not_previewed(tmp_path, content):
    path = tmp_path / "plan.txt"
    path.write_bytes(content)
    result = _read(path, (tmp_path,))
    assert result["state"] == "unsupported"
    assert result["content"] is None


@pytest.mark.parametrize("size,state", [
    (MAX_DOCUMENT_BYTES, "readable"), (MAX_DOCUMENT_BYTES + 1, "unsupported"),
])
def test_document_size_limit(tmp_path, size, state):
    path = tmp_path / "plan.txt"
    path.write_bytes(b"x" * size)
    result = _read(path, (tmp_path,))
    assert result["state"] == state
    assert len(result["content"] or "") <= MAX_DOCUMENT_BYTES


def test_growth_during_read_stays_bounded_and_returns_no_partial_content(tmp_path, monkeypatch):
    path = tmp_path / "plan.txt"
    path.write_text("initial")
    actual_read = os.read
    requested = []
    grew = False

    def grow_before_read(descriptor, size):
        nonlocal grew
        requested.append(size)
        if not grew:
            grew = True
            path.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))
        return actual_read(descriptor, size)

    monkeypatch.setattr(os, "read", grow_before_read)
    result = _read(path, (tmp_path,))
    assert result["state"] == "unsupported"
    assert result["content"] is None
    assert sum(requested) == MAX_DOCUMENT_BYTES + 1


def test_mutating_document_returns_retry_state_instead_of_mixed_content(tmp_path, monkeypatch):
    path = tmp_path / "plan.txt"
    path.write_text("initial")
    actual_read = os.read
    changed = False

    def edit_after_read(descriptor, size):
        nonlocal changed
        chunk = actual_read(descriptor, size)
        if not changed:
            changed = True
            path.write_text("changed, longer content")
        return chunk

    monkeypatch.setattr(os, "read", edit_after_read)
    result = _read(path, (tmp_path,))
    assert result["state"] == "unavailable"
    assert result["content"] is None


def test_escaping_file_and_directory_symlinks_are_rejected(tmp_path):
    root = tmp_path / "docs"
    outside = tmp_path / "private"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("SECRET")
    (root / "plan.md").symlink_to(secret)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    for path in (root / "plan.md", root / "linked" / "secret.md"):
        result = _read(path, (root,))
        assert result["state"] == "unavailable"
        assert "SECRET" not in str(result)


def test_configured_root_cannot_itself_be_a_symlink(tmp_path):
    physical = tmp_path / "physical"
    physical.mkdir()
    (physical / "plan.md").write_text("Plan")
    root = tmp_path / "docs"
    root.symlink_to(physical, target_is_directory=True)
    assert _read(root / "plan.md", (root,))["state"] == "unavailable"


def test_swapping_final_file_for_symlink_cannot_escape(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    plan = root / "plan.md"
    plan.write_text("Plan")
    outside = tmp_path / "secret.md"
    outside.write_text("SECRET")
    actual_open = os.open

    def swap_then_open(path, flags, *args, **kwargs):
        if path == "plan.md":
            plan.unlink()
            plan.symlink_to(outside)
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_then_open)
    result = _read(plan, (root,))
    assert result["state"] == "unavailable"
    assert "SECRET" not in str(result)


def test_renamed_root_does_not_redirect_an_already_open_descriptor(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "plan.md").write_text("Original plan")
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "plan.md").write_text("SECRET")
    actual_open = os.open
    swapped = False

    def swap_after_open(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = actual_open(path, flags, *args, **kwargs)
        if path == "docs" and not swapped:
            swapped = True
            root.rename(tmp_path / "original-docs")
            root.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(os, "open", swap_after_open)
    result = _read(root / "plan.md", (root,))
    assert result["state"] == "readable"
    assert result["content"] == "Original plan"


def test_fifo_and_directory_are_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "pipe.md"
    os.mkfifo(fifo)
    directory = tmp_path / "folder.md"
    directory.mkdir()
    for path in (fifo, directory):
        result = _read(path, (tmp_path,))
        assert result["state"] == "unsupported"
        assert result["content"] is None


def test_filesystem_failures_do_not_leak_errors_paths_or_partial_content(tmp_path, monkeypatch):
    path = tmp_path / "plan.md"
    path.write_text("Plan")

    def denied(*args, **kwargs):
        raise PermissionError("/private/secret/password: RAW_SECRET")

    monkeypatch.setattr(os, "read", denied)
    result = _read(path, (tmp_path,))
    assert result["state"] == "unavailable"
    assert result["content"] is None
    assert "RAW_SECRET" not in json.dumps(result)
    assert "/private/secret" not in json.dumps(result)
