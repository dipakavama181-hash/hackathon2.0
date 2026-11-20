"""The agent loop: baseline -> analyze -> fix (verified in a sandbox) -> re-test -> report data."""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from .analyzer import analyze_repo, deterministic_fix, Finding
from .llm import files_from_traceback, parse_edits, render_files
from .patcher import EditError, Sandbox
from .testrunner import TestResult, run_tests


class Agent:
    def __init__(self, repo, llm=None, run_tests_enabled=True, max_attempts=2,
                 max_llm_fixes=8, log=print, llm_note=""):
        self.repo = Path(repo).resolve()
        self.llm, self.llm_note = llm, llm_note
        self.run_tests_enabled = run_tests_enabled
        self.max_attempts, self.max_llm_fixes, self.log = max_attempts, max_llm_fixes, log
        self.sb = Sandbox(self.repo)
        self.fixes: list[dict] = []

    # ---------- helpers ----------
    def _tests(self) -> TestResult:
        if not self.run_tests_enabled:
            return TestResult(note="tests disabled")
        return run_tests(self.sb.root)

    @staticmethod
    def _no_regression(cur: TestResult, after: TestResult) -> bool:
        if not (cur.ran and after.ran):
            return True  # nothing to compare; syntax is still checked by Sandbox.apply
        return set(after.failing) <= set(cur.failing)

    def _record(self, **kw):
        self.fixes.append(kw)
        self.log(f"  [{kw['status']}] {kw['target']} ({kw['source']})")

    @staticmethod
    def _count(findings, file, rule):
        return sum(1 for f in findings if f.file == file and f.rule == rule)

    # ---------- LLM loop with verification + feedback ----------
    def _llm_loop(self, prompt, cur, improved):
        messages = [{"role": "user", "content": prompt}]
        note, explanation = "", ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                reply = self.llm.chat(messages)
            except Exception as e:  # network/API failure
                return None, cur, {"note": f"LLM error: {e}", "attempts": attempt, "explanation": ""}
            explanation, edits = parse_edits(reply)
            messages.append({"role": "assistant", "content": reply})
            if not edits:
                return None, cur, {"note": explanation or "no edits proposed", "attempts": attempt,
                                   "explanation": explanation}
            try:
                snap = self.sb.apply(edits)
            except EditError as e:
                note = f"edit could not be applied: {e}"
                messages.append({"role": "user", "content": f"{note}. Reply again with corrected JSON."})
                continue
            after = self._tests()
            if self._no_regression(cur, after) and improved(after):
                return snap, after, {"diff": self.sb.diff_from(snap), "attempts": attempt,
                                     "explanation": explanation, "note": ""}
            self.sb.restore(snap)
            note = "fix rejected: tests regressed or the problem was not resolved"
            tail = after.output[-2500:] if after.ran else after.note
            messages.append({"role": "user", "content":
                             f"That change was rolled back ({note}). Test output after your change:\n{tail}\n"
                             "Reply again with a corrected JSON fix."})
        return None, cur, {"note": note, "attempts": self.max_attempts, "explanation": explanation}

    # ---------- phases ----------
    def _fix_failing_tests(self, cur: TestResult) -> TestResult:
        self.log("Phase 1: fixing failing tests with the LLM")
        rels = files_from_traceback(cur.output, self.sb.root)
        prompt = ("These tests fail. Find the root cause in the source and fix it.\n\n"
                  f"Failing: {', '.join(cur.failing)}\n\nPytest output:\n{cur.output[-6000:]}\n\n"
                  f"Relevant files:\n{render_files(self.sb, rels)}")
        before = len(cur.failing)
        snap, after, meta = self._llm_loop(prompt, cur, lambda a: a.ran and len(a.failing) < before)
        status = "accepted" if snap else "rejected"
        self._record(kind="test-failure", target=", ".join(cur.failing), source="llm", status=status,
                     tests_after=(after.to_dict() if snap else None), **meta)
        return after if snap else cur

    def _fix_deterministic(self, findings: list[Finding], cur: TestResult) -> TestResult:
        self.log("Phase 2: deterministic auto-fixes")
        todo = sorted((f for f in findings if f.fixable), key=lambda f: (f.file, -f.line))
        for f in todo:
            src = self.sb.read(f.file)
            new = deterministic_fix(src, f)
            if new is None:
                continue
            try:
                snap = self.sb.apply_whole(f.file, new)
            except EditError as e:
                self._record(kind="static", target=f"{f.id} {f.rule} {f.file}:{f.line}", source="rule",
                             status="rejected", note=str(e), explanation=f.message, diff="", attempts=1)
                continue
            after = self._tests()
            if self._no_regression(cur, after):
                self._record(kind="static", target=f"{f.id} {f.rule} {f.file}:{f.line}", source="rule",
                             status="accepted", explanation=f.message, diff=self.sb.diff_from(snap),
                             attempts=1, note="", tests_after=after.to_dict() if after.ran else None)
                cur = after if after.ran else cur
            else:
                self.sb.restore(snap)
                self._record(kind="static", target=f"{f.id} {f.rule} {f.file}:{f.line}", source="rule",
                             status="rejected", explanation=f.message, diff="", attempts=1,
                             note="auto-fix broke tests; rolled back")
        return cur

    def _fix_with_llm(self, findings: list[Finding], cur: TestResult) -> TestResult:
        targets = [f for f in findings if f.severity in ("high", "medium")][: self.max_llm_fixes]
        if not targets:
            return cur
        self.log("Phase 3: LLM fixes for remaining findings")
        for f in targets:
            prompt = (f"Static analysis finding {f.rule} ({f.severity}) at {f.file}:{f.line}: {f.message}\n"
                      f"Offending line: `{f.snippet}`\n\nFix this issue without changing behaviour "
                      f"otherwise.\n\n{render_files(self.sb, [f.file])}")
            before = self._count(analyze_repo(self.sb.root), f.file, f.rule)
            snap, after, meta = self._llm_loop(
                prompt, cur,
                lambda a, f=f, before=before: self._count(analyze_repo(self.sb.root), f.file, f.rule) < before)
            self._record(kind="static", target=f"{f.id} {f.rule} {f.file}:{f.line}", source="llm",
                         status="accepted" if snap else "rejected",
                         tests_after=(after.to_dict() if snap and after.ran else None), **meta)
            if snap and after.ran:
                cur = after
        return cur

    # ---------- main ----------
    def run(self) -> dict:
        t0 = time.time()
        try:
            self.log("Baseline: running tests + static analysis")
            baseline = self._tests()
            initial = analyze_repo(self.sb.root)
            self.log(f"  tests: {self._summ(baseline)} | findings: {len(initial)}")
            cur = baseline
            if self.llm and baseline.ran and baseline.failing:
                cur = self._fix_failing_tests(cur)
            cur = self._fix_deterministic(analyze_repo(self.sb.root), cur)
            if self.llm:
                cur = self._fix_with_llm(analyze_repo(self.sb.root), cur)
            final_tests = self._tests()
            final_findings = analyze_repo(self.sb.root)
            patch = self.sb.full_diff()
            changed = self.sb.changed_files()
            return {
                "repo": str(self.repo), "duration_s": round(time.time() - t0, 1),
                "llm_enabled": bool(self.llm), "llm_note": self.llm_note,
                "baseline_tests": baseline.to_dict(), "final_tests": final_tests.to_dict(),
                "initial_findings": [f.to_dict() for f in initial],
                "remaining_findings": [f.to_dict() for f in final_findings],
                "fixes": self.fixes, "patch": patch, "changed_files": changed,
            }
        finally:
            pass

    @staticmethod
    def _summ(t: TestResult) -> str:
        return (f"{t.passed} passed, {t.failed} failed, {t.errors} errors" if t.ran else f"not run ({t.note})")

    def apply_to_repo(self):
        return self.sb.apply_to_repo()

    def cleanup(self):
        self.sb.cleanup()
