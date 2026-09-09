"""TEST FIXTURE ONLY: emit controlled CLI outputs without model calls."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--input-csv", type=Path)
parser.add_argument("--output-csv", type=Path)
parser.add_argument("--run-log", type=Path)
parser.add_argument("--limit")
args = parser.parse_args()
with args.input_csv.open(encoding="utf-8", newline="") as stream:
    row = next(csv.DictReader(stream))
if "[FAIL]" in row["user_prompt"]:
    sys.exit(1)
if "[WAIT]" in row["user_prompt"]:
    time.sleep(60)
with args.output_csv.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=[*row, "GPT预测结果"])
    writer.writeheader()
    writer.writerow({**row, "GPT预测结果": "[]"})
args.run_log.write_text(json.dumps({"success": True, "elapsed_sec": 0.1,
    "classic_checks": [{"check_name": "motion_physics_continuity", "execution_status": "ok",
                        "decision": "not_detected", "evidence_level": "supported",
                        "limitations": [], "tool_refs": ["test_fixture"]}]}), encoding="utf-8")
