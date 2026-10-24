# Hospital P1000 Khronos-only Nav2 maps

These four checked-in 0.1 m maps are the active Khronos-only navigation maps
for F0-F3. Their PGM/YAML hashes are pinned in
`config/hospital_p1000_floor_transitions.yaml` and summarized in
`map_manifest.json`.

The maps were generated from Khronos Place support and the projected Khronos
mesh. The grid geometry is recorded in
`experiments/hospital-khronos-only-20261019/grid_geometry.json`; it contains
only floor extents, resolution, and origin plus source hashes. No depth-map
occupancy pixels or SLAM occupancy output are consumed. Regenerating map pixels
requires the local Hospital P1000 DSG and mesh inputs, which are intentionally
not committed.

Each floor report is a sanitized summary; it contains no local absolute paths.
Floor transitions remain an abstract Nav2 map/pose switch, not physical stair
navigation.

To regenerate maps from the local P1000 `dsg.json` and `mesh.ply` after placing
the source snapshot under the ignored `datasets/Hospital-workstation-backup-20261006/`
directory, run from the repository root with a new output path:

```bash
python3 experiments/hospital-khronos-only-20261019/generate_khronos_only.py
```

The default output is the ignored directory
`experiments/hospital-khronos-only-20261019/output/hospital-p1000-khronos-only-generated/`;
the script uses the adjacent geometry-only manifest and refuses to overwrite a
non-empty output directory. This is also the source of the repaired DSG used by
the optional four-floor room-label mosaic. The committed maps are the verified
output of this pipeline; large intermediate DSG/mesh files are not included.
