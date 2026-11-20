"""Turn agent results into an actionable Markdown + JSON report."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

RECS = {
    "B001": "Enable ruff/flake8 rule E722 in CI so bare `except:` cannot be merged.",
    "B002": "Enable flake8-bugbear B006 (mutable defaults); these cause state leaking between calls.",
    "B003": "Enable ruff rule E711 and run `ruff --fix` in a pre-commit hook.",
    "B004": "Replace eval()/exec() with `ast.literal_eval`, a parser, or an explicit dispatch table.",
    "B005": "Run `ruff --select F401 --fix` in pre-commit to keep imports clean.",
    "B008": "Pass an argument list to subprocess (shell=False) and validate inputs; add bandit (S602) to CI.",
    "E000": "Add a CI step that byte-compiles the repo (`python -m compileall`) so syntax errors fail fast.",
}


def _tests_line(t: dict) -> str:
    if not t["ran"]:
        return f"not run ({t['note'] or 'n/a'})"
    return f"{t['passed']} passed, {t['failed']} failed, {t['errors']} errors"


def recommendations(d: dict) -> list[str]:
    recs, rules = [], Counter(f["rule"] for f in d["initial_findings"])
    bt = d["baseline_tests"]
    if not bt["ran"] and "no tests" in (bt["note"] or ""):
        recs.append("**No tests were found.** Add a pytest suite; the agent can only verify fixes against tests that exist.")
    if bt["ran"] and bt["failing"]:
        recs.append("Make the test suite a required CI check; it failed before any change was made.")
    for r, n in rules.most_common():
        recs.append(f"{RECS[r]} ({n} occurrence{'s' if n > 1 else ''} of {r})")
    if d["remaining_findings"]:
        recs.append(f"{len(d['remaining_findings'])} finding(s) still need a human or an LLM-enabled run (see below).")
    if not d["llm_enabled"]:
        recs.append(f"Re-run with the LLM enabled for logic bugs and complex fixes ({d['llm_note'] or 'enabled by default if key set'}).")
    return recs


def to_markdown(d: dict) -> str:
    acc = [f for f in d["fixes"] if f["status"] == "accepted"]
    rej = [f for f in d["fixes"] if f["status"] != "accepted"]
    sev = Counter(f["severity"] for f in d["initial_findings"])
    L = [f"# Bug Analysis Report", "",
         f"- **Repo:** `{d['repo']}`", f"- **Generated:** {datetime.now():%Y-%m-%d %H:%M}",
         f"- **Duration:** {d['duration_s']}s | **LLM:** {'on' if d['llm_enabled'] else 'off'}", "",
         "## Summary", "", "| Metric | Before | After |", "|---|---|---|",
         f"| Tests | {_tests_line(d['baseline_tests'])} | {_tests_line(d['final_tests'])} |",
         f"| Static findings | {len(d['initial_findings'])} "
         f"(high {sev['high']}, medium {sev['medium']}, low {sev['low']}) | {len(d['remaining_findings'])} |",
         f"| Fixes accepted / rejected | | {len(acc)} / {len(rej)} |", "",
         "## Recommended actions", ""]
    L += [f"{i}. {r}" for i, r in enumerate(recommendations(d), 1)] or ["Nothing to do."]
    L += ["", "## Fixes applied (verified in sandbox)", ""]
    if not acc:
        L.append("_None._")
    for f in acc:
        L += [f"### {f['target']}  \n*source: {f['source']}, attempts: {f['attempts']}*", "",
              f["explanation"] or "", "", "```diff", f["diff"].rstrip(), "```", ""]
    L += ["## Fixes attempted but rejected", ""]
    if not rej:
        L.append("_None._")
    for f in rej:
        L.append(f"- **{f['target']}** ({f['source']}): {f['note'] or 'rejected'}")
    L += ["", "## Remaining findings (need human review)", ""]
    if d["remaining_findings"]:
        L += ["| ID | Sev | Rule | Location | Message |", "|---|---|---|---|---|"]
        L += [f"| {f['id']} | {f['severity']} | {f['rule']} | `{f['file']}:{f['line']}` | {f['message']} |"
              for f in d["remaining_findings"]]
    else:
        L.append("_None._")
    if d["final_tests"]["failing"]:
        L += ["", "## Still failing tests", ""] + [f"- `{t}`" for t in d["final_tests"]["failing"]]
    return "\n".join(L) + "\n"


def save(d: dict, out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = {"markdown": out / "report.md", "json": out / "report.json", "patch": out / "fixes.patch"}
    paths["markdown"].write_text(to_markdown(d), encoding="utf-8")
    paths["json"].write_text(json.dumps({k: v for k, v in d.items() if k != "patch"}, indent=2), encoding="utf-8")
    paths["patch"].write_text(d["patch"], encoding="utf-8")
    return paths
