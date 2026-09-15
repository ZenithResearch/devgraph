"""Bounded local text previews for already-authorized attached Artifacts.

This module is deliberately not a URL fetcher or an authorization boundary. The
Work reader must authenticate, authorize the parent, and resolve the requested
Artifact attachment before calling it. Only stored, allowlisted metadata enters
this reader; an HTTP caller cannot supply a path or a replacement URI.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict
from urllib.parse import unquote_to_bytes, urlsplit

DOCUMENT_ROOTS_VARIABLE = "DEVGRAPH_DOCUMENT_ROOTS"
MAX_DOCUMENT_BYTES = 256 * 1024
_TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown"})
_TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}

DocumentState = Literal[
    "readable", "metadata_only", "unconfigured", "missing", "unsupported", "unavailable"
]


class DocumentReadResult(TypedDict):
    schema: Literal["devgraph.work-document.v1"]
    artifact_id: str
    state: DocumentState
    message: str
    media_type: str | None
    size_bytes: int | None
    content: str | None


def _absolute_path(value: object) -> PurePosixPath | None:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        return None
    return PurePosixPath(value)


def document_roots_from_env(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Read an explicit JSON list of absolute roots; absent/invalid fails closed.

    No default home, data, working, or temporary directory is ever admitted.
    Symlinks, including root ancestors, are rejected when opening a document.
    Canonical physical paths must therefore be configured (e.g. /private/tmp,
    not macOS's /tmp symlink). Configuration never creates directories.
    """
    raw = (os.environ if environ is None else environ).get(DOCUMENT_ROOTS_VARIABLE, "")
    if not raw or len(raw) > 65536:
        return ()
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        return ()
    roots: list[Path] = []
    for value in values:
        path = _absolute_path(value)
        if path is None or path == PurePosixPath("/"):
            return ()
        root = Path(path)
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _result(
    artifact_id: str,
    state: DocumentState,
    message: str,
    *,
    media_type: str | None = None,
    size_bytes: int | None = None,
    content: str | None = None,
) -> DocumentReadResult:
    return {
        "schema": "devgraph.work-document.v1",
        "artifact_id": artifact_id,
        "state": state,
        "message": message,
        "media_type": media_type,
        "size_bytes": size_bytes,
        "content": content,
    }


def _local_path(uri: str) -> PurePosixPath | None:
    if uri.startswith("/"):
        return _absolute_path(uri)
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "file"
            or parsed.netloc not in {"", "localhost"}
            or parsed.query
            or parsed.fragment
            or re.search(r"%(?![0-9a-fA-F]{2})", parsed.path)
        ):
            return None
        return _absolute_path(unquote_to_bytes(parsed.path).decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError):
        return None


@contextmanager
def _document_descriptor(root: PurePosixPath, relative: PurePosixPath):
    """Bind every lookup to an open directory, never following any symlink.

    No resolve-then-open gap exists: both configured-root ancestors and document
    descendants are traversed with directory descriptors and O_NOFOLLOW. The
    final file is checked and read through the same descriptor. NONBLOCK avoids
    blocking on a malicious FIFO before the regular-file check.
    """
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise OSError("safe_local_document_open_unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors: list[int] = []
    try:
        current = os.open("/", directory_flags)
        descriptors.append(current)
        for part in (*root.parts[1:], *relative.parts[:-1]):
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=current,
        )
        descriptors.append(descriptor)
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_artifact_document(
    artifact_id: str,
    metadata: Mapping[str, object],
    roots: Sequence[Path],
) -> DocumentReadResult:
    """Preview a previously resolved Artifact; never fetch a remote location.

    Callers must prove this Artifact is attached to their authorized Work before
    calling. Invalid paths and filesystem errors return redaction-safe states,
    never exception strings, filesystem listings, or partial document content.
    """
    if "id" in metadata and metadata["id"] != artifact_id:
        return _result(artifact_id, "unavailable", "The artifact metadata does not match.")
    uri = metadata.get("uri", "")
    if not isinstance(uri, str) or len(uri) > 8192:
        return _result(artifact_id, "unavailable", "The artifact location is invalid.")
    if not uri:
        return _result(artifact_id, "metadata_only", "This artifact has no document location.")
    if uri.lower().startswith(("https://", "http://")):
        return _result(
            artifact_id, "metadata_only", "Remote documents remain at their source; open the link."
        )
    path = _local_path(uri)
    if path is None:
        return _result(artifact_id, "unavailable", "This document location cannot be previewed.")
    if not roots:
        return _result(
            artifact_id, "unconfigured", "Local document roots have not been configured."
        )

    selected_root: PurePosixPath | None = None
    relative: PurePosixPath | None = None
    for candidate in roots:
        root = _absolute_path(str(candidate))
        if root is None or root == PurePosixPath("/") or path == root:
            continue
        try:
            contained = path.relative_to(root)
        except ValueError:
            continue
        if selected_root is None or len(root.parts) > len(selected_root.parts):
            selected_root, relative = root, contained
    if selected_root is None or relative is None:
        return _result(artifact_id, "unavailable", "The document is outside configured roots.")

    declared_type = metadata.get("media_type", "")
    if not isinstance(declared_type, str) or len(declared_type) > 256:
        return _result(artifact_id, "unsupported", "The document format is not supported.")
    media_type = declared_type.split(";", 1)[0].strip().lower()
    if not media_type:
        media_type = _TEXT_SUFFIXES.get(path.suffix.lower(), "")
    if media_type not in _TEXT_MEDIA_TYPES:
        return _result(artifact_id, "unsupported", "Only UTF-8 text and Markdown can be previewed.")

    try:
        with _document_descriptor(selected_root, relative) as descriptor:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                return _result(artifact_id, "unsupported", "Only regular text files can be read.")
            if before.st_size > MAX_DOCUMENT_BYTES:
                return _result(
                    artifact_id, "unsupported", "The document exceeds the 256 KiB limit."
                )
            chunks: list[bytes] = []
            size = 0
            while size <= MAX_DOCUMENT_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_DOCUMENT_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > MAX_DOCUMENT_BYTES:
                return _result(
                    artifact_id, "unsupported", "The document exceeds the 256 KiB limit."
                )
            after = os.fstat(descriptor)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                return _result(
                    artifact_id, "unavailable", "The document changed during reading; retry."
                )
            content = b"".join(chunks).decode("utf-8-sig", errors="strict")
            if "\x00" in content:
                return _result(artifact_id, "unsupported", "This document is not UTF-8 text.")
            return _result(
                artifact_id, "readable", "Document loaded.",
                # The envelope counts returned UTF-8 bytes, after removing an optional BOM.
                media_type=media_type, size_bytes=len(content.encode("utf-8")), content=content,
            )
    except FileNotFoundError:
        return _result(artifact_id, "missing", "The linked document could not be found.")
    except UnicodeError:
        return _result(artifact_id, "unsupported", "This document is not UTF-8 text.")
    except (OSError, ValueError):
        return _result(artifact_id, "unavailable", "The linked document cannot be read safely.")
