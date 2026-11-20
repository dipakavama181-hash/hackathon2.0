"""Sandbox copy of the repo + safe search/replace edits + diffs."""
from __future__ import annotations

import ast
import difflib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .analyzer import IGNORE_DIRS


class EditError(Exception):
    pass


@dataclass
class Edit:
    file: str
    old: str
    new: str


class Sandbox:
    def __init__(self, src: Path):
        self.src = Path(src).resolve()
        self._tmp = tempfile.mkdtemp(prefix="bugagent_")
        self.root = Path(self._tmp) / "repo"
        shutil.copytree(self.src, self.root, ignore=shutil.ignore_patterns(*IGNORE_DIRS, "*.pyc"))

    def _path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise EditError(f"path escapes repo: {rel}")
        return p

    def read(self, rel: str) -> str:
        p = self._path(rel)
        if not p.exists():
            raise EditError(f"file not found: {rel}")
        return p.read_text(encoding="utf-8")

    def write(self, rel: str, text: str):
        self._path(rel).write_text(text, encoding="utf-8")

    def apply(self, edits: list[Edit]) -> dict[str, str]:
        """Apply edits atomically. Returns snapshot of original contents for restore()."""
        snap: dict[str, str] = {}
        try:
            for e in edits:
                if not e.old:
                    raise EditError(f"empty 'old' text for {e.file}")
                text = self.read(e.file)
                snap.setdefault(e.file, text)
                n = text.count(e.old)
                if n != 1:
                    raise EditError(f"'old' text must match exactly once in {e.file} (found {n})")
                self.write(e.file, text.replace(e.old, e.new, 1))
            for rel in snap:
                if rel.endswith(".py"):
                    try:
                        ast.parse(self.read(rel))
                    except SyntaxError as se:
                        raise EditError(f"edit leaves {rel} with a syntax error: {se.msg} (line {se.lineno})")
        except EditError:
            self.restore(snap)
            raise
        return snap

    def apply_whole(self, rel: str, new_text: str) -> dict[str, str]:
        """Replace a whole file's content (used by deterministic fixers); syntax-checked."""
        old = self.read(rel)
        if rel.endswith(".py"):
            try:
                ast.parse(new_text)
            except SyntaxError as se:
                raise EditError(f"fix leaves {rel} with a syntax error: {se.msg}")
        self.write(rel, new_text)
        return {rel: old}

    def restore(self, snap: dict[str, str]):
        for rel, text in snap.items():
            self.write(rel, text)

    def diff_from(self, snap: dict[str, str]) -> str:
        out = []
        for rel, before in snap.items():
            after = self.read(rel)
            out += difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}")
        return "".join(out)

    def changed_files(self) -> list[str]:
        changed = []
        for p in self.root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.root)
                orig = self.src / rel
                if not orig.exists() or orig.read_bytes() != p.read_bytes():
                    changed.append(str(rel).replace("\\", "/"))
        return sorted(changed)

    def full_diff(self) -> str:
        out = []
        for rel in self.changed_files():
            try:
                new = (self.root / rel).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            o = self.src / rel
            old = o.read_text(encoding="utf-8") if o.exists() else ""
            out += difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}")
        return "".join(out)

    def apply_to_repo(self) -> list[str]:
        files = self.changed_files()
        for rel in files:
            dst = self.src / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / rel, dst)
        return files

    def cleanup(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
