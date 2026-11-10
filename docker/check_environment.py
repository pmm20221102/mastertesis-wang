"""Report the container environment in terminal logs and a browser status page."""
import html
import importlib
import json
from pathlib import Path
import shutil
import time
import urllib.request
import os

ROOT = Path("/opt/thesis")
checks = []
for name in ("yaml", "chromadb", "ollama", "sentence_transformers"):
    try:
        importlib.import_module(name)
        checks.append((name, True, "Python import available"))
    except Exception as error:
        checks.append((name, False, str(error)))
for name, path in (
    ("Khronos", ROOT / "khronos/scene-graph-khronos/install/khronos_ros/lib/khronos_ros/khronos_node"),
    ("Data adapter", ROOT / "khronos/tartanground-tools/install/tartanground_khronos_bridge"),
    ("Nav2 bridge", ROOT / "khronos-nav2-bridge/install/setup.bash"),
):
    checks.append((name, path.exists(), str(path)))
for name in ("ffmpeg", "rviz2", "Xvfb", "x11vnc"):
    checks.append((name, shutil.which(name, path=os.environ.get("PATH", "") + ":/opt/ros/jazzy/bin") is not None, "Executable lookup"))
url = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
error = "Service unavailable"
for attempt in range(60):
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=3) as response:
            models = json.load(response).get("models", [])
        found = any(model.get("name") == "gemma4:e2b-it-qat" for model in models)
        checks.append(("Gemma E2B", found, "Model installed" if found else "Run docker compose exec ollama ollama pull gemma4:e2b-it-qat"))
        break
    except Exception as exc:
        error = str(exc)
        time.sleep(1)
else:
    checks.append(("Ollama", False, error))
try:
    with urllib.request.urlopen(url + "/api/ps", timeout=3) as response:
        active_models = json.load(response).get("models", [])
    if active_models:
        on_gpu = any(model.get("size_vram", 0) > 0 for model in active_models)
        checks.append(("GPU inference", on_gpu, "GPU memory allocated" if on_gpu else "Loaded models are using CPU"))
    else:
        checks.append(("GPU inference", None, "Submit a planning request, then rerun this check"))
except Exception as error:
    checks.append(("GPU inference", False, str(error)))
ready = all(ok is not False for _, ok, _ in checks)
rows = []
for name, ok, detail in checks:
    status = "PENDING" if ok is None else ("OK" if ok else "CHECK REQUIRED")
    print(f"[{status}] {name} - {detail}", flush=True)
    rows.append(f"<tr><td>{html.escape(name)}</td><td>{status}</td><td>{html.escape(detail)}</td></tr>")
title = "Environment ready" if ready else "Environment checks need attention"
print(title + ". Demo: port 8765. RViz2 desktop: port 6080/vnc.html.", flush=True)
page = "<!doctype html><html lang=en><meta charset=utf-8><title>Environment checks</title><body style='font:18px sans-serif;margin:40px'><h1>" + title + "</h1><table border=1 cellpadding=10><tr><th>Component</th><th>Status</th><th>Details</th></tr>" + "".join(rows) + "</table><p><a href='vnc.html'>Open RViz2 desktop</a></p></body></html>"
Path("/usr/share/novnc/environment.html").write_text(page)
Path("/data").mkdir(exist_ok=True)
Path("/data/environment-check.json").write_text(json.dumps({"ready": ready, "checks": checks}, indent=2))
