# Six failed-task diagnostic rerun (2026-10-23)

## Outcome

All six tasks that failed in the original 49-mission navigation batch succeeded on a diagnostic rerun. The rerun used the same frozen mission JSON, Hospital map bundle, point-robot Nav2 profile, and fixed F0 start, but a fresh ROS domain (`ROS_DOMAIN_ID=74`) to isolate the earlier ROS action/service availability issue.

| Task | Original failure | Rerun | Duration (s) | Reached stops |
|---|---|---:|---:|---:|
| HC-029 | `LoadMap` service response timeout | Succeeded | 406.998 | 2/2 |
| HC-056 | `NavigateToPose` unavailable before first portal goal | Succeeded | 237.434 | 1/1 |
| HC-057 | `NavigateToPose` unavailable before first stop goal | Succeeded | 335.731 | 2/2 |
| HC-058 | `NavigateToPose` unavailable before first portal goal | Succeeded | 246.949 | 1/1 |
| HC-059 | `NavigateToPose` unavailable before first stop goal | Succeeded | 265.359 | 2/2 |
| HC-060 | `NavigateToPose` unavailable before first stop goal | Succeeded | 70.628 | 1/1 |

The five `NavigateToPose unavailable` failures were failures to discover/reach the Nav2 action server within the runner's 15-second wait. The goals were not accepted in the original run, so those cases did not demonstrate that the robot moved and failed to reach the first point. On rerun, all selected stops were reached.

HC-029's original `LoadMap` failure occurred before task events were recorded. The Nav2 log shows the map server read F0's map, then reported a timed-out service response. The rerun loaded maps and completed both navigation stops.

## Provenance and interpretation

- Map bundle: `hospital-p1000-khronos-only-20261019-band005-200`
- Bundle file SHA-256: `a0a61b69b09cfe584d26056aee5e02af42f689b807f687032b2c4f5b3a91829a`
- Nav2 profile: `khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_point.yaml`
- Start: frozen F0 pose `(14.85, 3.65, 0.0)`
- ROS domain: `74` (separate from the original batch's domain `73`)
- Retry result JSON: `results/HC-*.retry.result.json`
- Retry runner logs: `logs/HC-*.runner.log`; complete isolated Nav2 launch log: `logs/nav2-launch.log`
- Original failures remain unchanged under the parent run's `tasks/` directory.
- The original batch remains **43/49 succeeded, 6/49 failed**. This diagnostic rerun is supplementary evidence; it does not overwrite or retroactively change the original batch statistic.

The success of all six retries strongly supports a transient ROS 2 service/action availability or discovery problem in the original long-lived run. Because the retry used a fresh ROS domain, it does not by itself identify the exact underlying trigger.
