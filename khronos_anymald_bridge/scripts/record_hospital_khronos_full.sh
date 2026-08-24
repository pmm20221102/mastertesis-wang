#!/usr/bin/env bash
set -euo pipefail

workspace=/workspace
project="$workspace/tartanground_khronos"
khronos="$workspace/src/khronos"
bag="$project/bags/hospital_p2000_full"
output="$project/reports/videos/hospital_khronos_dsg_full_317s.mp4"
duration=323

usage() {
  echo "Usage: $0 [--bag DIR] [--duration SEC] [--output FILE]"
}

while (($#)); do
  case "$1" in
    --bag) bag=$2; shift 2 ;;
    --duration) duration=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

source_launch="$project/tools/tartanground_khronos_bridge/launch/tartanground_hospital_khronos.launch.yaml"
source_rviz="$khronos/khronos_ros/config/rviz/uHumans2.rviz"
source_mapper="$project/tools/tartanground_khronos_bridge/config/mapper_voxel010_fixed.yaml"
test -s "$bag/metadata.yaml"
test -s "$source_launch"
test -s "$source_rviz"
test -s "$source_mapper"
mkdir -p "$(dirname "$output")"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="/tmp/hospital-khronos-record-$stamp"
mkdir -p "$run_dir"
launch_file="$run_dir/hospital_khronos.launch.yaml"
rviz_file="$run_dir/hospital_dsg.rviz"
mapper_file="$run_dir/mapper_voxel015_video.yaml"
visualizer_plugins_file="$run_dir/visualizer_plugins.yaml"
run_output="$project/reports/khronos_output/video_full_$stamp"
mkdir -p "$run_output"

# The original launch that produced the accepted 120 s video was authored for
# the host checkout. The container mounts the same project at /workspace.
sed 's#/home/ulmmt/Masterthesis-wang#/workspace#g' "$source_launch" > "$launch_file"
cp "$source_rviz" "$rviz_file"
cp "$source_mapper" "$mapper_file"
cp "$khronos/install/khronos_ros/share/khronos_ros/config/visualization/visualizer_plugins.yaml" \
  "$visualizer_plugins_file"
# ponytail: 15 cm is the existing project's coarser mapping resolution; it keeps
# two live DSG/mesh copies below RAM for the full bag. Use 10 cm for offline maps.
sed -i \
  -e 's/voxel_size: 0\.10 # m/voxel_size: 0.15 # m/' \
  -e 's/truncation_distance: 0\.2 # m/truncation_distance: 0.3 # m/' \
  "$mapper_file"
grep -q 'voxel_size: 0.15 # m' "$mapper_file"
grep -q 'truncation_distance: 0.3 # m' "$mapper_file"
sed -i \
  -e 's#/tesse/depth_cam/mono/image_raw#/tartanground/front/depth/image_raw#g' \
  -e 's#/tesse/seg_cam/rgb/image_raw#/tartanground/front/semantic/image_raw#g' \
  -e 's#/tesse/left_cam/rgb/image_raw#/tartanground/front/rgb/image_raw#g' \
  -e 's/Background Color: 255; 255; 255/Background Color: 30; 30; 30/' \
  -e 's/Y: 18\.520326614379883/Y: -10.5/' \
  -e 's/  Height: 1131/  Height: 1000/' \
  -e 's/  Width: 1920/  Width: 1600/' \
  "$rviz_file"
sed -i "s#/tmp/hospital_dsg.rviz#$rviz_file#" "$launch_file"
sed -i "s#$source_mapper#$mapper_file#" "$launch_file"
# The default Khronos object publisher keeps 100 full mesh/marker messages,
# which exhausts 62 GiB near the end of this bag. RViz displays current state,
# so a one-message history is lossless for this recording and bounds memory.
sed -i '/type: KhronosObjectPlugin/a\    queue_size: 1' "$visualizer_plugins_file"
sed -i "/name: mapper_config, value:/a\        - {name: visualizer_plugins, value: $visualizer_plugins_file}" \
  "$launch_file"

set +u
. /opt/ros/jazzy/setup.bash
. "$khronos/install/setup.bash"
set -u

export DISPLAY=:98
export ROS_DOMAIN_ID=73
export XDG_RUNTIME_DIR="$run_dir/runtime"
export LIBGL_ALWAYS_SOFTWARE=1
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# kimera_pgmo_rviz was rebuilt against Conda yaml-cpp 0.9, while Jazzy only
# exports the system 0.8 path. Expose that one ABI dependency without putting
# every Conda library ahead of ROS/Qt.
runtime_lib="$run_dir/lib"
mkdir -p "$runtime_lib"
ln -s /opt/conda/lib/libyaml-cpp.so.0.9 "$runtime_lib/libyaml-cpp.so.0.9"
export LD_LIBRARY_PATH="$runtime_lib:${LD_LIBRARY_PATH:-}"

launch_log="$output.launch.log"
bag_log="$output.bag.log"
ffmpeg_log="$output.ffmpeg.log"
xvfb_log="$output.xvfb.log"
visualizer_restart_log="$output.visualizer-restart.log"
visualizer_pid_file="$run_dir/visualizer.pid"
visualizer_restart_status="$run_dir/visualizer-restart.status"
visualizer_exe="$khronos/install/hydra_visualizer/lib/hydra_visualizer/hydra_visualizer_node"

start_replacement_visualizer() {
  setsid "$visualizer_exe" \
    --config-utilities-file "$khronos/install/khronos_ros/share/khronos_ros/config/visualization/visualizer_config.yaml" \
    --config-utilities-file "$visualizer_plugins_file" \
    --config-utilities-yaml '{glog_level: 1, glog_verbosity: 0}' \
    --config-utilities-yaml '{graph: {type: GraphFromRos, frame_id: map}}' \
    --ros-args -r __node:=hydra_visualizer -r '~/dsg:=/khronos_node/backend/dsg' \
    >> "$visualizer_restart_log" 2>&1 &
  new_visualizer_pid=$!
  echo "$new_visualizer_pid" > "$visualizer_pid_file"
  sleep 10
  kill -0 "$new_visualizer_pid"
}

restart_visualizer() {
  # Rebuild the subscriber-side graph after fragmentation starts to
  # dominate. RViz retains the last mesh/markers while the new process catches up.
  sleep 170
  kill -0 "$initial_visualizer_pid"
  kill -TERM "$initial_visualizer_pid"
  for _ in {1..50}; do
    kill -0 "$initial_visualizer_pid" 2>/dev/null || break
    sleep 0.1
  done
  ! kill -0 "$initial_visualizer_pid" 2>/dev/null

  echo "restart at bag +170 s" > "$visualizer_restart_log"
  start_replacement_visualizer

  # The full hospital graph needs one more reset near the end. This process is
  # our child, so wait also reaps it instead of leaving a zombie.
  sleep 80
  kill -TERM -- "-$new_visualizer_pid"
  wait "$new_visualizer_pid" || true
  echo "restart at bag +260 s" >> "$visualizer_restart_log"
  start_replacement_visualizer
  echo ok > "$visualizer_restart_status"
}

cleanup() {
  trap - EXIT INT TERM
  set +e
  [[ -n ${visualizer_restart_pid:-} ]] && kill "$visualizer_restart_pid" 2>/dev/null
  if [[ -s $visualizer_pid_file ]]; then
    new_visualizer_pid=$(<"$visualizer_pid_file")
    kill -TERM -- "-$new_visualizer_pid" 2>/dev/null
  fi
  for item in "${bag_pid:-}" "${ffmpeg_pid:-}"; do
    [[ -n "$item" ]] && kill -TERM -- "-$item" 2>/dev/null
  done
  if [[ -n ${launch_pid:-} ]]; then
    kill -INT -- "-$launch_pid" 2>/dev/null
    for _ in {1..30}; do
      kill -0 "$launch_pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -TERM -- "-$launch_pid" 2>/dev/null
  fi
  [[ -n ${xvfb_pid:-} ]] && kill "$xvfb_pid" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

Xvfb "$DISPLAY" -screen 0 1600x1000x24 -nolisten tcp > "$xvfb_log" 2>&1 &
xvfb_pid=$!
sleep 2

setsid ros2 launch "$launch_file" \
  output_dir:="$run_output" start_visualizer:=true \
  > "$launch_log" 2>&1 &
launch_pid=$!

# Match the known-good 120 s run: let Khronos, visualizer, RViz and TF settle
# before publishing the first bag frame.
sleep 25
mapfile -t visualizer_pids < <(pgrep -f "^$visualizer_exe ")
if ((${#visualizer_pids[@]} != 1)); then
  echo "Expected one Hydra visualizer, found ${#visualizer_pids[@]}" >&2
  exit 1
fi
initial_visualizer_pid=${visualizer_pids[0]}
setsid ffmpeg -y -nostdin -loglevel info -f x11grab -framerate 10 \
  -video_size 1600x1000 -i "${DISPLAY}.0" -t "$duration" \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p "$output" \
  > "$ffmpeg_log" 2>&1 &
ffmpeg_pid=$!
sleep 2
setsid ros2 bag play "$bag" --clock --rate 1.0 > "$bag_log" 2>&1 &
bag_pid=$!
restart_visualizer &
visualizer_restart_pid=$!

wait "$bag_pid"
bag_pid=
wait "$ffmpeg_pid"
ffmpeg_pid=
wait "$visualizer_restart_pid"
visualizer_restart_pid=
test -s "$output"
test "$(<"$visualizer_restart_status")" = ok
new_visualizer_pid=$(<"$visualizer_pid_file")
kill -0 "$new_visualizer_pid"
[[ $(ps -o stat= -p "$new_visualizer_pid") != *Z* ]]
visualizer_deaths=$(grep -Ec '\[ERROR\].*hydra_visualizer_node.*process has died' "$launch_log" || true)
test "$visualizer_deaths" -eq 1
if grep -Eq 'Check failed|\[ERROR\].*khronos_node.*process has died' "$launch_log"; then
  echo "Khronos/RViz2 recording failed; inspect $launch_log" >&2
  exit 1
fi

echo "Video:  $output"
echo "Launch: $launch_log"
echo "Bag:    $bag_log"
echo "FFmpeg: $ffmpeg_log"
