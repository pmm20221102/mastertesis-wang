# llm-rag 构建环境与依赖

本文档记录 Python/模型侧依赖的来源。ROS 2 系统依赖分别由 Khronos 和 Nav2 package.xml 声明，不要把 ROS apt 包改写成 pip 依赖。

## 已验证环境

| 项目 | 版本/来源 |
|---|---|
| OS | WSL 2、Ubuntu 24.04 |
| Python | 3.12；planner 的 pyproject 声明 requires-python >=3.10 |
| ROS | ROS 2 Jazzy |
| Python 直接依赖 | `llm-rag/requirements.txt`，固定当前验证环境中的版本 |
| ROS 依赖 | 各 ROS package 的 `package.xml`，通过 `rosdep` 安装 |
| 本地 LLM | Ollama，默认 gemma4:e2b-it-qat |
| Embedding | paraphrase-multilingual-MiniLM-L12-v2，revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42，CPU |

`llm-rag/requirements.txt` 是 Python 运行和测试的安装清单，固定直接依赖版本并指定 CPU 版 PyTorch，避免安装 CUDA 运行时。它不锁定所有传递依赖；`llm-rag/planner/pyproject.toml` 保留包元数据和版本下限。不要使用 `pip install -e 'llm-rag/planner[dev]'`：当前 Setuptools 自动包发现会因多个顶层包而失败。本项目从仓库源码路径运行脚本，无需 editable install。

这些固定版本取自当前已通过 Python 测试的 Python 3.12 环境；CPU PyTorch 发行包的安装命令/索引已按 PyTorch 官方安装页选择，但这份 requirements 集合尚未在全新虚拟环境整体安装验证。请在干净环境运行下方测试命令并记录任何解析或兼容问题。

## 安装顺序

从仓库根目录执行：

~~~bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r llm-rag/requirements.txt
ollama pull gemma4:e2b-it-qat
~~~

确认 Ollama 服务已启动：

~~~bash
ollama list
curl -s http://127.0.0.1:11434/api/tags
~~~

第一次 RAG 构建可能下载 pinned embedding model；构建流程将 embedding-device 固定为 cpu，避免与 Ollama 的 GPU 推理争用资源。

## ROS 2 系统依赖

先按 [ROS 2 Jazzy Ubuntu 安装指南](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html) 安装 ROS 2 Desktop 与 ros-dev-tools，再从仓库根目录运行：

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep update
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
~~~

如果要从原始传感器/数据集重新构建 Khronos，而不是使用已有 DSG，应额外对 khronos/scene-graph-khronos/src 执行 rosdep，并按 khronos/README.md 编译 upstream workspace。

## 外部输入不随仓库提供

- Hospital P1000 原始 DSG、mesh 和人工审阅语义 CSV（原始数据约 27 GB）。
- Ollama 模型权重与 embedding 模型缓存。
- RAG 输出 vector_db 是按 build manifest 重建的运行产物；历史任务链归档中包含其运行快照，但不应把它误认为当前 builder 输出。
- Khronos 与 TartanGround 的原始数据集、校准文件和传感器 bag。

没有这些输入时仍可运行单元测试、加载归档 mission/结果、检查已打包地图；不能从零重建论文 RAG index 或重跑完整感知流程。
