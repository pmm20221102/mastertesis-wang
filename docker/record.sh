#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: bash docker/record.sh /data/recordings/demo.mp4 [duration_seconds]" >&2
    exit 2
fi
duration_args=()
if [[ $# == 2 ]]; then
    duration_args=(-t "$2")
fi
mkdir -p "$(dirname "$1")"
exec ffmpeg -n -f x11grab -video_size 1600x1000 -framerate 25 \
    -i :0 "${duration_args[@]}" -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p "$1"
