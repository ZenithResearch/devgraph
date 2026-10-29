"""Pinned launcher bootstrap: first-party code never loads cached/native aliases.

Entry scripts execute these pinned source bytes directly before importing the
devgraph package. This is an application-code drift fence, not OS isolation or
immutability of third-party dependencies.
"""

import atexit
import importlib.abc
import importlib.machinery
import importlib.util
import os
import stat
import sys
import tempfile
from pathlib import Path


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 1048576:
                raise ImportError("unsafe_first_party_source")
            return self.source_to_code(file.read(), self.path)


class _SourceOnlyFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root):
        self.root = root

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "devgraph" and not fullname.startswith("devgraph."):
            return None
        parts = fullname.split(".")
        if not all(part.isidentifier() for part in parts):
            raise ModuleNotFoundError("unsupported_first_party_module")
        base = self.root.joinpath(*parts)
        package, module = base / "__init__.py", base.with_suffix(".py")
        if package.is_file() and module.is_file():
            raise ImportError("ambiguous_first_party_module")
        source = package if package.is_file() else module
        if not source.is_file() or source.resolve().parent != source.parent:
            raise ModuleNotFoundError("first_party_source_required")
        return importlib.util.spec_from_file_location(
            fullname,
            source,
            loader=_SourceOnlyLoader(fullname, str(source)),
            submodule_search_locations=[str(base)] if source == package else None,
        )


def activate(source_root):
    source_root = Path(source_root).resolve(strict=True)
    cache = tempfile.TemporaryDirectory(prefix="devgraph-private-import-")
    # -B alone still permits reading old .pyc. A fresh private prefix avoids
    # inherited caches, and the first-party loader always compiles pinned .py.
    sys.pycache_prefix = cache.name
    sys.dont_write_bytecode = True
    atexit.register(cache.cleanup)
    sys.meta_path.insert(0, _SourceOnlyFinder(source_root))
    sys.path.insert(0, str(source_root))
