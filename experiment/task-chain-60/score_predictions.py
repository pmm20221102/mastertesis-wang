import argparse
import json
from pathlib import Path

from score_taskchain_benchmark import read_jsonl, score_records


def main():
    parser = argparse.ArgumentParser(description="Score one set of 60 Hospital task-chain predictions.")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    gold_path = Path(__file__).resolve().parent / "reference/taskchain_gold.jsonl"
    trials, summary = score_records(read_jsonl(args.predictions), read_jsonl(gold_path), repeats=1)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump({"summary": summary, "trials": trials}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"{sum(row['trial_success'] for row in trials)}/{len(trials)}")


if __name__ == "__main__":
    main()
