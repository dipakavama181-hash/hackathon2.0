# bugagent: AI agent for finding, fixing and reporting bugs

Analyzes a Python codebase, runs its tests, proposes fixes (rule-based + Claude), **verifies every fix in a
sandbox copy**, rolls back anything that regresses, and writes an actionable report.

## Quick start
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # optional; without it only rule-based fixes run
python -m bugagent path/to/repo            # report -> path/to/repo/bugagent_report/
python -m bugagent path/to/repo --apply    # also write accepted fixes into the repo
python -m bugagent path/to/repo --no-llm   # offline: static analysis + deterministic fixes
python -m bugagent examples/buggy_shop --out /tmp/report   # try the demo
```
Outputs: `report.md` (human), `report.json` (machine / CI), `fixes.patch` (`git apply`-able).
Exit code is 1 if tests still fail after the run, so it can gate CI.

## How the agent works
```
baseline tests + AST analysis
 -> Phase 1  failing tests   -> Claude proposes edits -> apply in sandbox -> re-run tests
                                 (accept if failures strictly decrease and nothing new fails,
                                  else rollback and feed the test output back to Claude, N attempts)
 -> Phase 2  mechanical rules (bare except, `== None`, unused import) -> auto-fix, test, keep/rollback
 -> Phase 3  remaining high/medium findings -> Claude -> same verify/rollback loop
 -> final tests + findings -> report.md / report.json / fixes.patch
```
Safety: edits are exact-match search/replace (must match once), syntax-checked, confined to the repo copy;
nothing touches your repo unless `--apply`.

## Layout
| File | Role |
|---|---|
| `bugagent/analyzer.py` | AST rules B001-B008, E000, deterministic fixers |
| `bugagent/testrunner.py` | runs pytest, parses failures |
| `bugagent/patcher.py` | sandbox, atomic edits, diffs, rollback |
| `bugagent/llm.py` | Claude client (`BUGAGENT_MODEL` env to change model), prompts, JSON parsing |
| `bugagent/agent.py` | the verify-and-rollback agent loop |
| `bugagent/report.py` | Markdown/JSON/patch generation + recommendations |
| `tests/test_agent.py` | end-to-end test using a fake LLM (no network needed) |

## Extending
- New rule: add to `RULES` + a branch in `analyze_source` (+ optional `deterministic_fix`).
- Other languages: swap `analyzer.py` and the command in `testrunner.py` (e.g. `npm test`, `go test`).
- Other LLMs: pass any object with `.chat(messages) -> str` as `llm`.
- Ideas: git-branch + PR creation, per-file batching for big suites, coverage-guided test generation.
