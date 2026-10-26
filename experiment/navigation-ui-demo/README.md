# Hospital Navigation GUI Demo

This standalone demo module connects the natural-language planner to either of two packaged Hospital P1000 RAG builds. Select the active RAG in the GUI before submitting a navigation request.

## Included RAG Profiles

| GUI option | Build ID | Semantic review |
| --- | --- | --- |
| Human-reviewed RAG (v5) | 2415cc812fb6714ad41b23d765b5896b5cad7e35e09b612388794f8d99e1572a | Applied; review CSV SHA-256 29baf76d7bd2cda59b5e86e3a07fceb9411716cd59ba1e780b3b9808fd2ca6d5 |
| Fresh RAG (no manual labels) | aa9d8aa9c4bc745718a0878d15ce78d8f92400d1c934a22fbf450d0725838354 | No semantic-review CSV |

Both indexes use the same source DSG (a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d) and pinned paraphrase-multilingual-MiniLM-L12-v2 embedding revision. Each index is paired with a matching defaults file and validated against the same map bundle at startup.

## Requirements

- WSL2 with Ubuntu 24.04
- ROS 2 Jazzy
- A built khronos-nav2-bridge
- Ollama with gemma4:e2b-it-qat
- The repository Python environment and dependencies from llm-rag/BUILD_ENVIRONMENT.md

## Launch

From the repository root in WSL:

    bash experiment/navigation-ui-demo/run_demo.sh

The script opens the local GUI at http://127.0.0.1:8765. It selects a Python environment with the required dependencies from the project virtual environment, the standard Miniconda location, or PATH. Set HOSPITAL_UI_PYTHON to override the selection.

Choose **Human-reviewed RAG (v5)** to use the manually supplemented labels, or **Fresh RAG (no manual labels)** to compare against the unreviewed build. Switching indexes clears the previous plan and stage outputs. The GUI validates each index against its source manifest and matching defaults before making it selectable.

## Maps and Simulation

The demo uses the four-floor Khronos-only maps in khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only and a profile with ROS_DOMAIN_ID=79. RViz2 runs the point-robot kinematic simulation with a desired linear speed of 0.5 m/s. This is software-only demonstration; it does not control a physical robot or validate ANYmal hardware safety.

The map bundle stores workspace-relative paths and verifies every map YAML/PGM against its recorded SHA-256. The RAG manifests preserve the semantic-review provenance for both profiles.
