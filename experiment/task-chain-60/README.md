# Hospital 60-task gold evaluation

The public instructions are in public/tasks_en.jsonl. The frozen reference answers
are in reference/taskchain_gold.jsonl. They combine the original 40 planning
annotations and 20 retrieval annotations using the original composite scorer's
normalization. Keep gold separate from prediction generation.

From the repository root, score the archived primary-ablation predictions with
Python 3.10 or later. The scorer uses only the Python standard library.

    for condition in full no_llm1 raw_dsg no_llm2; do
      python3 experiment/task-chain-60/score_predictions.py \
        "experiment/task-chain-60/runs/surface-v1-llm2-60x4-20261020/predictions/$condition.jsonl" \
        --output "$condition.gold-scores.json"
    done

The archived results are 47/60, 12/60, 40/60, and 40/60, respectively.
Each output file contains the aggregate metrics and the per-task decisions.
Use new output filenames when rerunning. The scorer rejects missing, duplicate,
or unexpected task/repeat records.

To score a fresh inference run, pass its prediction JSONL in the same format
(one record per task with repeat set to 1). New LLM outputs and scores can vary.
This evaluation scores the fixed Hospital task chains and boundary responses.
The GUI's interactive validation and Nav2 execution use separate criteria.
