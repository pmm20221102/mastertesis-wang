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


Batch Nav2 preparation/execution must use the same verified RAG and map profile
as prediction generation. From the repository root (in the inference Python
environment with Chroma installed):

    .venv/bin/python experiment/task-chain-60/run_nav2_batch.py \
      "$RUN_DIR/predictions.jsonl" --db-dir "$RAG_DB" \
      --output-dir "$RUN_DIR/nav2-prepare" --prepare-only

For execution, source the ROS/bridge overlays, set the dedicated ROS_DOMAIN_ID,
remove --prepare-only and choose a new output directory. The ROS child uses
/usr/bin/python3. For another profile pass --bundle and --defaults; --graph
defaults to the canonical graph beside --db-dir and any override must match
its verified manifest hash.

Use the [root README fresh-generation command](../../README.md)
to capture provenance via load_batch_sources before inference and store that
provenance on every prediction row. Required fields are rag_build_id,
rag_manifest_sha256, rag_vector_content_sha256, source_dsg_sha256,
semantic_review_csv_sha256 (explicit null for unreviewed builds),
canonical_graph_sha256, map_bundle_id, map_bundle_sha256 and start_profile_sha256.
The shared plan_nav2 profile/RAG verifier checks source DSG, semantic review,
embedding provenance, artifact hashes and collection contents. Prediction
provenance and graph hashes must match before any mission is prepared.

Historical graph_sha256/database_sha256/raw_dsg_sha256 records remain supported
only with the exact original graph/SQLite snapshot/DSG and a verified compatible
profile. Missing, partial or mismatched provenance is rejected explicitly;
regenerate predictions if the original build is unavailable.

An accepted goal that times out is canceled and awaited to a terminal action
result. If termination cannot be confirmed, batch stops (exit 2), writes
batch_stopped and unprocessed_task_ids in summary.json, and submits no further
tasks. Missing or malformed runner results also stop the batch. Inspect the
runner's cancel/terminal diagnostics and stop the old goal before starting
another batch. Ordinary failures with confirmed terminal goals may continue.
