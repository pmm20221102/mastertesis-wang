# Khronos Hospital Code/Log Investigation and Agent Execution Guide

> Purpose: Use the uploaded debug package to identify the true source of the “574 nodes / objects layer only” result before fixing the `dynamic_object_points` save issue.
>
> **Do not make further unverified edits to `backend.cpp`.**
>
> Key findings:
>
> 1. The older 1200-frame Hospital run is a confirmed success:
>    - 1980 nodes
>    - 9407 edges
>    - 33 objects
>    - 1781 places
>    - 14 rooms
>    - 1 building
> 2. In the current archive, `backend.cpp` and `backend.cpp.bak` have **identical SHA-256 hashes**.
> 3. The archived code does **not contain** `dynamic_object_points.clear()`.
> 4. Therefore:
>    > The claimed causal chain—“editing `backend.cpp` left only the objects layer”—is not supported by the archived code evidence.
> 5. The latest `voxel010_full.log` / `voxel010_run3.log` files are **a different type of failed run**:
>    - Configuration file paths do not exist.
>    - The node aborts with SIGABRT during initialization.
>    - Normal mapping never starts.
>    - This log is not evidence for the “574-node degraded run.”
> 6. The main problem is:
>    > **Different runs, executables, output directories, configurations, and DSG files have been conflated in the analysis.**
>
> Establish provenance (traceability of each run) before discussing a strip fix.

---

# 1. Confirmed Successful Baselines

## 1.1 Smoke 300 frames

Previous report:

```text
793 nodes
3571 edges
9 objects
727 places
9 rooms
1 building
```

Result:

```text
PASS
```

## 1.2 1200 frames / 120 s

Previous report:

```text
1980 nodes
9407 edges
33 objects
1781 places
14 rooms
1 building
```

Output:

```text
reports/khronos_output/2026_08_16-10_45_05/dsg.json
```

This is the most important current **known-good DSG baseline**.

Compare every subsequent output against it.

---

# 2. Key Code Facts in the Current Archive

Archived files:

```text
src_backend/backend.cpp
src_backend/backend.cpp.bak
```

SHA256：

```text
backend.cpp
6470b6075d9f83b1644e6eb8ab765260feaca4ce40812bb9a5ca8352d9a1e9a3

backend.cpp.bak
6470b6075d9f83b1644e6eb8ab765260feaca4ce40812bb9a5ca8352d9a1e9a3
```

The two files are byte-identical.

Also:

```bash
grep -R "dynamic_object_points.clear" src_backend
```

The search returns no matches.

The current `Backend::save()` implementation is:

```cpp
void Backend::save(const hydra::DataDirectory& log_setup) {
  const auto path = log_setup.path();
  unmerged_graph_->save(path / "shared_dsg.json", false);
  private_dsg_->graph->save(path / "dsg.json", false);

  const auto mesh = private_dsg_->graph->mesh();
  if (mesh && !mesh->empty()) {
    kimera_pgmo::WriteMesh(path / "mesh.ply", *mesh, *mesh);
  }

  deformation_graph_->save(path / "deformation_graph.dgrf");

  ...
}
```

Therefore:

> The archived source does not perform any lightweight strip.

Therefore, do not claim:

```text
backend strip modification
→ places/rooms disappeared
```

The current evidence does not support this causal claim.

---

# 3. Clearing `dynamic_object_points` Should Not Remove Place / Room / Building Layers

The proposed change resembles:

```cpp
for (const auto& [id, node] :
     private_dsg_->graph->getLayer(DsgLayers::OBJECTS).nodes()) {
  node->attributes<KhronosObjectAttributes>()
      .dynamic_object_points.clear();
}
```

This operation affects only:

```text
Layer 2 OBJECTS
```

the attribute field in that layer.

By itself, it does not:

```text
removeNode()
removeLayer()
clear PLACES
clear ROOMS
clear BUILDINGS
```

Therefore:

> If a new DSG truly contains only Layer 2, do not immediately attribute that to `dynamic_object_points.clear()`.

Find the actual cause of the missing Layers 3/4/5.

---

# 4. The Latest `voxel010_full.log` Is a Separate Startup Failure, Not a Degraded DSG Run

`voxel010_full.log` shows:

```text
ERROR File '.../tartanground_input.yaml' does not exist
ERROR File '.../mapper_tartanground_01.yaml' does not exist
ERROR File '.../tartanground_hospital_label_space.yaml' does not exist
SIGABRT
exit code -6
```

The root cause is clear:

> The container launch command uses host path `/home/ulmmt/...`, but those files do not exist at that path inside the container.

This run:

```text
did not initialize Khronos normally
did not process a bag
did not produce a valid mapping
```

Therefore:

**Do not associate `voxel010_full.log` with the 574-node degraded output unless the output directory and timestamp are proven to match.**

---

# 5. The Ianvs Warning Is Not the Current Primary Cause

The log also contains:

```text
ModuleNotFoundError:
No module named 'ianvs._ianvs_bindings'
```

This is a launch-extension warning.

The actual cause of the later launch abort is:

```text
config files do not exist
```

Current priority:

```text
Missing config path = fatal
ianvs warning = secondary
```

Do not start by fixing Ianvs.

---

# 6. Leading Suspect: The Wrong DSG File Was Analyzed

The alleged degraded output has:

```text
574 nodes
480 edges
1 layer
objects only
614 KB
```

These numbers are highly unusual.

In particular:

```text
574 object nodes
480 edges
```

Compared with the older data:

```text
33 objects
1781 places
14 rooms
```

This does not resemble:

```text
a complete DSG with only `dynamic_object_points` removed
```

It more closely resembles one of these cases:

1. An intermediate file containing only one layer was opened.
2. A frontend/private object graph was opened.
3. An incomplete save was opened.
4. The analysis script parsed the JSON schema incorrectly.
5. The output directory points to a different test run.
6. The new run did not load the complete mapper configuration.
7. The wrong executable/install overlay was used.

The first task is not to change the algorithm.

The first task is:

> **Locate the exact 574-node file.**

---

# 7. Agent Phase 1: Establish Degraded-Output Provenance

Locate the real file corresponding to:

```text
574 nodes / 480 edges / objects-only
```

Write the findings to:

```text
reports/degraded_output_provenance.md
```

The report must include:

```text
absolute path
file name
file size
mtime
sha256
run timestamp
output directory
log file
config.txt
experiment_log.txt
executable path
binary sha256
source backend.cpp sha256
```

Example:

```bash
DEGRADED_DSG=/absolute/path/dsg.json

stat "$DEGRADED_DSG"
sha256sum "$DEGRADED_DSG"
```

Find the parent directory:

```bash
dirname "$DEGRADED_DSG"
```

Then list its files:

```bash
find "$OUTPUT_DIR" -maxdepth 3 -type f -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | sort
```

---

# 8. Do Not Guess the Run Based on the “Most Recent File”

Do not run:

```bash
ls -t | head
```

and then conclude:

```text
“This must be the degraded run.”
```

Use these sources instead:

```text
timestamp
experiment_log
config.txt
shell log
output mtime
```

All four must agree with each other.

If they cannot be matched:

```text
RESULT=INCONCLUSIVE
```

Do not infer further.

---

# 9. Agent Phase 2: Inspect the Actual Layers in Every Candidate DSG

Count the layers in each of the following files separately:

## Known-Good Baseline

```text
2026_08_16-10_45_05/dsg.json
```

## Degraded Candidate

The actual 574-node file.

## Other Files from the Same Run

If present:

```text
shared_dsg.json
backend/dsg.json
frontend/dsg.json
dsg_with_mesh.json
maps/dsg_*.json
```

Inspect each file separately.

Output table:

```text
file
size
nodes
edges
layer2_objects
layer3_places
layer4_rooms
layer5_buildings
mesh_attached
```

Confirm:

> Which type of DSG contains the 574 nodes?

---

# 10. Do Not Trust Results from an Old Counting Script Without Verification

The agent must inspect the JSON layer keys first.

For example, in Python:

```python
import json

with open(path) as f:
    d = json.load(f)

print(d.keys())
```

Then print:

```text
layer IDs
layer names
node counts by layer
```

If the 5 GB JSON cannot be loaded directly:

use streaming, grep, or the `spark_dsg` API.

Do not trust an analysis-script result such as:

```text
layers = 1
```

without checking it.

First confirm that the script correctly parses the Khronos/Spark-DSG 1.1.3 schema.

---

# 11. Diff the Known-Good and Degraded Configurations

Find the corresponding run's:

```text
config.txt
```

Do not compare only hand-written YAML files.

Run:

```bash
diff -u \
  KNOWN_GOOD_OUTPUT/config.txt \
  DEGRADED_OUTPUT/config.txt \
  > reports/known_good_vs_degraded_config.diff
```

Check these items in particular:

```text
enable_rooms
freespace_places
frontend configuration
backend type
semantic_layers
room_finder
input
label space
semantic remap
save flags
```

Pay special attention:

The current config dump contains:

```text
surface_places [Uninitialized Virtual Config]
freespace_places [Uninitialized Virtual Config]
frontier_places [Uninitialized Virtual Config]
```

This is not necessarily an error because the specific place-generation configuration may be defined at the frontend-module level.

However, perform a **line-by-line diff** against the known-good `config.txt`.

Do not guess by visual inspection.

---

# 12. Record Executable Provenance for Every Run

This is especially important in the current investigation.

Multiple installations may exist on the same machine:

```text
/workspace/install
/workspace/src/khronos/install
other colcon installations
```

Therefore, record:

```bash
which ros2
ros2 pkg prefix khronos
ros2 pkg prefix khronos_ros
```

Also record:

```bash
readlink -f $(ros2 pkg prefix khronos_ros)/lib/khronos_ros/khronos_node
```

Then:

```bash
sha256sum /actual/path/khronos_node
```

Record:

```text
AMENT_PREFIX_PATH
CMAKE_PREFIX_PATH
LD_LIBRARY_PATH
```

Answer:

> Which exact build produced the 574-node run?

---

# 13. Verify That a `backend.cpp` Change Reaches the Executable

Do not simply:

```text
modify the source
colcon build
```

and assume that the run uses the new code.

Add a temporary, distinctive log message that appears once:

```cpp
CLOG(1) << "[HOSPITAL_LIGHT_SAVE_V1] Backend::save() build marker";
```

Rebuild.

Run 10–50 frames.

The log must contain:

```text
[HOSPITAL_LIGHT_SAVE_V1]
```

If it does not appear:

> The current run did not use the modified binary.

This establishes the build provenance required for the investigation.

Remove the marker after verification.

---

# 14. Do Not Destructively Strip the Original Graph

An earlier proposal was:

```cpp
private_dsg_->graph->...dynamic_object_points.clear();
```

Although this should not remove layers in theory, it is:

```text
mutating live backend graph during save
```

not sufficiently safe from an engineering perspective.

In particular, the following operations occur after saving:

```text
map_.update(private_dsg_->graph->clone())
map_.save(...)
maps/*.json
pipeline clone
```

Directly modifying the live graph would affect later:

```text
4D map
snapshot
other saved outputs
```

Do not directly clear the live DSG in the new fix.

---

# 15. Correct Lightweight-Save Design

Goal:

```text
preserve the complete live graph
↓
omit `dynamic_object_points` only during serialization
```

Priority:

## Preferred

Add the following option to the `KhronosObjectAttributes` JSON serializer:

```text
save_dynamic_object_points = false
```

Skip the field when producing lightweight output.

Advantages:

```text
does not copy 5 GB
does not modify the live graph
does not affect layers
does not affect the backend
```

## Second-Best Option

Construct lightweight attribute/node copies and copy only:

```text
id
semantic
position
bounding box
mesh summary
trajectory summary
```

Do not copy:

```text
dynamic_object_points
```

## Not Recommended

```cpp
auto copy = graph->clone();  // clone 5GB
clear points;
```

Reason:

```text
first copies 5 GB
```

and may still cause a memory peak.

---

# 16. Do Not Clear Both Live Graphs at the Start of `Backend::save()`

Do not use this as the final implementation:

```cpp
for (...) {
  unmerged_graph_...dynamic_object_points.clear();
}

for (...) {
  private_dsg_->graph...dynamic_object_points.clear();
}
```

Reasons:

1. destructive
2. It changes the input to later map saves.
3. It changes debug/archive state.
4. It is difficult to prove that algorithm outputs remain unchanged.
5. Dynamic history is lost if it is needed later.

---

# 17. Add a Separate RAG Output

The cleanest architecture is:

```text
Khronos full in-memory DSG
        │
        ├── normal/full output (optional)
        │
        └── RAG lightweight exporter
                  ↓
         hospital_rag.sparkdsg
         hospital_rag.json
```

The lightweight RAG DSG retains:

```text
Objects
Places
Rooms
Buildings
Edges
semantic labels
positions
room containment
place topology
is_dynamic
optional trajectory summary
```

It does not store:

```text
dynamic_object_points
raw point history
```

This does not interfere with Khronos's standard save path.

---

# 18. The Most Important A/B Test Is Not the Full 3,178-Frame Run

Use the small known-good dataset:

```text
300 frames
```

First validate the lightweight exporter.

Baseline：

```text
793 nodes
3571 edges
9 objects
727 places
9 rooms
1 building
```

The lightweight output must:

```text
793 nodes
3571 edges
9 objects
727 places
9 rooms
1 building
```

**match exactly.**

The only permitted change:

```text
file size
dynamic_object_points field
```

---

# 19. Hard Acceptance Criteria for the A/B Test

Outputs:

```text
reports/lightweight_graph_equivalence.json
```

must include:

```json
{
  "baseline": {
    "nodes": 793,
    "edges": 3571,
    "objects": 9,
    "places": 727,
    "rooms": 9,
    "buildings": 1
  },
  "lightweight": {
    "nodes": 793,
    "edges": 3571,
    "objects": 9,
    "places": 727,
    "rooms": 9,
    "buildings": 1
  },
  "topology_equal": true,
  "semantic_labels_equal": true,
  "positions_equal": true,
  "dynamic_object_points_removed": true
}
```

If any:

```text
places
rooms
buildings
edges
```

changes:

```text
FAIL
```

stop immediately; do not run the full P1000 dataset.

---

# 20. Start with the 300-Frame Known-Good Baseline, Not the Unknown 500-Frame Baseline

The earlier 500-frame test was used, but the old project already has a well-established:

```text
300-frame smoke known-good
```

The more appropriate next step is:

```text
same 300 frames
same configuration
same bag
```

Then run:

```text
old binary / old output
vs
new lightweight save
```

This provides the clearest causal comparison.

---

# 21. Fix the Latest Missing-Config Launch Issue Separately

The failed command currently uses:

```text
/home/ulmmt/Masterthesis-wang/...
```

but the node runs inside a container:

```text
/workspace/...
```

Therefore, the configuration file is missing.

The agent must use a consistent path strategy.

Recommended:

```text
Use container paths for all configuration files inside the container.
```

For example:

```text
/workspace/tartanground_khronos/...
```

Before launch:

```bash
test -f "$INPUT_CONFIG" || exit 10
test -f "$MAPPER_CONFIG" || exit 11
test -f "$LABELSPACE_CONFIG" || exit 12
test -f "$REMAP_CONFIG" || exit 13
```

If a configuration file is missing:

```text
do not launch Khronos
```

---

# 22. Do Not Mix the Launch and `ros2 run` Workflows

The known-good 08-16 run used:

```text
ros2 launch tartanground_khronos_bridge ...
```

Many later experiments switched to:

```text
ros2 run khronos_ros khronos_node ...
```

The two workflows may compose parameters differently.

When restoring the baseline:

> First use the exact launch method that succeeded on 08-16.

Switch to a simplified `ros2 run` workflow only after the baseline has been reproduced.

---

# 23. First Regression Task: Reproduce the 300-Frame Baseline

Do not fix the strip first.

First record:

```text
current environment
current container
current source
current bag
```

Run the known configuration and verify:

```text
hospital_p2000_smoke
300 frames
```

Targets:

```text
793 nodes ± a reasonable small difference
Places are present
Rooms are present
Building is present
```

Stricter requirement:

If the pipeline is deterministic:

```text
793 / 3571 / 9 / 727 / 9 / 1
```

reproduce the baseline exactly.

If the current baseline itself produces only objects:

> The problem is not the strip-save logic.

First identify a build/configuration/environment regression.

---

# 24. Make the Lightweight Modification Only After Reproducing the Baseline

Sequence:

```text
A. current unmodified source + 300 frames
   → PASS

B. apply lightweight serialization ONLY
   → rebuild
   → verify build marker
   → same 300 frames
   → compare graph
```

Change only one variable at a time.

---

# 25. Do Not Debug Directly with the Full 1,200/3,178-Frame Runs

Each full run:

```text
takes a long time
uses substantial memory
has expensive save operations
```

and increases the risk of conflating outputs again.

Only after:

```text
300 baseline PASS
300 lightweight PASS
```

then:

```text
1200 frames
```

and then:

```text
3178 frames
```

---

# 26. Current Assessment of the 574-Node Degraded Output

No conclusion can currently be made that:

```text
strip dynamic_object_points broke DSG
```

Current hypotheses, in descending order of plausibility:

1. **The wrong output file was analyzed.**
2. **The run/output/log timeline was conflated.**
3. **The wrong install overlay or a stale binary was used.**
4. **A different launch method or configuration composition was used.**
5. **A configuration-path error prevented a run from starting normally.**
6. The graph-analysis script parsed the wrong file/schema.
7. The lightweight modification caused a side effect.
8. A Khronos algorithm regression occurred.

First rule out hypotheses 1–6.

---

# 27. The Agent Must Maintain a RUN_MANIFEST

Generate this for every future run:

```text
RUN_MANIFEST.json
```

Contents:

```json
{
  "run_id": "",
  "date": "",
  "bag": "",
  "frame_count": 0,
  "launch_mode": "ros2_launch|ros2_run",
  "command": "",
  "output_dir": "",
  "log_file": "",
  "input_config": "",
  "mapper_config": "",
  "labelspace_config": "",
  "remap_config": "",
  "binary": "",
  "binary_sha256": "",
  "backend_cpp_sha256": "",
  "ament_prefix_path": "",
  "result": ""
}
```

Runs without a manifest:

> must not be used as evidence for formal experimental conclusions.

---

# 28. Generate Graph Statistics Automatically for Every Output

After each run:

```text
graph_stats.json
```

include:

```text
nodes
edges
objects
places
rooms
buildings
dynamic nodes
dynamic_object_points bytes
file size
```

This avoids relying on manual guesses.

---

# 29. Recommended Final Fix

The current goal is not to modify Khronos mapping.

The final solution should add:

```text
save_rag_lightweight_dsg
```

instead of destructively modifying live state.

Recommended behavior:

```text
Full Khronos Graph
↓
RAG Serializer
↓
skip dynamic_object_points
↓
retain all graph layers
↓
hospital_rag.json / sparkdsg
```

---

# 30. Strict Execution Order for the Agent

## Phase 1 — Provenance

```text
1. Locate the 574-node file.
2. Confirm its absolute path.
3. hash
4. parent output dir
5. matching log
6. matching config
7. matching binary
```

If the provenance cannot be matched completely:

```text
STOP = INCONCLUSIVE
```

---

## Phase 2 — Layer audit

Analyze separately:

```text
known-good 1200 dsg
574-node candidate
all other DSG files from the same run
```

Report the actual layer statistics.

---

## Phase 3 — Baseline reproduction

Use:

```text
300-frame smoke
known-good launch path
unmodified backend
```

Confirm that Places / Rooms / Building are present.

---

## Phase 4 — Build provenance

Add:

```text
[HOSPITAL_LIGHT_SAVE_V1]
```

marker.

Confirm that the modified source is used by the actual binary.

---

## Phase 5 — Lightweight save implementation

Do not destructively clear live data.

Implement:

```text
serialization-time skip
or a separate RAG exporter
```

---

## Phase 6 — 300-frame equivalence A/B

Require the graph topology to remain unchanged.

---

## Phase 7 — 1200 frames

Proceed only if Phase 6 passes.

---

## Phase 8 — Full P1000

Run the 3,178-frame dataset only at the end.

---

# 31. Highest-Priority Instructions for the Agent

> Do not attribute “574 nodes, objects only” to the `dynamic_object_points` strip at this stage.
>
> The archived source contains no strip modification; `backend.cpp` and its `.bak` file are identical.
>
> The latest `voxel010_full.log` records a startup SIGABRT caused by an incorrect configuration path; it is a separate issue from the degraded DSG.
>
> The first task is to establish provenance for the degraded output:
>
> ```text
> DSG file
> ↔ output directory
> ↔ experiment log
> ↔ config
> ↔ command
> ↔ executable
> ↔ source hash
> ```
>
> Do not modify Khronos core code until the full correspondence is established.
>
> Then reproduce the four-layer DSG using the 300-frame known-good baseline and run the lightweight-serialization A/B test.
