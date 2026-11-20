"""Run pytest in a directory and parse the outcome."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict


@dataclass
class TestResult:
    ran: bool = False
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failing: list[str] = field(default_factory=list)
    output: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.ran and not self.failing and self.failed == 0 and self.errors == 0

    def to_dict(self):
        d = asdict(self)
        d["output"] = self.output[-3000:]
        return d


def run_tests(root, timeout: int = 300) -> TestResult:
    cmd = [sys.executable, "-m", "pytest", "-q", "-rfE", "--tb=short", "-p", "no:cacheprovider"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return TestResult(ran=False, note=f"test run timed out after {timeout}s")
    out = p.stdout + p.stderr
    if "No module named pytest" in out:
        return TestResult(ran=False, output=out, note="pytest is not installed")
    if p.returncode == 5:
        return TestResult(ran=False, output=out, note="no tests collected")
    counts = {"passed": 0, "failed": 0, "error": 0}
    for n, word in re.findall(r"(\d+) (passed|failed|errors?)\b", out.splitlines()[-1] if out.strip() else ""):
        counts["error" if word.startswith("error") else word] = int(n)
    failing = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, flags=re.M)
    res = TestResult(True, counts["passed"], counts["failed"], counts["error"], failing, out)
    if p.returncode not in (0, 1) and not failing:  # interrupted / usage error
        res.ran, res.note = False, f"pytest exited with code {p.returncode}"
    return res
