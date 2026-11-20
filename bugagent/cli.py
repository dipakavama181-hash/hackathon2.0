import argparse
import sys
from pathlib import Path

from .agent import Agent
from .llm import DEFAULT_MODEL, make_default_llm
from .report import save


def main(argv=None, llm=None) -> int:
    ap = argparse.ArgumentParser(prog="bugagent", description="AI bug-finding & fixing agent")
    ap.add_argument("path", help="repository to analyze")
    ap.add_argument("--out", help="report directory (default: <path>/bugagent_report)")
    ap.add_argument("--no-llm", action="store_true", help="static analysis + deterministic fixes only")
    ap.add_argument("--no-tests", action="store_true", help="do not run the test suite")
    ap.add_argument("--apply", action="store_true", help="write accepted fixes back into the repo")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--max-llm-fixes", type=int, default=8)
    a = ap.parse_args(argv)

    repo = Path(a.path).resolve()
    note = "disabled by --no-llm" if a.no_llm else ""
    if llm is None and not a.no_llm:
        llm, note = make_default_llm(a.model)
    if note:
        print(f"LLM off: {note}")
    agent = Agent(repo, llm, not a.no_tests, a.max_attempts, a.max_llm_fixes, llm_note=note)
    try:
        data = agent.run()
        paths = save(data, Path(a.out) if a.out else repo / "bugagent_report")
        if a.apply:
            print("Applied to repo:", ", ".join(agent.apply_to_repo()) or "nothing")
    finally:
        agent.cleanup()
    print(f"\nReport: {paths['markdown']}\nPatch:  {paths['patch']}")
    return 1 if data["final_tests"]["failing"] else 0


if __name__ == "__main__":
    sys.exit(main())
