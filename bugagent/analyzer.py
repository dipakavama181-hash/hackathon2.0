"""Static analysis: AST-based bug/smell detection + deterministic auto-fixes."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict
from pathlib import Path

IGNORE_DIRS = {".git", ".hg", ".venv", "venv", "env", "node_modules", "__pycache__",
               ".tox", "build", "dist", ".mypy_cache", ".pytest_cache", "bugagent_report"}

RULES = {
    "E000": ("high", "Syntax error"),
    "B001": ("medium", "Bare `except:` swallows every exception, including KeyboardInterrupt"),
    "B002": ("high", "Mutable default argument is shared between calls"),
    "B003": ("low", "Comparison to None with ==/!= (use `is` / `is not`)"),
    "B004": ("high", "Use of eval()/exec() on possibly untrusted input"),
    "B005": ("low", "Unused import"),
    "B008": ("high", "subprocess call with shell=True (command injection risk)"),
}


@dataclass
class Finding:
    id: str
    rule: str
    severity: str
    file: str
    line: int
    message: str
    snippet: str = ""
    fixable: bool = False  # True when a deterministic auto-fix exists

    def to_dict(self):
        return asdict(self)


def iter_py_files(root: Path):
    for p in sorted(Path(root).rglob("*.py")):
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _mk(rule, rel, line, lines, extra="", fixable=False):
    sev, msg = RULES[rule]
    snippet = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    return Finding("", rule, sev, rel, line, msg + (f" ({extra})" if extra else ""), snippet, fixable)


def analyze_source(rel: str, source: str) -> list[Finding]:
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [_mk("E000", rel, e.lineno or 1, lines, e.msg)]

    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append(_mk("B001", rel, node.lineno, lines, fixable=True))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
            for d in defaults:
                bad = isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    and d.func.id in {"list", "dict", "set"})
                if bad:
                    out.append(_mk("B002", rel, d.lineno, lines, f"in `{node.name}`"))
        elif isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant) and comp.value is None:
                    out.append(_mk("B003", rel, node.lineno, lines, fixable=True))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                out.append(_mk("B004", rel, node.lineno, lines, node.func.id))
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    out.append(_mk("B008", rel, node.lineno, lines))

    # unused imports (skip __init__.py re-exports and star imports)
    if not rel.endswith("__init__.py"):
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        exported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets):
                if isinstance(n.value, (ast.List, ast.Tuple)):
                    exported |= {e.value for e in n.value.elts if isinstance(e, ast.Constant)}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if any(a.name == "*" for a in node.names):
                    continue
                unused = [a for a in node.names
                          if (a.asname or a.name.split(".")[0]) not in used
                          and (a.asname or a.name.split(".")[0]) not in exported]
                if unused and not (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
                    simple = len(node.names) == 1 and node.lineno == node.end_lineno
                    out.append(_mk("B005", rel, node.lineno, lines,
                                   ", ".join(a.asname or a.name for a in unused), fixable=simple))
    return out


def analyze_repo(root: Path) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for p in iter_py_files(root):
        try:
            src = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings += analyze_source(str(p.relative_to(root)).replace("\\", "/"), src)
    sev = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (sev[f.severity], f.file, f.line))
    for i, f in enumerate(findings, 1):
        f.id = f"F{i:03d}"
    return findings


def deterministic_fix(source: str, f: Finding) -> str | None:
    """Return fixed source for rules with a safe mechanical fix, else None."""
    lines = source.splitlines(keepends=True)
    if not (0 < f.line <= len(lines)):
        return None
    ln = lines[f.line - 1]
    if f.rule == "B001":
        new = re.sub(r"^(\s*)except\s*:", r"\1except Exception:", ln)
    elif f.rule == "B003":
        new = re.sub(r"!=\s*None\b", "is not None", ln)
        new = re.sub(r"==\s*None\b", "is None", new)
    elif f.rule == "B005" and f.fixable:
        new = ""
    else:
        return None
    if new == ln:
        return None
    lines[f.line - 1] = new
    return "".join(lines)
