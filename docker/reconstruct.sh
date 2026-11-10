#!/usr/bin/env bash
set -eo pipefail
cd /opt/thesis
source docker/activate.sh
export ROS_DOMAIN_ID=80
if [[ $# != 1 || "$1" != /data/* ]]; then
    echo "Usage: bash docker/reconstruct.sh /data/new-run-directory" >&2
    exit 2
fi
run_dir="$1"
HOSPITAL_EXISTING_BAG="${HOSPITAL_EXISTING_BAG:-}"
mkdir -p "$(dirname "$run_dir")"
mkdir "$run_dir"
if [[ -n "$HOSPITAL_EXISTING_BAG" ]]; then
    bag="$HOSPITAL_EXISTING_BAG"
else
    bag="$run_dir/bag"
    if [[ -e "$bag" ]]; then
        echo "Use a new output directory or set HOSPITAL_EXISTING_BAG." >&2
        exit 2
    fi
    ros2 run tartanground_khronos_bridge tartanground_to_ros2bag \
        --dataset-root /input --camera lcam_front --start-frame 0 \
        --num-frames 3178 --fps 10 --output "$bag" \
        2>&1 | tee "$run_dir/bag-conversion.log"
fi
/opt/venv/bin/python - "$run_dir/mapper.yaml" <<'PY'
from pathlib import Path
import sys
import yaml
import os
source = Path(os.environ.get("HOSPITAL_MAPPER_CONFIG", "/opt/thesis/khronos/tartanground-tools/tools/tartanground_khronos_bridge/config/mapper_voxel010_fixed.yaml"))
config = yaml.safe_load(source.read_text())
config["experiment"]["save_every_n_frames"] = 0
config["experiment"]["save_full_state"] = False
config["frontend"]["pgmo"]["mesh_resolution"] = float(os.environ.get("HOSPITAL_MESH_RESOLUTION", "0.005"))

Path(sys.argv[1]).write_text(yaml.safe_dump(config))
PY
mkdir -p "$run_dir/mapper"
cd "$run_dir/mapper"
setsid ros2 launch tartanground_khronos_bridge tartanground_hospital_khronos.launch.yaml \
    use_gt_semantics:=true mapper_config:="$run_dir/mapper.yaml" output_dir:="$run_dir/mapper/output" \
    start_visualizer:=false >"$run_dir/mapper.log" 2>&1 &
mapper_pid=$!
trap 'kill -TERM -- "-$mapper_pid" 2>/dev/null || true' EXIT
sleep 5
kill -0 "$mapper_pid"
ros2 bag play "$bag" --clock --rate 1.0 2>&1 | tee "$run_dir/playback.log"
timeout 180 ros2 service call /finish_mapping_and_save std_srvs/srv/Empty '{}' \
    2>&1 | tee "$run_dir/save.log"
kill -TERM -- "-$mapper_pid"
wait "$mapper_pid" || true
trap - EXIT
cd /opt/thesis
/opt/venv/bin/python - "$run_dir" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
graphs = sorted((root / "mapper/output").rglob("dsg.json"))
valid = [p for p in graphs if p.stat().st_size and p.with_name("mesh.ply").is_file()]
if len(valid) != 1:
    raise RuntimeError(f"Expected one final DSG/mesh pair, found {len(valid)}")
(root / "source-path.txt").write_text(str(valid[0].parent) + "\n")
PY
source_dir="$(cat "$run_dir/source-path.txt")"
/opt/venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
    --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
    --reproducible --dataset hospital-p1000 --label-space hospital109 \
    --input "$source_dir/dsg.json" \
    --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
    --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
    --embedding-device cpu --output "$run_dir/rag" \
    2>&1 | tee "$run_dir/rag-build.log"
/opt/venv/bin/python experiment/map-generation/generate_khronos_only.py \
    --dsg "$source_dir/dsg.json" --mesh "$source_dir/mesh.ply" \
    --output "$run_dir/maps" --no-manual-topology \
    2>&1 | tee "$run_dir/map-build.log"
/opt/venv/bin/python experiment/map-generation/package_nav2_run.py \
    --maps-manifest "$run_dir/maps/manifest.json" --rag-build "$run_dir/rag" \
    --output "$run_dir/nav2-profile" 2>&1 | tee "$run_dir/profile-build.log"
echo "Reconstruction, RAG, maps and profile saved in $run_dir"
