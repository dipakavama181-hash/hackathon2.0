import json
import shutil
from pathlib import Path

from bugagent.analyzer import analyze_source
from bugagent.cli import main
from bugagent.llm import parse_edits

DEMO = Path(__file__).parent.parent / "examples" / "buggy_shop"


def test_analyzer_rules():
    src = "import os\ndef f(x=[]):\n    try:\n        pass\n    except:\n        pass\n    return x == None\n"
    assert {f.rule for f in analyze_source("m.py", src)} == {"B001", "B002", "B003", "B005"}


def test_parse_edits_handles_fences():
    expl, edits = parse_edits('```json\n{"explanation":"x","edits":[{"file":"a.py","old":"1","new":"2"}]}\n```')
    assert expl == "x" and edits[0].file == "a.py"


class FakeLLM:
    """Stands in for Claude: fixes the two logic bugs in the demo repo, declines everything else."""
    def __init__(self): self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({"explanation": "discount math and shared mutable default", "edits": [
                {"file": "shop/cart.py", "old": "def add_item(item, items=[]):\n    items.append(item)",
                 "new": "def add_item(item, items=None):\n    if items is None:\n        items = []\n    items.append(item)"},
                {"file": "shop/cart.py", "old": "return subtotal * discount",
                 "new": "return subtotal * (1 - discount)"}]})
        return json.dumps({"explanation": "needs a design decision", "edits": []})


def test_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(DEMO, repo)
    code = main([str(repo), "--out", str(tmp_path / "out")], llm=FakeLLM())
    data = json.loads((tmp_path / "out" / "report.json").read_text())
    assert code == 0
    assert data["baseline_tests"]["failed"] == 3 and data["final_tests"]["failed"] == 0
    rules_left = {f["rule"] for f in data["remaining_findings"]}
    assert rules_left == {"B004"}  # eval() needs human review
    assert (tmp_path / "out" / "report.md").read_text().startswith("# Bug Analysis Report")
    assert "except:" in (repo / "shop" / "cart.py").read_text()  # original untouched without --apply
