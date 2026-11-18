#!/usr/bin/env python3
"""Local browser UI for interactive Hospital navigation planning and simulation."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
NAV2_PACKAGE = ROOT / "khronos-nav2-bridge" / "khronos_nav2_bridge"
NAV2_SCRIPTS = NAV2_PACKAGE / "scripts"
if str(ROOT / "llm-rag" / "planner") not in sys.path:
    sys.path.insert(0, str(ROOT / "llm-rag" / "planner"))
if str(NAV2_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(NAV2_SCRIPTS))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_snapshot import RouteHistory

import plan_nav2  # noqa: E402
import yaml  # noqa: E402
from floor_transition_core import validate_transition_bundle  # noqa: E402

FLOOR_HEIGHTS = {0: -1.2, 1: 2.3, 2: 5.8, 3: 9.3}
RVIZ_CONFIG = NAV2_PACKAGE / "rviz" / "hospital_nav2_manual.rviz"
NAV2_LAUNCH = "hospital_nav2.launch.py"
RUNNER_SCRIPT = NAV2_SCRIPTS / "run_task_chain_nav2.py"
DEFAULT_PARAMS = NAV2_PACKAGE / "config" / "nav2_hospital_rviz.yaml"
DEFAULT_RAG_PROFILE_CONFIG = Path(__file__).resolve().parent / "rag_profiles.json"
MAX_REQUEST_BYTES = 256 * 1024

PROGRESS_MESSAGES = {
    "checking_inputs": "Checking the RAG index, map, and run configuration.",
    "inputs_ready": "RAG index and map configuration validated.",
    "intent_parsing": "LLM1 is analyzing the navigation request.",
    "intent_parsed": "Intent analysis complete.",
    "rag_grounding": "Searching the RAG index and grounding scene-graph destinations.",
    "rag_grounded": "RAG grounding complete.",
    "selection_rejected": "That selection no longer matches the available candidates. Choose again.",
    "awaiting_user_selection": "Multiple destinations match. Choose one to continue.",
    "planning": "Destination resolved. Generating the task chain.",
    "validating_plan": "Validating the task chain and destination constraints.",
    "plan_ready": "Task chain generated.",
    "pipeline_stopped": "Planning paused. See the stage output for details.",
    "mission_preparation": "Compiling the ordered navigation mission.",
    "mission_ready": "Navigation mission prepared.",
    "mission_not_ready": "Could not compile the navigation mission.",
    "nav2_planning": "Nav2 is calculating the route.",
    "nav2_starting": "Starting the isolated Nav2 simulation stack.",
    "nav2_route_planning": "Nav2 is calculating the route.",
    "plan_complete": "Route planning complete.",
}


def build_candidate_groups(pipeline_result: dict, canonical_graph: dict) -> list[dict]:
    """Attach readable graph context to ambiguous references returned by grounding."""
    grounding = pipeline_result.get("grounding") or {}
    references = grounding.get("references") or {}
    nodes = canonical_graph.get("nodes") or {}
    if not isinstance(nodes, dict):
        return []

    object_evidence: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        if node.get("node_type") != "object":
            continue
        label = str(node.get("label") or node.get("name") or "").strip()
        if not label or label.lower() == "unknown":
            continue
        room_id = str(node.get("room_id") or "")
        if room_id:
            object_evidence.setdefault(room_id, set()).add(label)
        for parent_id in node.get("parent_ids") or []:
            object_evidence.setdefault(str(parent_id), set()).add(label)

    intent = pipeline_result.get("intent") or {}
    reference_text = {}
    for role in ("actor", "target", "source", "destination"):
        ref = intent.get(role)
        if isinstance(ref, dict) and ref.get("text"):
            reference_text[role] = ref["text"]
    for step_index, step in enumerate(intent.get("steps") or []):
        for role in ("actor", "target", "source", "destination"):
            ref = step.get(role)
            if isinstance(ref, dict) and ref.get("text"):
                reference_text[f"step_{step_index}_{role}"] = ref["text"]

    groups = []
    for key, reference in references.items():
        if reference.get("status") != "ambiguous":
            continue
        options = []
        for raw_id in reference.get("candidate_node_ids") or []:
            node_id = str(raw_id)
            node = nodes.get(node_id)
            if not isinstance(node, dict):
                continue
            position = node.get("position") or []
            floor_id = None
            if isinstance(position, (list, tuple)) and len(position) >= 3:
                try:
                    z = float(position[2])
                    floor_id = min(FLOOR_HEIGHTS,
                                   key=lambda floor: abs(FLOOR_HEIGHTS[floor] - z))
                except (TypeError, ValueError, OverflowError):
                    floor_id = None
            label = str(node.get("label") or "").strip()
            name = str(node.get("name") or "").strip()
            title = name or (label if label and label.lower() != "unknown"
                             else node.get("node_type", "scene node"))
            evidence = sorted(object_evidence.get(node_id, set()))
            if not evidence and node.get("room_id"):
                evidence = sorted(object_evidence.get(str(node["room_id"]), set()))
            options.append({
                "node_id": node_id,
                "title": title,
                "label": label,
                "node_type": node.get("node_type", "unknown"),
                "floor_id": floor_id,
                "position": list(position) if isinstance(position, (list, tuple)) else [],
                "room_id": node.get("room_id"),
                "evidence": evidence[:8],
            })
        options.sort(key=lambda item: (item["floor_id"] is None,
                                       item["floor_id"] or -1,
                                       item["title"].casefold(), item["node_id"]))
        if options:
            groups.append({
                "reference_key": key,
                "reference_text": reference_text.get(key, key),
                "reason": reference.get("reason"),
                "options": options,
            })
    return groups


def rag_review_badge(review_csv_sha256: str | None) -> str:
    if review_csv_sha256:
        return f"Human-reviewed semantic labels applied · {review_csv_sha256[:12]}"
    return "No human-reviewed semantic labels applied"


def _read_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML mapping in {path}")
    return value


HTML = (Path(__file__).resolve().parent / "web" / "index.html").read_text(encoding="utf-8")


class NavigationController:
    def __init__(self):
        self.task_chain_backend = os.environ.get("HOSPITAL_TASK_CHAIN_BACKEND", "llm2")
        if self.task_chain_backend not in ("llm2", "grounded_deterministic"):
            raise ValueError("HOSPITAL_TASK_CHAIN_BACKEND must be llm2 or grounded_deterministic")
        self.profile_config_path = Path(os.environ.get(
            "HOSPITAL_RAG_PROFILE_CONFIG", str(DEFAULT_RAG_PROFILE_CONFIG))).expanduser()
        self.profile_root = self.profile_config_path.resolve().parent
        self.bundle_path = Path(os.environ.get("HOSPITAL_NAV2_BUNDLE", "")).expanduser()
        self.params_path = Path(os.environ.get("HOSPITAL_NAV2_PARAMS", str(DEFAULT_PARAMS))).expanduser()
        self.rviz_path = Path(os.environ.get("HOSPITAL_NAV2_RVIZ_CONFIG", str(RVIZ_CONFIG))).expanduser()
        self.ros_domain_id = int(os.environ.get("HOSPITAL_NAV2_DOMAIN_ID", "79"))
        self.bundle = self.defaults = self.canonical_graph = None
        self.rag_db = self.defaults_path = None
        self.rag_info = None
        self.rag_profiles = {}
        self.selected_rag_profile_id = None
        self.startup_error = None
        self.discovery_error = None
        self.state_lock = threading.RLock()
        self.profile_scan_lock = threading.Lock()
        self.last_profile_scan = time.monotonic()
        self.profile_scan_interval_s = 15.0
        self.job_lock = threading.Lock()
        self.stop_requested = False
        self._stopping = False
        self._demo_execution_id = None
        self._demo_goal_ledger = None
        self.state = {"status": "idle", "events": [], "candidate_groups": [],
                      "stage_outputs": {}, "result": None,
                      "demo_result": None, "error": None, "stage_status": {},
                      "final_summary": {}, "route_snapshot": None}
        self.last_result = None
        self.last_mission_path = None
        self.nav2_process = self.rviz_process = self.demo_process = self.labels_process = None
        self.output_dir = Path(os.environ.get(
            "HOSPITAL_UI_OUTPUT_DIR", str(Path.home() / ".cache" / "hospital-nav2-ui")
        )).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load_inputs()

    @staticmethod
    def _resolved_profile_path(base, value):
        raw = Path(value).expanduser()
        return (raw if raw.is_absolute() else base / raw).resolve(strict=True)

    def _validate_rag_profile(self, profile_id, label, rag_path, defaults_path,
                              bundle_path, expected=None):
        bundle = _read_yaml(bundle_path)
        validate_transition_bundle(bundle, verify_files=True)
        defaults = _read_yaml(defaults_path)
        plan_nav2._load_defaults(defaults_path, bundle)
        rag_root = rag_path.resolve().parent
        manifest_path = rag_root / "build_manifest.json"
        if rag_path.name != "vector_db" or not (rag_root / "vector_db" / "chroma.sqlite3").is_file():
            raise FileNotFoundError(f"RAG vector database is missing: {rag_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_nav2._validate_build_manifest(manifest)
        if manifest.get("schema_version") not in (
                "hospital-rag-build-manifest-v1", "hospital-rag-build-manifest-v2",
                "hospital-rag-build-manifest-v3"):
            raise ValueError(f"Unsupported RAG manifest schema: {profile_id}")
        build_config = manifest.get("build_config") or {}
        if build_config.get("dataset") not in ("hospital_p1000", "hospital-p1000"):
            raise ValueError(f"RAG profile is not a Hospital P1000 build: {profile_id}")
        inputs = manifest.get("inputs") or {}
        actual_dsg = (inputs.get("dsg") or {}).get("sha256")
        actual_review = (inputs.get("semantic_overrides") or {}).get("sha256")
        expected_source = defaults.get("rag_source") or {}
        for key, actual in (
                ("dsg_sha256", actual_dsg),
                ("semantic_review_csv_sha256", actual_review),
                ("embedding_model", build_config.get("embedding_model")),
                ("embedding_model_revision", build_config.get("embedding_model_revision")),
                ("embedding_device", build_config.get("embedding_device"))):
            if expected_source.get(key) != actual:
                raise ValueError(f"RAG profile provenance mismatch for {key}: {profile_id}")
        if not build_config.get("embedding_model_revision"):
            raise ValueError(f"RAG profile does not pin an embedding revision: {profile_id}")

        expected = expected or {}
        build_id = manifest.get("build_id")
        bundle_id = bundle.get("map_bundle_id")
        expected_build = expected.get("expected_build_id") or expected.get("rag_build_id")
        if expected_build and build_id != expected_build:
            raise ValueError(f"RAG profile build ID mismatch: {profile_id}")
        if ("review_csv_sha256" in expected
                and actual_review != expected.get("review_csv_sha256")):
            raise ValueError(f"RAG profile review hash mismatch: {profile_id}")
        if expected.get("source_dsg_sha256") and actual_dsg != expected["source_dsg_sha256"]:
            raise ValueError(f"RAG profile DSG hash mismatch: {profile_id}")
        if expected.get("map_bundle_id") and bundle_id != expected["map_bundle_id"]:
            raise ValueError(f"RAG profile map bundle ID mismatch: {profile_id}")
        for key, path in (("defaults_sha256", defaults_path),
                          ("bundle_sha256", bundle_path)):
            if expected.get(key) and plan_nav2._sha256_file(path) != expected[key]:
                raise ValueError(f"RAG profile {key} mismatch: {profile_id}")
        expected_rag_manifest = expected.get("rag_manifest_sha256")
        if expected_rag_manifest and plan_nav2._sha256_file(manifest_path) != expected_rag_manifest:
            raise ValueError(f"RAG profile manifest hash mismatch: {profile_id}")
        run_manifest_path = expected.get("run_profile_manifest")
        if run_manifest_path:
            run_manifest = json.loads(Path(run_manifest_path).read_text(encoding="utf-8"))
            if run_manifest.get("rag_build_id") != build_id:
                raise ValueError(f"Run profile does not match its RAG build: {profile_id}")
            if run_manifest.get("map_bundle_id") != bundle_id:
                raise ValueError(f"Run profile does not match its map bundle: {profile_id}")
            if run_manifest.get("rag_manifest_sha256") != plan_nav2._sha256_file(manifest_path):
                raise ValueError(f"Run profile RAG manifest hash mismatch: {profile_id}")
            if run_manifest.get("defaults_sha256") != plan_nav2._sha256_file(defaults_path):
                raise ValueError(f"Run profile defaults hash mismatch: {profile_id}")
            if run_manifest.get("bundle_sha256") != plan_nav2._sha256_file(bundle_path):
                raise ValueError(f"Run profile map bundle hash mismatch: {profile_id}")
            if run_manifest.get("source_dsg_sha256") != actual_dsg:
                raise ValueError(f"Run profile DSG hash mismatch: {profile_id}")
            if run_manifest.get("review_csv_sha256") != actual_review:
                raise ValueError(f"Run profile review hash mismatch: {profile_id}")
        canonical_path = rag_root / "canonical_graph_v2.json"
        if not canonical_path.is_file():
            raise FileNotFoundError(f"RAG canonical graph is missing: {canonical_path}")
        canonical_graph = json.loads(canonical_path.read_text(encoding="utf-8"))
        rag_info = {
            "build_id": build_id,
            "manifest_path": str(manifest_path),
            "manifest_sha256": plan_nav2._sha256_file(manifest_path),
            "source_dsg_sha256": actual_dsg,
            "semantic_review_csv_sha256": actual_review,
        }
        return {
            "id": profile_id, "label": label,
            "rag_db": rag_path, "defaults_path": defaults_path,
            "bundle_path": bundle_path, "bundle": bundle,
            "defaults": defaults, "rag_info": rag_info,
            "canonical_graph": canonical_graph,
            "build_id": build_id, "review_csv_sha256": actual_review,
            "profile_key": f"{build_id}:{bundle_id}",
        }

    def _load_inputs(self):
        if not self.profile_config_path.is_file():
            self.startup_error = f"RAG profile configuration is missing: {self.profile_config_path}"
            return
        if not self.params_path.is_file() or not self.rviz_path.is_file():
            self.startup_error = "Nav2 parameters or RViz configuration file is missing."
            return
        try:
            config = json.loads(self.profile_config_path.read_text(encoding="utf-8"))
            if config.get("schema_version") != "hospital-navigation-demo-rag-profiles-v1":
                raise ValueError("Unsupported RAG profile configuration schema.")
            default_bundle = config.get("bundle")
            if not default_bundle and not os.environ.get("HOSPITAL_NAV2_BUNDLE"):
                raise ValueError("RAG profile configuration does not name a map bundle.")
            default_bundle_path = self._resolved_profile_path(
                self.profile_root, os.environ.get("HOSPITAL_NAV2_BUNDLE") or default_bundle)
            profiles = config.get("profiles")
            if not isinstance(profiles, list) or not profiles:
                raise ValueError("RAG profile configuration must contain at least one profile.")
            for item in profiles:
                if not isinstance(item, dict):
                    raise ValueError("Each RAG profile must be an object.")
                profile_id = item.get("id")
                label = item.get("label")
                if not isinstance(profile_id, str) or not profile_id or profile_id in self.rag_profiles:
                    raise ValueError("RAG profile IDs must be unique non-empty strings.")
                if not isinstance(label, str) or not label:
                    raise ValueError(f"RAG profile {profile_id} has no display label.")
                rag_path = self._resolved_profile_path(self.profile_root, item["rag_db"])
                defaults_path = self._resolved_profile_path(self.profile_root, item["defaults"])
                bundle_path = self._resolved_profile_path(
                    self.profile_root, item.get("bundle", default_bundle_path))
                expected = dict(item)
                if expected.get("run_profile_manifest"):
                    expected["run_profile_manifest"] = self._resolved_profile_path(
                        self.profile_root, expected["run_profile_manifest"])
                profile = self._validate_rag_profile(
                    profile_id, label, rag_path, defaults_path, bundle_path, expected)
                self.rag_profiles[profile_id] = profile
            default_id = config.get("default_profile_id")
            if default_id not in self.rag_profiles:
                raise ValueError("Default RAG profile is not present in the profile list.")
            self._activate_rag_profile(default_id)
        except Exception as error:
            self.startup_error = f"Demo profile validation failed: {type(error).__name__}: {error}"

    def _activate_rag_profile(self, profile_id):
        with self.state_lock:
            profile = self.rag_profiles[profile_id]
            self.rag_db = profile["rag_db"]
            self.defaults_path = profile["defaults_path"]
            self.bundle_path = profile["bundle_path"]
            self.bundle = profile["bundle"]
            self.defaults = profile["defaults"]
            self.rag_info = profile["rag_info"]
            self.canonical_graph = profile["canonical_graph"]
            self.selected_rag_profile_id = profile_id

    @staticmethod
    def _walk_build_manifests(root, max_depth=6):
        root = Path(root).expanduser()
        if not root.is_dir():
            return
        pruned = {".git", ".cache", ".local", ".conda", ".codex", ".agents",
                  ".venv", "venv", "miniconda3", "node_modules", "__pycache__",
                  "site-packages", "datasets", "models", "Masterthesis-wang"}
        for directory, subdirs, files in os.walk(root):
            current = Path(directory)
            try:
                depth = len(current.relative_to(root).parts)
            except ValueError:
                subdirs[:] = []
                continue
            if depth >= max_depth:
                subdirs[:] = []
            else:
                subdirs[:] = [name for name in subdirs
                              if name not in pruned and not name.startswith(".")]
                if root.resolve() == Path.home().resolve() and depth == 0:
                    tokens = ("run", "output", "hospital", "khronos", "rag", "experiment")
                    subdirs[:] = [name for name in subdirs
                                  if any(token in name.lower() for token in tokens)]
            if "build_manifest.json" in files and (current / "vector_db").is_dir():
                yield current

    def refresh_rag_profiles(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_profile_scan < self.profile_scan_interval_s:
            return 0
        if not self.profile_scan_lock.acquire(blocking=False):
            return 0
        added = 0
        try:
            roots = [ROOT / "experiment",
                     Path.home() / "Masterthesis-wang" / "RAG" /
                     "khronos_3dsg_rag" / "outputs",
                     Path.home()]
            extra_roots = os.environ.get("HOSPITAL_RAG_DISCOVERY_ROOTS", "")
            roots.extend(Path(part).expanduser()
                         for part in extra_roots.split(os.pathsep) if part.strip())
            seen_roots = set()
            for root in roots:
                try:
                    resolved_root = root.resolve(strict=True)
                except OSError:
                    continue
                if resolved_root in seen_roots:
                    continue
                seen_roots.add(resolved_root)
                for build_root in self._walk_build_manifests(resolved_root):
                    try:
                        build_manifest = json.loads(
                            (build_root / "build_manifest.json").read_text(encoding="utf-8"))
                        build_id = build_manifest.get("build_id")
                        build_rag = build_root / "vector_db"
                        run_root = None
                        for ancestor in (build_root.parent, *list(build_root.parents)[1:5]):
                            if ((ancestor / "nav2-profile" /
                                 "hospital_nav2_plan_defaults.yaml").is_file()
                                    and (ancestor / "nav2-profile" /
                                         "hospital_floor_transitions.yaml").is_file()
                                    and (ancestor / "nav2-profile" /
                                         "run_profile_manifest.json").is_file()):
                                run_root = ancestor
                                break
                        if not run_root or not build_id:
                            continue
                        nav_profile = run_root / "nav2-profile"
                        run_manifest = json.loads(
                            (nav_profile / "run_profile_manifest.json").read_text(
                                encoding="utf-8"))
                        bundle_id = run_manifest.get("map_bundle_id")
                        profile_key = f"{build_id}:{bundle_id}"
                        with self.state_lock:
                            if any(p["profile_key"] == profile_key
                                   for p in self.rag_profiles.values()):
                                continue
                        source_review = (build_manifest.get("inputs", {})
                                        .get("semantic_overrides") or {}).get("sha256")
                        review_label = "human-reviewed" if source_review else "no manual labels"
                        profile_id = f"generated-{build_id[:12]}-{str(bundle_id)[-8:]}"
                        label = f"{run_root.name} · {review_label} · {build_id[:8]}"
                        expected = {
                            "rag_build_id": build_id,
                            "review_csv_sha256": source_review,
                            "source_dsg_sha256": (build_manifest.get("inputs", {})
                                                  .get("dsg", {}).get("sha256")),
                            "map_bundle_id": bundle_id,
                            "run_profile_manifest": nav_profile / "run_profile_manifest.json",
                        }
                        profile = self._validate_rag_profile(
                            profile_id, label, build_rag,
                            nav_profile / "hospital_nav2_plan_defaults.yaml",
                            nav_profile / "hospital_floor_transitions.yaml",
                            expected)
                        with self.state_lock:
                            if not any(p["profile_key"] == profile_key
                                       for p in self.rag_profiles.values()):
                                self.rag_profiles[profile_id] = profile
                                added += 1
                    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                        continue
            self.discovery_error = None
            self.last_profile_scan = time.monotonic()
            return added
        finally:
            self.profile_scan_lock.release()

    def _append_event(self, stage, message, **details):
        # Keep the progress timeline focused on user-facing flow. Detailed
        # exceptions remain in the page error notice and structured outputs.
        if stage in {"system_error", "demo_error"}:
            message = "The process encountered an issue. See the page notice and stage output."
            details = {}
        event = {"time": time.strftime("%H:%M:%S"), "stage": stage,
                 "message": message, "details": details}
        with self.state_lock:
            if getattr(self, "stop_requested", False) and stage != "stopped":
                return
            self.state["events"].append(event)
            self.state["events"] = self.state["events"][-120:]
            self.state["stage"] = stage
            self.state["stage_message"] = message

    @staticmethod
    def _compact_intent_payload(raw):
        # Display whitelist follows SimpleTaskChainIntent/SimpleTaskStop. The
        # parser validates the recorded payload; do not reconstruct lost text.
        if not isinstance(raw, dict) or raw.get("action") not in {"navigate", "ask_clarification"}:
            return None
        stops = raw.get("stops")
        if not isinstance(stops, list) or len(stops) > 5:
            return None
        projected = []
        for index, stop in enumerate(stops):
            if (not isinstance(stop, dict) or type(stop.get("order")) is not int
                    or stop["order"] != index + 1
                    or not isinstance(stop.get("destination"), str) or not stop["destination"].strip()
                    or not isinstance(stop.get("objects"), list)
                    or not all(isinstance(x, str) and x.strip() for x in stop["objects"])):
                return None
            floor = stop.get("floor_id")
            if floor is not None and not (type(floor) is int and 0 <= floor <= 3
                    or isinstance(floor, str) and re.fullmatch(r"[Ff]?[0-3]", floor)):
                return None
            projected.append({key: stop[key] for key in
                              ("order", "destination", "objects", "floor_id") if key in stop})
        if raw["action"] == "navigate" and (not stops or raw.get("clarification")):
            return None
        if raw["action"] == "ask_clarification" and (stops or not isinstance(raw.get("clarification"), str)
                                                      or not raw["clarification"].strip()):
            return None
        output = {key: raw[key] for key in ("action", "clarification") if key in raw}
        output["stops"] = projected
        return output

    @staticmethod
    def _friendly_error(value):
        """Read structured causes, not warning logs or stack traces."""
        reasons, errors = [], []
        def visit(item):
            if isinstance(item, dict):
                for key in ("reason", "error"):
                    text = item.get(key)
                    if isinstance(text, str) and text.strip():
                        text = text.strip()
                        if ("\n" not in text and len(text) < 400
                                and not re.search(r"traceback|\[warn|\[error|File \"", text, re.I)):
                            (reasons if key == "reason" else errors).append(text)
                for key, child in item.items():
                    if key not in {"raw", "raw_content", "thinking_text", "runner_stderr_tail",
                                   "planning_attempts", "intent_parse_attempts", "error", "reason"}:
                        visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
        visit(value)
        messages = list(dict.fromkeys(reasons or errors))[:3]
        return " ".join(messages)[:360] or "This stage could not finish. Details are saved in the run artifact."

    @staticmethod
    def _final_summary(result):
        pipeline = result.get("pipeline_result") or {}
        plan_status = pipeline.get("status")
        task_plan = pipeline.get("task_plan")
        plan_ok = plan_status == "valid" and not pipeline.get("validation_errors")
        if plan_ok:
            plan = {"status": "succeeded", "message": "Task plan passed validation."}
        elif plan_status == "ambiguous":
            plan = {"status": "needs_input", "message": "A destination or clarification is needed before planning can continue."}
        elif plan_status == "target_not_found":
            plan = {"status": "not_run", "message": (
                "No task plan was generated because no matching destination was found."
                if not isinstance(task_plan, dict) else
                "No executable task plan is available because no matching destination was found.")}
        elif not isinstance(task_plan, dict):
            plan = {"status": "not_run", "message": "No task plan was generated. " + NavigationController._friendly_error(pipeline)}
        elif pipeline.get("validation_errors") or task_plan.get("status") == "system_error":
            plan = {"status": "failed", "message": "Task plan did not pass validation."}
        else:
            plan = {"status": "not_run", "message": "The pipeline returned a boundary response rather than an executable task plan."}

        mission = result.get("mission_preparation") or {}
        navigation_plan = result.get("nav2_plan") or {}
        route_status = navigation_plan.get("status")
        if route_status == "planned" and result.get("status") == "planned":
            route = {"status": "succeeded", "message": "A feasible route was selected; navigation has not been run."}
        elif route_status in (None, "not_attempted", "not_run"):
            message = "Route planning was not run."
            if mission.get("status") not in (None, "ready", "not_attempted", "not_run"):
                message += " Mission preparation did not complete. " + NavigationController._friendly_error(mission)
            route = {"status": "not_run", "message": message}
        else:
            route = {"status": "failed", "message": NavigationController._friendly_error(navigation_plan)}
        return {
            "plan": plan,
            "route": route,
            "navigation": {"status": "not_run", "message": "Not run. Route planning does not establish arrival."},
        }

    @staticmethod
    def _navigation_outcome(result, return_code, expected_stops):
        reached = result.get("reached_stops") or []
        ok = (return_code == 0 and result.get("status") == "succeeded"
              and reached == list(range(expected_stops)))
        return {"status": "succeeded" if ok else "failed", "reached_stops": reached,
                "total_stops": expected_stops,
                "message": ("All navigation stops and required transitions completed." if ok
                            else NavigationController._friendly_error(result))}

    def _stage(self, key, status, message):
        with self.state_lock:
            stages = self.state.setdefault("stage_status", {})
            stages[key] = {"status": status, "message": message}
            if status == "running":
                self.state["active_stage"] = key
            if status in {"failed", "needs_input", "cancelled"}:
                keys = ["intent", "rag", "task_chain", "mission", "nav2", "execution"]
                for downstream in keys[keys.index(key) + 1:]:
                    stages[downstream] = {"status": "not_run", "message": "Not run after the preceding stage stopped."}

    def _record_progress(self, event):
        if getattr(self, "stop_requested", False):
            return
        stage = event.get("stage", "planner")
        details = event.get("details") or {}
        message = PROGRESS_MESSAGES.get(stage, "Processing the current stage.")
        starts = {"intent_parsing": "intent", "rag_grounding": "rag", "planning": "task_chain",
                  "validating_plan": "task_chain", "mission_preparation": "mission",
                  "nav2_planning": "nav2", "nav2_starting": "nav2", "nav2_route_planning": "nav2"}
        if stage in starts:
            self._stage(starts[stage], "running", message)
        key, value = None, None
        if stage == "intent_parsed":
            self._stage("intent", "succeeded", "Intent parsed. Recorded compact output follows when available.")
        elif stage == "intent_schema_ready":
            key, value = "intent", self._compact_intent_payload(details.get("intent_schema"))
            message = "Recorded compact intent is available." if value else "No compact model record is available; no payload was reconstructed."
        elif stage == "task_draft_ready":
            key, value = "task_draft", details.get("task_draft")
            message = "Generated task-plan draft recorded (before binding/validation)."
        elif stage == "rag_grounded":
            key, value = "rag", details.get("grounding")
            self._stage("rag", "succeeded", message)
        elif stage == "plan_ready":
            key, value = "task_chain", details.get("task_plan")
            self._stage(key, "succeeded", "Task plan passed validation.")
        elif stage == "plan_invalid":
            key, value = "task_chain", details.get("task_plan")
            message = "The task plan did not pass validation."
            self._stage(key, "failed", message)
        elif stage in {"mission_ready", "mission_not_ready"}:
            mission_result = details.get("mission_result") or {}
            mission = mission_result.get("mission") or {}
            key, value = "mission", {"status": mission_result.get("status"),
                "stop_count": len(mission.get("stops") or []),
                "stop_floors": [stop.get("floor_id") for stop in mission.get("stops") or []]}
            ok = mission_result.get("status") == "ready"
            message = "Navigation mission prepared." if ok else self._friendly_error(mission_result)
            self._stage(key, "succeeded" if ok else "failed", message)
        elif stage == "plan_complete":
            result = details.get("plan_result") or {}
            route = (result.get("nav2_plan") or {}).get("route_selection") or {}
            key, value = "nav2", {"status": result.get("status"),
                "path_length_m": route.get("total_path_length_m"),
                "transition_count": route.get("transition_count"), "execution_performed": False}
            ok = result.get("status") == "planned"
            message = "A feasible route was selected." if ok else self._friendly_error(result)
            self._stage(key, "succeeded" if ok else "failed", message)
        elif stage == "pipeline_stopped":
            pipeline = details.get("pipeline_result") or {}
            key = (self.state.get("active_stage") or "intent")
            waiting = pipeline.get("status") == "ambiguous"
            message = "Choose or clarify the destination to continue." if waiting else self._friendly_error(pipeline)
            self._stage(key, "needs_input" if waiting else "failed", message)
            key = None
        if key:
            with self.state_lock:
                if getattr(self, "stop_requested", False):
                    return
                self.state.setdefault("stage_outputs", {})[key] = value
        self._append_event(stage, message)

    def snapshot(self):
        self.refresh_rag_profiles()
        with self.state_lock:
            snapshot = json.loads(json.dumps(self.state, ensure_ascii=False))
            selected_profile_id = self.selected_rag_profile_id
            rag_info = self.rag_info
            profiles = [
                {"id": item["id"], "label": item["label"],
                 "build_id": item["build_id"],
                 "review_csv_sha256": item["review_csv_sha256"]}
                for item in self.rag_profiles.values()
            ]
            discovery_error = self.discovery_error
        snapshot["task_chain_backend"] = self.task_chain_backend
        snapshot["rag_build_id"] = (rag_info or {}).get("build_id")
        snapshot["semantic_review_csv_sha256"] = (rag_info or {}).get(
            "semantic_review_csv_sha256")
        snapshot["rag_review_badge"] = rag_review_badge(
            snapshot["semantic_review_csv_sha256"])
        snapshot["selected_rag_profile_id"] = selected_profile_id
        snapshot["rag_profiles"] = profiles
        snapshot["rag_discovery_error"] = discovery_error
        return snapshot

    def select_rag_profile(self, profile_id):
        with self.state_lock:
            if getattr(self, "_stopping", False):
                raise RuntimeError("Stop is still in progress. Wait for confirmation.")
            if profile_id not in self.rag_profiles:
                raise ValueError("Unknown RAG profile.")
            if not self.job_lock.acquire(blocking=False):
                raise RuntimeError("Wait for the current planning or simulation task to finish.")
            try:
                if profile_id == self.selected_rag_profile_id:
                    return
                for name in ("labels_process", "rviz_process"):
                    self._stop_process(name)
                self._activate_rag_profile(profile_id)
                self.stop_requested = False
                self.last_result = None
                self.last_mission_path = None
                self._set_state(status="idle", events=[], candidate_groups=[],
                                stage_outputs={}, stage_status={}, final_summary={}, route_snapshot=None,
                                result=None, demo_result=None, error=None)
                label = self.rag_profiles[profile_id]["label"]
                self._append_event("rag_profile_selected", f"Selected RAG profile: {label}.")
            finally:
                self.job_lock.release()

    def start_plan(self, instruction, selections=None):
        with self.state_lock:
            if getattr(self, "_stopping", False):
                raise RuntimeError("Stop is still in progress. Wait for confirmation.")
            if self.startup_error:
                raise RuntimeError(self.startup_error)
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError("Enter a navigation request.")
            if not self.job_lock.acquire(blocking=False):
                raise RuntimeError("A navigation planning request is already in progress.")
            if self.state.get("status") == "stop_unconfirmed":
                self.job_lock.release()
                raise RuntimeError("Confirm the previous navigation stop before starting a new task.")
            self.stop_requested = False
            if selections is None:
                for name in ("labels_process", "rviz_process"):
                    self._stop_process(name)
                self.last_result = None
                self.last_mission_path = None
                self._demo_execution_id = None
                self._demo_goal_ledger = None
                self._set_state(status="running", session_id=uuid.uuid4().hex,
                                instruction=instruction.strip(), events=[], candidate_groups=[],
                                stage_outputs={}, stage_status={}, final_summary={}, route_snapshot=None,
                                active_stage=None, result=None, demo_result=None, error=None)
            else:
                self._set_state(status="running", candidate_groups=[], result=None,
                                demo_result=None, error=None)
            threading.Thread(target=self._plan_worker,
                             args=(instruction.strip(), selections), daemon=True).start()

    def _set_state(self, **values):
        with self.state_lock:
            if self.stop_requested and values.get("status") != "stopped":
                return
            self.state.update(values)

    def _ros_env(self):
        env = os.environ.copy()
        env.update({"ROS_DOMAIN_ID": str(self.ros_domain_id),
                    "ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST",
                    "ROS_STATIC_PEERS": "",
                    "PATH": "/usr/bin:/opt/ros/jazzy/bin:/bin"})
        env.pop("PYTHONHOME", None)
        return env

    @staticmethod
    def _lifecycle_state(probe):
        if probe.returncode != 0 or probe.stderr.strip():
            return None
        match = re.fullmatch(r"(unconfigured|inactive|active|finalized) \[(\d+)\]",
                             probe.stdout.strip())
        if not match:
            return None
        state, state_id = match.groups()
        return state if {"unconfigured": "1", "inactive": "2", "active": "3",
                         "finalized": "4"}[state] == state_id else None

    def _spawn_owned(self, attr, command, *, allow_stopped=False, **kwargs):
        # Stop and process registration share this lock: no unchecked Popen gap.
        with self.state_lock:
            if getattr(self, "_stopping", False) or (self.stop_requested and not allow_stopped):
                raise RuntimeError("The task was stopped before process startup.")
            process = subprocess.Popen(command, **kwargs)
            setattr(self, attr, process)
            return process

    def _start_nav2(self):
        if self.stop_requested:
            raise RuntimeError("Planning was stopped before Nav2 startup.")
        with self.state_lock:
            if self.nav2_process and self.nav2_process.poll() is None:
                return
            self.nav2_process = None  # Dead handles confer no ownership of a later external stack.
        initial = self.defaults.get("initial_state", {})
        pose = initial.get("pose", {})
        floor_cfg = self.bundle.get("floors", {}).get("0", self.bundle.get("floors", {}).get(0, {}))
        map_yaml = floor_cfg.get("map_yaml_path")
        if not map_yaml:
            raise RuntimeError("Nav2 bundle has no floor-0 map path.")
        env = self._ros_env()
        try:
            planner_probe = subprocess.run(
                ["ros2", "lifecycle", "get", "/planner_server"],
                capture_output=True, text=True, timeout=20, env=env)
        except OSError as error:
            raise RuntimeError("Could not run the ROS 2 Nav2 state check. Launch with "
                               "experiment/navigation-ui-demo/run_demo.sh.") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "Nav2 discovery took longer than 20 seconds. The existing stack was "
                "left untouched; wait briefly and retry.") from error
        planner_state = (planner_probe.stdout + planner_probe.stderr).strip()
        # Jazzy reports an absent node with a nonzero exit status. Only this
        # exact diagnostic authorizes a cold start; transport/CLI errors do not.
        node_missing = (planner_probe.returncode in (0, 1)
                        and planner_state.casefold() == "node not found")
        if not node_missing and planner_probe.returncode != 0:
            raise RuntimeError(
                "ROS 2 Nav2 state check failed. Start the interface with "
                "experiment/navigation-ui-demo/run_demo.sh so the ROS Jazzy environment is loaded.")
        if self._lifecycle_state(planner_probe) == "active":
            try:
                map_probe = subprocess.run(
                    ["ros2", "param", "get", "/map_server", "yaml_filename"],
                    capture_output=True, text=True, timeout=20, env=env)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "The active Nav2 stack was found, but its map check took longer "
                    "than 20 seconds. The stack was left untouched; retry shortly.") from error
            reported_map = (map_probe.stdout + map_probe.stderr).strip()
            prefix = "String value is:"
            if map_probe.returncode != 0 or prefix not in reported_map:
                raise RuntimeError(
                    "An active Nav2 stack already exists in this ROS domain, "
                    "but its map could not be verified. Stop that stack before retrying.")
            active_map = reported_map.split(prefix, 1)[1].strip()
            if Path(active_map).resolve() != Path(map_yaml).resolve():
                raise RuntimeError(
                    f"An active Nav2 stack is using a different map: {active_map}")
            with self.state_lock:
                if self.stop_requested:
                    raise RuntimeError("Planning was stopped before external Nav2 reuse.")
                self.nav2_process = None
            self._append_event(
                "nav2_ready", "An active Nav2 stack is using the same map. Reusing it.")
            return
        if not node_missing:
            if self._lifecycle_state(planner_probe) is None:
                raise RuntimeError(f"Unrecognized Nav2 lifecycle response: {planner_state!r}. "
                                   "The existing stack was left untouched.")
            raise RuntimeError(
                "Nav2 exists in this ROS domain but is not active. Stop or recover "
                "that stack before starting another one.")
        command = ["ros2", "launch", "khronos_nav2_bridge", "hospital_nav2.launch.py",
                   f"map:={map_yaml}", f"params_file:={self.params_path}",
                   f"start_x:={pose.get('x', 0.0)}", f"start_y:={pose.get('y', 0.0)}",
                   f"start_yaw:={pose.get('yaw', 0.0)}"]
        self._spawn_owned("nav2_process",
            command, cwd=ROOT, env=self._ros_env(), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        threading.Thread(target=self._read_logs,
                         args=(self.nav2_process, "nav2"), daemon=True).start()
        deadline = time.monotonic() + 60
        last_probe = ""
        while time.monotonic() < deadline:
            if self.nav2_process.poll() is not None:
                raise RuntimeError(f"Nav2 launch exited with code {self.nav2_process.returncode}.")
            try:
                probe = subprocess.run(["ros2", "lifecycle", "get", "/planner_server"],
                                       capture_output=True, text=True, timeout=3,
                                       env=self._ros_env())
                last_probe = (probe.stdout + probe.stderr).strip()
                if self._lifecycle_state(probe) == "active":
                    self._append_event("nav2_ready", "Nav2 is ready to calculate a route.")
                    return
            except subprocess.SubprocessError as error:
                last_probe = str(error)
            time.sleep(1)
        raise TimeoutError(f"Nav2 did not become active in 60 seconds: {last_probe}")

    def _read_logs(self, process, source):
        try:
            session_id = self.state.get("session_id") or "startup"
            log_path = self.output_dir / f"{session_id}-{source}.log"
            with log_path.open("a", encoding="utf-8") as stream:
                for line in process.stdout:
                    # Retain diagnostics without adding warnings to the UI timeline.
                    stream.write(line)
                    stream.flush()
        except (OSError, AttributeError, TypeError):
            return

    def _runtime_args(self, instruction):
        return argparse.Namespace(
            instruction=instruction, model=self.defaults.get("model"), db_dir=str(self.rag_db),
            bundle=str(self.bundle_path), defaults=str(self.defaults_path), output=None,
            intent_prompt_profile="hospital_taskchain_surface_v1",
            task_chain_backend=self.task_chain_backend, num_ctx=8192, num_predict=2048,
            request_timeout_s=360, floor_tolerance_m=None,
            robot_model=self.defaults.get("robot_model", "point"),
            ros_domain_id=self.ros_domain_id)

    def _plan_worker(self, instruction, selections):
        try:
            if self.stop_requested:
                return
            self._append_event("planning_request", f"Starting LLM1 + RAG + {self.task_chain_backend} task-chain planning.")
            result = plan_nav2.run_instruction(
                self._runtime_args(instruction),
                progress_callback=self._record_progress,
                interactive_selection=True, grounding_selections=selections,
                before_nav2_callback=self._start_nav2)
            if self.stop_requested:
                return
            output_path = self.output_dir / f"{self.state['session_id']}-plan.json"
            output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
            self.last_result = result
            final = self._final_summary(result)
            route_snapshot = RouteHistory.from_plan(
                result, self.state["session_id"],
                (result.get("provenance") or {}).get("map_bundle_id")).snapshot()
            self._set_state(final_summary=final, route_snapshot=route_snapshot)
            self._write_route_snapshot()
            pipeline = result.get("pipeline_result") or {}
            groups = build_candidate_groups(pipeline, self.canonical_graph or {})
            status = result.get("status", "system_error")
            summary = self._summarize_result(result, output_path)
            if status == "ambiguous" and groups:
                self._set_state(status="awaiting_selection", result=summary,
                                candidate_groups=groups, error=summary.get("error"))
                self._append_event("awaiting_selection", "Choose a destination to continue.",
                                   candidate_group_count=len(groups))
            else:
                self._set_state(status=status, result=summary,
                                candidate_groups=[], error=result.get("error"))
                self._append_event("result", self._result_message(result),
                                   output_path=str(output_path))
                if status != "planned":
                    self._stop_process("nav2_process")
        except Exception as error:
            if self.stop_requested:
                return
            message = self._friendly_error({"error": str(error)})
            self._stage((self.state.get("active_stage") or "intent"), "failed", message)
            final = self._final_summary({"status": "system_error", "pipeline_result": {
                "status": "valid" if (self.state.get("stage_status", {}).get("task_chain") or {}).get("status") == "succeeded" else "system_error"}})
            self._set_state(status="system_error", error=message, final_summary=final)
            self._append_event("system_error", str(error))
            self._stop_process("nav2_process")
        finally:
            self.job_lock.release()

    @staticmethod
    def _result_message(result):
        status = result.get("status")
        if status == "ambiguous":
            pipeline = result.get("pipeline_result") or {}
            intent = pipeline.get("intent") or {}
            if intent.get("action") == "ask_clarification":
                detail = intent.get("clarification") or intent.get("description") or pipeline.get("error")
                message = "LLM1 requested clarification."
                if detail:
                    message += f" {detail}"
                return message + " Please revise the instruction and click Plan Route again."
            return ("The destination could not be uniquely resolved. "
                    "Please provide a more specific description and click Plan Route again.")
        messages = {
            "planned": "Nav2 route planning succeeded.",
            "target_not_found": "RAG could not find a matching destination in the scene graph.",
            "unsupported": "This navigation request is outside the supported capabilities.",
            "system_error": "The pipeline encountered a system error.",
            "nav2_plan_failed": "Nav2 route planning failed. See the result for details.",
        }
        return messages.get(status, f"Pipeline finished: {status}")

    @staticmethod
    def _summarize_result(result, output_path):
        nav = result.get("nav2_plan") or {}
        route = nav.get("route_selection") or {}
        mission = result.get("mission_preparation") or {}
        return {"status": result.get("status"),
                "execution_performed": result.get("execution_performed", False),
                "error": (NavigationController._friendly_error(result)
                          if result.get("status") != "planned" else None), "model": result.get("model"),
                "task_chain_backend": result.get("task_chain_backend"),
                "rag_build_id": (result.get("provenance") or {}).get("rag_build_id"),
                "map_bundle_id": (result.get("provenance") or {}).get("map_bundle_id"),
                "path_length_m": route.get("total_path_length_m"),
                "transition_count": route.get("transition_count"),
                "mission_status": mission.get("status"), "output_path": str(output_path)}

    def _start_visualization(self, *, allow_stopped=False):
        session_id = self.state.get("session_id", "current")
        self._write_route_snapshot()
        if not self.labels_process or self.labels_process.poll() is not None:
            with (self.output_dir / f"{session_id}-map-labels.log").open("w") as log:
                self._spawn_owned("labels_process",
                    ["/usr/bin/python3", str(Path(__file__).with_name("map_labels.py")),
                     "--bundle", str(self.bundle_path), "--graph",
                     str(self.rag_db.parent / "canonical_graph_v2.json"),
                      "--route-snapshot", str(self._route_snapshot_path())],
                    allow_stopped=allow_stopped, cwd=ROOT, env=self._ros_env(), stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
        if not self.rviz_process or self.rviz_process.poll() is not None:
            self._spawn_owned("rviz_process",
                ["rviz2", "-d", str(self.rviz_path)], allow_stopped=allow_stopped, cwd=ROOT,
                env=self._ros_env(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)

    def start_route_view(self):
        if self.job_lock.locked():
            raise RuntimeError("Wait for the current task before reopening RViz.")
        if not (self.state.get("route_snapshot") or {}).get("segments"):
            raise RuntimeError("There is no retained selected route to display.")
        self._start_visualization(allow_stopped=True)

    def start_demo(self):
        with self.state_lock:
            if getattr(self, "_stopping", False):
                raise RuntimeError("Stop is still in progress. Wait for confirmation.")
            if self.state.get("status") == "stop_unconfirmed":
                raise RuntimeError("Confirm the previous navigation stop before restarting execution.")
            if not self.last_result or self.last_result.get("status") != "planned":
                raise RuntimeError("Start the RViz2 demo only after a route has been planned successfully.")
            if not self.job_lock.acquire(blocking=False):
                raise RuntimeError("Another task is already running.")
            self.stop_requested = False
            threading.Thread(target=self._demo_worker, daemon=True).start()

    def _demo_worker(self):
        try:
            if self.stop_requested:
                return
            mission = (self.last_result.get("mission_preparation") or {}).get("mission")
            if not isinstance(mission, dict):
                raise RuntimeError("The plan does not contain a navigation mission to demonstrate.")
            session_id = self.state["session_id"]
            mission_path = self.output_dir / f"{session_id}-demo-mission.json"
            result_path = self.output_dir / f"{session_id}-demo-result.json"
            mission_path.write_text(json.dumps(mission, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
            self._start_visualization()
            self._set_state(status="demo_running", demo_result=None)
            self._stage("execution", "running", "Navigation is running.")
            final = dict(self.state.get("final_summary") or {})
            final["navigation"] = {"status": "running", "message": "Navigation is running."}
            self._set_state(final_summary=final)
            self._append_event("rviz_started", "RViz2 is running the point-robot simulation.")
            if self.stop_requested:
                return
            execution_id = uuid.uuid4().hex
            ledger_path = self.output_dir / f"{execution_id}-goals.json"
            with self.state_lock:
                if self.stop_requested:
                    return
                self._demo_execution_id = execution_id
                self._demo_goal_ledger = ledger_path
            command = ["/usr/bin/python3", str(Path(__file__).with_name("navigation_runner.py")),
                       "--runner", str(RUNNER_SCRIPT), "--ledger", str(ledger_path),
                       "--execution-id", execution_id, "--", "--mission", str(mission_path), "--bundle", str(self.bundle_path),
                       "--output", str(result_path), "--robot-model",
                       str(self.defaults.get("robot_model", "point"))]
            self._spawn_owned("demo_process",
                command, cwd=ROOT, env=self._ros_env(), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
            threading.Thread(target=self._read_logs,
                             args=(self.demo_process, "demo"), daemon=True).start()
            event_cursor = 0
            while self.demo_process.poll() is None and not self.stop_requested:
                if result_path.is_file():
                    try:
                        live = json.loads(result_path.read_text(encoding="utf-8"))
                        events = live.get("events") or []
                        for event in events[event_cursor:]:
                            self._append_event("demo_step", self._demo_event_message(event))
                        event_cursor = len(events)
                        execution = {
                            "status": live.get("status"),
                            "active_floor": live.get("active_floor"),
                            "reached_stops": live.get("reached_stops", []),
                            "latest_event": (self._demo_event_message(events[-1])
                                             if events else None),
                        }
                        outputs = dict(self.state.get("stage_outputs") or {})
                        outputs["execution"] = execution
                        self._set_state(demo_result=execution, stage_outputs=outputs)
                    except (OSError, json.JSONDecodeError):
                        pass
                time.sleep(0.4)
            return_code = self.demo_process.wait()
            if self.stop_requested:
                return
            demo_result = (json.loads(result_path.read_text(encoding="utf-8"))
                           if result_path.is_file() else
                           {"status": "failed", "error": "runner wrote no result"})
            outputs = dict(self.state.get("stage_outputs") or {})
            outputs["execution"] = {
                "status": demo_result.get("status"),
                "active_floor": demo_result.get("active_floor"),
                "reached_stops": demo_result.get("reached_stops", []),
                "failure_stage": demo_result.get("failure_stage"),
                "error": demo_result.get("error"),
            }
            outcome = self._navigation_outcome(demo_result, return_code, len(mission.get("stops") or []))
            final = dict(self.state.get("final_summary") or {})
            final["navigation"] = outcome
            self._stage("execution", outcome["status"], outcome["message"])
            self._set_state(status="demo_complete" if outcome["status"] == "succeeded" else "demo_failed",
                            demo_result=demo_result, stage_outputs=outputs, final_summary=final,
                            error=None if outcome["status"] == "succeeded" else outcome["message"])
            self._append_event("demo_complete" if outcome["status"] == "succeeded" else "demo_failed",
                               outcome["message"])
        except Exception as error:
            if self.stop_requested:
                return
            message = self._friendly_error({"error": str(error)})
            self._stage("execution", "failed", message)
            final = dict(self.state.get("final_summary") or {})
            final["navigation"] = {"status": "failed", "message": message}
            self._set_state(status="demo_failed", error=message, final_summary=final)
            self._append_event("demo_error", str(error))
        finally:
            self.job_lock.release()

    @staticmethod
    def _demo_event_message(event):
        kind = event.get("kind", "simulation")
        transition = event.get("transition_id")
        floor = event.get("floor_id")
        result = event.get("result") or {}
        status = result.get("status")
        if kind == "navigate_to_entry":
            return f"Navigating to floor transition {transition} (Floor {floor}, {status})."
        if kind == "load_map":
            return f"Loading map for Floor {floor} ({status})."
        if kind == "set_pose":
            return f"Relocalizing on Floor {floor} ({status})."
        if kind == "floor_transition":
            return f"Floor transition {transition} complete ({status})."
        if kind == "plan_stop":
            length = result.get("path_length_m")
            return f"Destination route planned on Floor {floor} (about {length} m)."
        if kind == "navigate_to_stop":
            return f"Destination navigation result: {status} (Floor {floor})."
        return f"Simulation step {kind}: {status or 'in progress'}."

    def _stop_process(self, attr):
        process = getattr(self, attr, None)
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=8)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                pass

    def _route_snapshot_path(self):
        return self.output_dir / (str(self.state.get("session_id") or "current") + "-route.json")

    def _write_route_snapshot(self):
        target = self._route_snapshot_path()
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state.get("route_snapshot") or
                                        {"segments": []}, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(target)

    def clear_display(self):
        if self.job_lock.locked():
            raise RuntimeError("Stop the running task before clearing the display.")
        if self.state.get("status") == "stop_unconfirmed":
            raise RuntimeError("Navigation stop is unconfirmed. Resolve it before clearing.")
        with self.state_lock:
            if getattr(self, "_stopping", False):
                raise RuntimeError("Stop is still in progress.")
            self.stop_requested = False
            self.state.update(status="idle", stage_outputs={}, stage_status={}, final_summary={},
                              route_snapshot=None, result=None, demo_result=None, error=None,
                              events=[], stage_message="Display cleared.")
            self.last_result = None
        self._write_route_snapshot()
        for name in ("labels_process", "rviz_process"):
            self._stop_process(name)

    def _cancel_navigation(self):
        # Only a stopped GUI runner's exact UUID ledger proves goal ownership.
        process = self.demo_process
        ledger_path = getattr(self, "_demo_goal_ledger", None)
        execution_id = getattr(self, "_demo_execution_id", None)
        if process is None or process.poll() is None or not ledger_path or not execution_id:
            return {"confirmed": False}
        try:
            ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
            if (ledger.get("execution_id") != execution_id or ledger.get("client_pid") != process.pid
                    or ledger.get("action_name") != "/navigate_to_pose"):
                return {"confirmed": False}
            raw_ids = ledger.get("goal_ids")
            if not isinstance(raw_ids, list) or not all(isinstance(goal, str) for goal in raw_ids):
                return {"confirmed": False}
            goal_ids = [uuid.UUID(goal).hex for goal in raw_ids]
            if any(not uuid.UUID(goal).int for goal in goal_ids):
                return {"confirmed": False}
            if not goal_ids:
                return {"confirmed": True}  # Recorded runner exited before sending any motion goal.
            command = ["/usr/bin/python3", str(Path(__file__).with_name("navigation_cancel.py"))]
            for goal in dict.fromkeys(goal_ids):
                command.extend(["--goal-id", goal])
            result = subprocess.run(command, env=self._ros_env(), capture_output=True,
                                    text=True, timeout=18 + 3 * len(goal_ids))
            if result.returncode != 0:
                return {"confirmed": False}
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (OSError, TypeError, ValueError, IndexError, subprocess.TimeoutExpired):
            return {"confirmed": False}

    def stop(self):
        with self.state_lock:
            if getattr(self, "_stopping", False):
                raise RuntimeError("Stop is already in progress.")
            self._stopping = True
        try:
            with self.state_lock:
                self.stop_requested = True
                owned_nav2 = self.nav2_process
                owned_was_alive = owned_nav2 is not None and owned_nav2.poll() is None
                active = self.demo_process is not None and self.demo_process.poll() is None
                needs_cancel = active or self.state.get("status") == "stop_unconfirmed"
            # Prevent this client from submitting a subsequent goal before reading its ledger.
            if active:
                self._stop_process("demo_process")
            confirmed = not needs_cancel
            if needs_cancel:
                confirmed = self._cancel_navigation().get("confirmed") is True
            for name in ("rviz_process", "labels_process", "nav2_process"):
                self._stop_process(name)
            if active and not confirmed and owned_was_alive:
                confirmed = owned_nav2.poll() is not None
            with self.state_lock:
                final = dict(self.state.get("final_summary") or {})
                if needs_cancel and (final.get("navigation") or {}).get("status") != "succeeded":
                    final["navigation"] = {"status": "cancelled" if confirmed else "stop_unconfirmed",
                        "message": "Navigation was stopped." if confirmed else
                        "Navigation stop could not be confirmed. Do not start another task."}
                self.state.update(status="stopped" if confirmed else "stop_unconfirmed",
                                  candidate_groups=[], stage_outputs={}, demo_result=None,
                                  final_summary=final,
                                  error=None if confirmed else final["navigation"]["message"])
            self._append_event("stopped", "Simulation stopped; selected route and final summary retained."
                               if confirmed else "Navigation stop is unconfirmed.")
        finally:
            with self.state_lock:
                self._stopping = False


class NavigationUiHandler(BaseHTTPRequestHandler):
    controller: NavigationController

    def log_message(self, format_string, *args):
        return

    def _json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if urlparse(self.path).path == "/api/route":
            self._json(200, self.controller.snapshot().get("route_snapshot") or {"segments": []})
            return
        if urlparse(self.path).path == "/api/state":
            state = self.controller.snapshot()
            state["startup_error"] = self.controller.startup_error
            state["rviz_desktop_port"] = os.environ.get("HOSPITAL_RVIZ_WEB_PORT")
            self._json(200, state)
            return
        if urlparse(self.path).path != "/":
            self._json(404, {"error": "not found"})
            return
        payload = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_REQUEST_BYTES:
                self._json(413, {"error": "request too large"})
                return
            data = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
            if path == "/api/clear":
                self.controller.clear_display()
            elif path == "/api/view-route":
                self.controller.start_route_view()
            elif path == "/api/plan":
                self.controller.start_plan(data.get("instruction"))
            elif path == "/api/select":
                state = self.controller.snapshot()
                if state.get("status") != "awaiting_selection":
                    raise RuntimeError("There is no ambiguous destination awaiting selection.")
                self.controller.start_plan(state["instruction"], data.get("selections"))
            elif path == "/api/rag-profile":
                self.controller.select_rag_profile(data.get("profile_id"))
            elif path == "/api/rag-profiles/refresh":
                discovered = self.controller.refresh_rag_profiles(force=True)
                self._json(200, {"discovered": discovered,
                                 "error": self.controller.discovery_error})
                return
            elif path == "/api/demo":
                self.controller.start_demo()
            elif path == "/api/stop":
                self.controller.stop()
            else:
                self._json(404, {"error": "not found"})
                return
            self._json(202, {"status": "accepted"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})


def _open_browser(url: str):
    cmd = shutil.which("wslview")
    if cmd:
        try:
            subprocess.Popen([cmd, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    cmd = shutil.which("cmd.exe")
    if not cmd:
        for windows_cmd in (Path("/mnt/c/Windows/System32/cmd.exe"),
                            Path("/mnt/c/Windows/system32/cmd.exe")):
            if windows_cmd.is_file():
                cmd = str(windows_cmd)
                break
    if cmd:
        try:
            subprocess.run([cmd, "/c", "start", "", url], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    print(f"Open this URL in a browser: {url}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    controller = NavigationController()
    handler = type("BoundNavigationUiHandler", (NavigationUiHandler,), {})
    handler.controller = controller
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}"
    print(f"Navigation UI listening at {url}", flush=True)
    if not controller.startup_error:
        threading.Thread(target=controller.refresh_rag_profiles,
                         kwargs={"force": True}, daemon=True).start()
    if controller.startup_error:
        print(f"Run profile not ready: {controller.startup_error}", flush=True)
    if args.open_browser:
        threading.Timer(1.0, _open_browser, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.stop()


if __name__ == "__main__":
    main()
