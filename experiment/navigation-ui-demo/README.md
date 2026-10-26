# Hospital Navigation GUI Demo

This is an optional, self-contained demonstration module for the thesis repository. It connects the natural-language planner to a selected Hospital P1000 RAG build, resolves destinations against the scene graph, compiles a navigation mission, and can display point-robot Nav2 execution in RViz2.

## Included RAG Profiles

| GUI option | Build ID | Semantic review |
| --- | --- | --- |
| Human-reviewed RAG (v5) | 2415cc812fb6714ad41b23d765b5896b5cad7e35e09b612388794f8d99e1572a | Applied; review CSV SHA-256 29baf76d7bd2cda59b5e86e3a07fceb9411716cd59ba1e780b3b9808fd2ca6d5 |
| Fresh RAG (no manual labels) | aa9d8aa9c4bc745718a0878d15ce78d8f92400d1c934a22fbf450d0725838354 | No semantic-review CSV |

Both bundled indexes use the same Hospital DSG SHA-256 a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d and the pinned paraphrase-multilingual-MiniLM-L12-v2 embedding revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42. Each profile carries its own defaults and provenance manifest. The human-reviewed profile is selected by default.

## Automatic RAG Discovery

The GUI scans for newly generated RAG builds every 15 seconds and has a Refresh RAG list button. By default it searches:

- The repository experiment directory.
- Run-like folders in the WSL home directory.
- The Hospital RAG outputs directory under ~/Masterthesis-wang.
- Additional roots listed in HOSPITAL_RAG_DISCOVERY_ROOTS.

Set extra WSL paths as a colon-separated list before launch, for example:

    export HOSPITAL_RAG_DISCOVERY_ROOTS="$HOME/hospital-runs:/mnt/d/hospital-runs"

A generated build is offered only when it has a vector_db directory, build manifest, canonical graph, and a neighboring nav2-profile directory containing hospital_nav2_plan_defaults.yaml, hospital_floor_transitions.yaml, and run_profile_manifest.json. The run manifest must match the RAG build, DSG, map bundle, and defaults hashes. Incomplete or mismatched runs are skipped. Full Chroma content verification is performed before planning.

## Requirements

- WSL2 with Ubuntu 24.04.
- ROS 2 Jazzy.
- The khronos-nav2-bridge package built in this checkout.
- Ollama with gemma4:e2b-it-qat available locally.
- A Python environment with the dependencies in llm-rag/requirements.txt.

The launcher sources ROS Jazzy and the bridge workspace. It selects a Python environment with the required imports from the repository .venv, the standard Miniconda location, or PATH. Set HOSPITAL_UI_PYTHON to use a specific interpreter.

## Launch

From the repository root in WSL:

    bash experiment/navigation-ui-demo/run_demo.sh

The GUI opens at http://127.0.0.1:8765. Enter an English navigation request, choose a RAG profile, and press Plan Route. If grounding returns multiple candidates, select a destination and confirm. The left column displays LLM1 intent, RAG grounding, task chain, mission, Nav2 route, and RViz execution output. The progress column displays application-level status messages.

Changing the RAG clears the current plan and stage outputs. The Stop Simulation button stops the GUI-owned Nav2/RViz processes and clears the displayed plan outputs.

## Maps and Simulation

The demo uses the four-floor Khronos-only maps in khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only and a matching transition bundle. Map paths are workspace-relative and each map YAML/PGM is checked against its recorded SHA-256.

RViz2 runs an isolated point-robot kinematic simulation on ROS_DOMAIN_ID 79. The Nav2 profile sets a 0.5 m/s desired linear speed; the controller may reduce actual speed around turns and goals. This is software demonstration only. It does not control a physical robot, validate ANYmal hardware, or demonstrate physical stair/elevator traversal.

The GUI writes per-session planning and demo JSON files under ~/.cache/hospital-nav2-ui.
