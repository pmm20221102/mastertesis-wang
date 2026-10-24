# Hospital natural-language-to-Nav2 interface smoke

This folder records interface diagnostics, not benchmark results. Both runs
used the instruction `Please take me to a room with a chair and a door.` and
`gemma4:e2b-it-qat`.

The run successfully completed LLM1 parsing and RAG grounding. Grounding
returned two candidate rooms, as expected for deferred Nav2 selection. The
LLM2 task-plan request then timed out after 360 s. Mission preparation and
Nav2 path planning were therefore not attempted. No `NavigateToPose` action
was sent; `execution_performed` is false. This must not be reported as an
end-to-end success.

The complete machine-readable result is
`results/smoke-gemma4-e2b-llm2-timeout.json`. It records the exact instruction,
model digest, source hashes, map/RAG provenance, stage timing, and LLM failure.
That diagnostic run used the earlier v3 RAG artifact; the current default is
v5, rebuilt with the same 6,297 documents and additionally protected by a
logical content digest over documents, metadata, and stored vectors.
The intermediate v4 build is not for use: it omitted 18 alias documents because
the RAG CLI was launched without its config path; v5 restores all 6,297 docs.

A second run used the current v5 index and the default `grounded_deterministic`
task-chain backend (so it would not call LLM2), but LLM1 itself timed out at
360 s before RAG grounding. At that time WSL reported the GPU fully utilized;
a Windows GPU-engine sample immediately after the run attributed about 91–93%
of the 3D engine to the running Steam `FindTheNeedle.exe` process. This strongly
suggests GPU contention, but does not prove that it was the only cause. The
captured record is `results/plan-room-chair-door.json`. Please close that GPU
workload yourself before the next live-model retry; this process is not stopped
automatically.

After the GPU became idle, the retry completed the full software path:

- LLM1 intent parsing: 8.82 s.
- RAG grounding: 0.62 s; the phrase matched room-node candidates
  `5908722711110090758` (F3) and `5908722711110090764` (F1).
- The deterministic adapter preserved both candidates without choosing one.
- Nav2 selected `5908722711110090764` on F1 by feasible route evaluation and
  planned a 28.147 m route from the configured F0 start, including one
  abstract floor transition; Nav2 planning took 4.30 s. Candidate evaluation
  restored the configured initial map/pose before returning `planned`.
- Final status: `planned`; `execution_performed: false`. This is a plan-only
  software result, not robot motion, not RViz recording, and not a benchmark.

The successful machine-readable result is
`results/plan-room-chair-door-gpu-idle-retry.json`.

After converting the default floor bundle to workspace-relative map paths, a
second portability check also returned `planned` with the same selected F1
target, path length, and transition count. Its new bundle hash is recorded in
`results/plan-room-chair-door-commit-ready-check.json`; this confirms the
relative-path change is exercised by the actual Nav2 route planner, not just a
static unit test.

After packaging the four Khronos-only map assets with sanitized per-floor
reports, the same route was replanned successfully from those package assets.
The result is `results/plan-room-chair-door-packaged-maps.json` and records the
final map bundle hash. The WSL stack was stopped after the check.

To retry the integrated path after starting the dedicated Hospital Nav2 stack
on domain 77:

```bash
cd /path/to/Masterthesis-wang
bash scripts/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --num-ctx 4096 \
  --num-predict 512 \
  --request-timeout-s 360 \
  --output /tmp/hospital-nav2-natural-language-plan-retry.json
```

The command will not overwrite an existing output. A successful `planned`
result means Nav2 computed paths for all ordered stops; it does not execute
motion. Plan-only evaluation can still reload maps and publish `/initialpose`,
so use only an isolated local simulation ROS domain.
