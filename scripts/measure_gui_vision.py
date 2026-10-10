"""Measure the owner-approved vision model on the fixed screenshot set and record the result (#1189).

usage: measure_gui_vision.py [model-id]   writes tests/fixtures/gui_vision/measurement.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from squad.gui_test_vision import Vision, claude_cli_model, measure  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "gui_vision"

if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5-5"
    result = {"model": model, **measure(Vision(claude_cli_model(model)), json.loads((FIX / "cases.json").read_text()), FIX)}
    (FIX / "measurement.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{model}: {result['correct']}/{result['total']} correct, ${result['cost']:.4f}")
