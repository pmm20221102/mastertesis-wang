# Docker on a remote workstation

The Compose setup runs the Hospital demo, Nav2 and RViz2 on the workstation.
The client needs SSH and a browser. The default profile is Human-reviewed RAG
(v5), containing the 78 manually supplemented object labels.

Install Docker Engine, the Compose plugin and NVIDIA Container Toolkit on the
workstation. Verify the GPU with a CUDA container before starting.

From a clone of thesis-final on the workstation:

    docker compose build
    docker compose up -d ollama
    docker compose exec ollama ollama pull gemma4:e2b-it-qat
    docker compose up -d demo
    docker compose logs -f demo

On the client, keep an SSH tunnel open:

    ssh -L 8765:127.0.0.1:8765 -L 6080:127.0.0.1:6080 USER@WORKSTATION

For KIT, connect the KIT VPN first and use your own workstation account in
the SSH command. Leave the tunnel terminal open while using the demo.

Open http://127.0.0.1:8765 for planning and
http://127.0.0.1:6080/vnc.html for the RViz2 desktop. Use the demo's navigation
button after planning. The selected Room labels, maps and navigation results
remain visible on the remote desktop.

To stop services, run docker compose down. Named volumes retain downloaded
models and session outputs. To inspect GPU use, run docker compose exec ollama ollama ps after a
planning request. Session JSON and Nav2 logs are in the cache volume under
/root/.cache/hospital-nav2-ui.

A replacement RAG needs its matching maps and
run profile, as described in the demo README.

The full image compiles Khronos and the sensor adapter and includes the
RAG builder, map generator, Ollama client, Nav2, RViz2 and recording tools.
## Complete environment and new reconstruction

Compose builds the full target, including the Khronos C++ workspace and the
TartanGround ROS adapter. The bundled reviewed Hospital demo and the new-scene
workflow share this image.

For extracted Hospital P1000 input data, set HOSPITAL_DATASET_ROOT on the host
before starting Compose. It is mounted read-only at /input. The host directory
docker-data is mounted at /data for bags, reconstructions, RAG builds, maps and
recordings.

    export HOSPITAL_DATASET_ROOT=/absolute/path/to/extracted/P1000
    docker compose build
    docker compose up -d
    docker compose exec demo bash

Run the complete reconstruction workflow from the host:

    docker compose exec demo bash docker/reconstruct.sh /data/my-hospital-run

The wrapper converts the 3178-frame input to a bag, launches Khronos in a
separate ROS domain, requests final saving and shuts down the mapper process
group. It then builds RAG from the exported DSG, generates four maps from
that same DSG and mesh, and packages a matching Nav2 profile. Each stage
writes its log under the chosen run directory.

The wrapper creates a mapper configuration with 0.10 m voxels,
0.005 m mesh compression resolution, periodic snapshots disabled and
save_full_state=false. These settings retain final DSG, Mesh and 4D map
outputs while avoiding the complete frontend/module dump that exhausted RAM
on the 62 GiB workstation. For manual launches, use the generated mapper.yaml
with mapper_config:=/data/my-hospital-run/mapper.yaml.

For a retry using an already converted bag:

    docker compose exec -e HOSPITAL_EXISTING_BAG=/data/my-hospital-run/bag \
      demo bash docker/reconstruct.sh /data/my-hospital-rerun

Always choose a new output directory. HOSPITAL_MAPPER_CONFIG can select a
different compatible source YAML and HOSPITAL_MESH_RESOLUTION can override
the mesh compression resolution. Effective settings are saved in mapper.yaml.

The GUI discovers the generated profile under /data. Select it to try the
new RAG and maps. The generated index uses reconstructed labels unless
review annotations for that graph are supplied. Human-reviewed RAG (v5)
is available for the original bundled Hospital demo.

For individual stage commands, see
[CODE_REPRODUCTION.md](../experiment/CODE_REPRODUCTION.md). Source
docker/activate.sh in a container shell and replace .venv/bin/python with
/opt/venv/bin/python. Keep source-specific RAG, maps and profiles together.

## Record the complete visualization

Open the RViz2 desktop through the SSH tunnel. From a second SSH terminal,
start recording before running the navigation:

    docker compose exec demo bash docker/record.sh /data/recordings/navigation.mp4

Stop recording with q after the route completes. For unattended recording,
append a duration in seconds, for example 90, to the recording command. FFmpeg finalizes the MP4 and
saves it on the host at docker-data/recordings/navigation.mp4. Each recording
captures the full 1600 by 1000 virtual desktop at 25 fps. Keep the corresponding
session result JSON alongside the video.

## Environment readiness

Startup prints component checks to docker compose logs demo. Open
http://127.0.0.1:6080/environment.html through the SSH tunnel to see the same
checks as a table. The machine-readable result is saved to
/data/environment-check.json. GPU execution is verified with ollama ps after
submitting a planning request. A model present in the model list is checked
separately from its active GPU allocation.

A recorded navigation check completed successfully in 42.837 seconds. The
resulting MP4 was verified as H.264, 1600 by 1000 pixels and 90 seconds long.

The reconstruction wrapper defaults to mapper_voxel010_fixed.yaml with 0.10 m
voxels and periodic full-state snapshots disabled. HOSPITAL_MAPPER_CONFIG can
select another mapper YAML. The effective configuration is written to the
run directory. Set HOSPITAL_EXISTING_BAG to reuse a previously converted bag.

The wrapper sets save_full_state to false and disables periodic snapshots.
It still exports the final DSG, mesh and 4D map. The final-save call now passes
the configured save_full_state value to the pipeline, matching the setting
already used for intermediate saves. The previous final-save call always
requested the complete frontend/module dump and exhausted workstation RAM
after writing the core scene files.

The default mesh compression resolution stays at 0.005 m. Set
HOSPITAL_MESH_RESOLUTION to explicitly change it. Build concurrency defaults
to two workers; a workstation can select four with
docker compose build --build-arg BUILD_JOBS=4.

A fresh reconstruction produced 3115 graph nodes and 6231 RAG documents. The
new four-floor maps and profile passed endpoint checks. Selecting this generated
build in the demo grounded the bed instruction and completed a cross-floor
navigation mission from F0 to F2 in 90.517 seconds. This build uses its own
reconstructed semantic labels and does not reuse the reviewed v5 overrides.
Use a new /data output directory for every reconstruction attempt.

The checked reconstruction script then completed all stages continuously in
/data/full-workflow-final, including normal final saving, RAG generation,
four floor maps and a profile with 12 validated portal endpoints. A dedicated
process group keeps the mapper and its child processes together during
shutdown. Compose enables an init process to reap completed child processes.

Release-image checks completed both paths. The bundled reviewed-v5 request
"Take me to a room with a metal medical table." succeeded in 42.894 seconds.
For the full-workflow-final generated profile, HC-001 ("Please take me into
the room with the bed.") succeeded in 119.058 seconds. These durations are
single navigation executions on their respective maps. The test instructions
were supplied independently of the generated scene.
