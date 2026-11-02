# LLM-RAG Build Environment and Dependencies

This document identifies Python/model dependencies and system requirements. ROS 2 dependencies are declared separately in the Khronos and Nav2 `package.xml` files; do not convert ROS apt packages into pip dependencies.

## Validated Environment

| Component | Version/source |
|---|---|
| OS | WSL 2 / Ubuntu 24.04 |
| Python | 3.12; planner `pyproject.toml` declares `requires-python >=3.10` |
| ROS | ROS 2 Jazzy |
| Python direct dependencies | `llm-rag/requirements.txt`; pins versions from the validated environment |
| ROS dependencies | Declared in each ROS package's `package.xml` and installed with `rosdep` |
| Local LLM | Ollama; default model `gemma4:e2b-it-qat` |
| Embedding | `paraphrase-multilingual-MiniLM-L12-v2`, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, CPU |

`llm-rag/requirements.txt` is the installation manifest for Python runtime and tests. It pins direct dependency versions and selects CPU PyTorch to avoid installing CUDA runtime packages. It does not lock every transitive dependency; `llm-rag/planner/pyproject.toml` retains package metadata and minimum-version declarations. Do not use `pip install -e 'llm-rag/planner[dev]'`: Setuptools automatic package discovery currently fails because it finds multiple top-level packages. The project runs scripts from the repository source tree and does not require an editable install.

The complete requirements set was installed into a fresh Python 3.12 virtual environment during the current reproduction check; `pip check` passed. CPU PyTorch comes from the configured official wheel index. Run the software tests after installation and retain the environment/package inventory with the run.

## Installation Order

Run from the repository root:

Create the Python environment and install the local Ollama server using the [official Linux installation guide](https://docs.ollama.com/linux):

~~~bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r llm-rag/requirements.txt
curl -fsSL https://ollama.com/install.sh | sh
~~~

The official installer creates a systemd service when systemd is running. Enable and start it, then download the model:

~~~bash
sudo systemctl enable --now ollama
systemctl is-active ollama
ollama pull gemma4:e2b-it-qat
~~~

If the machine has no running systemd, start the server in a dedicated terminal and leave that process running:

~~~bash
ollama serve
~~~

In another terminal, check the installed CLI, model, and API:

~~~bash
ollama --version
ollama list
curl -fsS http://127.0.0.1:11434/api/tags
~~~

The first RAG build may download the pinned embedding model. The build uses `embedding-device=cpu` to avoid competing with Ollama for GPU resources.

## ROS 2 System Dependencies

First install ROS 2 Desktop and `ros-dev-tools` using the [ROS 2 Jazzy Ubuntu installation guide](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html), then run from the repository root:

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi
rosdep update
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
~~~

To rebuild Khronos from raw sensors/datasets rather than use an existing DSG, also run rosdep on `khronos/scene-graph-khronos/src` and build the upstream workspace as described in `khronos/README.md`.

## External Inputs Not Included in the Repository

- Raw Hospital P1000 sensor data, original DSG, and meshes for a new reconstruction. The short demo already includes its databases and maps; it needs none of these external inputs.
- Ollama model weights and the embedding-model cache.
- The RAG `vector_db` is a generated artifact rebuilt from its build manifest. Historical task-chain archives contain runtime snapshots; these are not outputs from the current builder.
- Original Khronos and TartanGround datasets, calibration files, and sensor bags.

Without raw scene inputs, use the bundled GUI and archived missions after common runtime setup. New reconstruction requires the scene data. The committed semantic_overrides.json preserves the human-review labels for its original DSG; it does not apply to a different graph by assumption.
