#!/usr/bin/env python3
"""Local browser UI for interactive Hospital navigation planning and simulation."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
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


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hospital Navigation Console</title>
<style>
:root{color-scheme:light;--ink:#17212b;--muted:#617080;--line:#dce3ea;--blue:#1463d5;--soft:#f4f7fb;--green:#16784a;--red:#ad2f35}
*{box-sizing:border-box}body{margin:0;background:#edf2f7;color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1500px;margin:28px auto;padding:0 18px}.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.header h1{font-size:24px;margin:0}.tag{border:1px solid #c6d4e3;border-radius:999px;padding:5px 12px;color:#31475e;background:white;font-size:13px}
.grid{display:grid;grid-template-columns:minmax(250px,.95fr) minmax(360px,1.2fr) minmax(260px,.9fr);gap:14px}.panel{background:white;border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:0 5px 18px #19324b0a}.panel h2{font-size:17px;margin:0 0 12px}.muted{color:var(--muted);font-size:13px}.notice{padding:11px 13px;border-radius:9px;background:#eff6ff;color:#174a82;margin:12px 0}.warning{background:#fff4e7;color:#874a00}.error{background:#fff0f0;color:#94252b}.success{background:#eaf7ef;color:#145d38}
textarea,select{width:100%;border:1px solid #b8c5d2;border-radius:9px;padding:10px;font:inherit;background:white}select{margin:4px 0 8px}.profile-selector{margin:16px 0}.profile-selector label{display:block;font-weight:650;margin-bottom:4px}textarea{min-height:112px;resize:vertical;border:1px solid #b8c5d2;border-radius:9px;padding:12px;font:inherit}button{border:0;border-radius:9px;padding:10px 15px;background:var(--blue);color:white;font:600 14px system-ui;cursor:pointer}button.secondary{background:#e7edf4;color:#24384c}button:disabled{opacity:.5;cursor:default}.row{display:flex;gap:10px;align-items:center;margin-top:12px}.status{font-weight:700}.events{list-style:none;padding:0;margin:0;max-height:430px;overflow:auto}.events li{border-left:2px solid #bdd0e3;margin:0 0 0 5px;padding:0 0 14px 13px}.events time{color:#7a8794;font:12px ui-monospace,monospace;margin-right:8px}.events .log{font-size:12px;color:#647180;overflow-wrap:anywhere}.candidate-group{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}.option{display:flex;gap:10px;border:1px solid var(--line);border-radius:10px;padding:12px;margin:9px 0;cursor:pointer}.option:has(input:checked){border-color:var(--blue);background:#f1f6ff}.option input{margin-top:4px}.option strong{display:block}.stage-column{min-width:0}.stage-card{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}.stage-card h3{font-size:13px;margin:0 0 5px;color:#43566a}.stage-output{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;border-radius:8px;padding:9px;font:12px/1.4 ui-monospace,monospace;max-height:165px;overflow:auto;color:#263748}.result{white-space:pre-wrap;background:#f5f7fa;border-radius:9px;padding:12px;font:13px ui-monospace,monospace;max-height:300px;overflow:auto}.footer{margin:16px 2px;color:var(--muted);font-size:12px}@media(max-width:1120px){.grid{grid-template-columns:minmax(240px,.8fr) minmax(350px,1.2fr)}.stage-column{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.stage-card{min-width:0}}@media(max-width:760px){.grid{grid-template-columns:1fr}.stage-column{display:block}.header{align-items:flex-start;gap:10px;flex-direction:column}}
</style></head><body><main>
<header class="header"><h1>Hospital Navigation Console</h1><span class="tag">WSL · Point Robot Simulation · ROS_DOMAIN_ID 79</span></header>
<div id="startup" class="notice" hidden></div>
<div class="grid"><section class="panel stage-column"><h2>Stage Outputs</h2><article class="stage-card"><h3>LLM1 Intent</h3><pre id="stage-intent" class="stage-output">Waiting for input</pre></article><article class="stage-card"><h3>RAG Grounding</h3><pre id="stage-rag" class="stage-output">Waiting for input</pre></article><article class="stage-card"><h3>Task Chain</h3><pre id="stage-mission" class="stage-output">Waiting for input</pre></article><article class="stage-card"><h3>Mission</h3><pre id="stage-mission-build" class="stage-output">Waiting for input</pre></article><article class="stage-card"><h3>Nav2 Route</h3><pre id="stage-nav2" class="stage-output">Waiting for input</pre></article><article class="stage-card"><h3>RViz Simulation</h3><pre id="stage-execution" class="stage-output">Run after route planning succeeds</pre></article></section><section class="panel"><h2>Navigation Request</h2><div class="muted">Enter a natural-language destination. If multiple places match, choose one below. After planning succeeds, you can run the planar kinematic simulation in RViz2.</div>
<div class="profile-selector"><label for="rag-profile">RAG index</label><select id="rag-profile" aria-label="RAG index"></select><div class="muted">New run profiles are detected automatically. Changing the RAG clears the previous plan and stage outputs.</div><div class="row"><button id="refresh-rags" class="secondary">Refresh RAG list</button><span id="rag-refresh-status" class="muted">Scans configured run folders every 15 seconds.</span></div></div>
<textarea id="instruction" placeholder="e.g., Take me to a room with a bed"></textarea><div class="row"><button id="plan">Plan Route</button><span id="status" class="status">Ready</span></div>
<div id="message" class="notice" hidden></div><div id="choices"></div><div id="result"></div>
<div class="row"><button id="demo" hidden>Run Navigation in RViz2</button><button id="stop" class="secondary">Stop Simulation</button></div>
</section><section class="panel"><h2>Progress</h2><ol id="events" class="events"></ol></section></div>
<p class="footer">This demo uses an isolated ROS domain and a point-robot kinematic model. It does not control a physical robot or validate ANYmal hardware safety.</p>
<script>
const $=id=>document.getElementById(id);let timer=null,lastEventCount=0;
function post(path,data={}){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)}).then(async r=>{let v=await r.json();if(!r.ok)throw new Error(v.error||"request failed");return v})}
let candidateGroupsKey=null;
function showMessage(text,kind="notice"){const el=$("message");el.className=kind;el.textContent=text;el.hidden=!text}
function renderChoices(groups){const root=$("choices");const nextKey=JSON.stringify(groups||[]);if(nextKey===candidateGroupsKey)return;candidateGroupsKey=nextKey;root.replaceChildren();if(!groups||!groups.length)return;
 for(const group of groups){const box=document.createElement("div");box.className="candidate-group";const h=document.createElement("h2");h.textContent=`Choose a destination for: ${group.reference_text}`;box.append(h);
  for(const option of group.options){const label=document.createElement("label");label.className="option";const radio=document.createElement("input");radio.type="radio";radio.name=group.reference_key;radio.value=option.node_id;const body=document.createElement("span");const strong=document.createElement("strong");strong.textContent=option.title;const details=document.createElement("span");details.className="muted";let parts=[option.node_type,option.floor_id===null?"Floor unknown":`F${option.floor_id}`];if(option.evidence?.length)parts.push(`Contains: ${option.evidence.join(", ")}`);parts.push(`ID ${option.node_id}`);details.textContent=parts.join(" · ");body.append(strong,details);label.append(radio,body);box.append(label)}root.append(box)}
 const button=document.createElement("button");button.textContent="Confirm Selection and Continue";button.onclick=()=>{const selected={};for(const group of groups){const radio=root.querySelector(`input[name="${CSS.escape(group.reference_key)}"]:checked`);if(!radio){showMessage("Choose one place for each ambiguous destination.","warning");return}selected[group.reference_key]=radio.value}post("/api/select",{selections:selected}).then(()=>showMessage("Selection confirmed. Rechecking the destination and continuing the plan.")).catch(e=>showMessage(e.message,"error"))};root.append(button)}
function renderStageOutputs(s){const o=s.stage_outputs||{},items=[["intent","stage-intent","Waiting for input"],["rag","stage-rag","Waiting for input"],["task_chain","stage-mission","Waiting for input"],["mission","stage-mission-build","Waiting for input"],["nav2","stage-nav2","Waiting for input"],["execution","stage-execution","Run after route planning succeeds"]];for(const [key,id,fallback] of items){const el=$(id);el.textContent=Object.prototype.hasOwnProperty.call(o,key)?JSON.stringify(o[key],null,2):fallback}}
let ragProfileOptionsKey=null;
function renderRagProfiles(s){const options=s.rag_profiles||[],key=JSON.stringify(options);const select=$("rag-profile");if(key!==ragProfileOptionsKey){ragProfileOptionsKey=key;select.replaceChildren();for(const profile of options){const option=document.createElement("option");option.value=profile.id;option.textContent=profile.label;select.append(option)}}if(s.selected_rag_profile_id)select.value=s.selected_rag_profile_id}
function render(s){renderStageOutputs(s);renderRagProfiles(s);const banner=$("startup");if(s.startup_error){banner.hidden=false;banner.className="notice error";banner.textContent=s.startup_error}else if(s.rag_build_id){banner.hidden=false;banner.className="notice";banner.textContent="RAG "+s.rag_build_id.slice(0,12)+" · "+s.rag_review_badge}$("status").textContent=s.stage_message||s.status||"Ready";$("plan").disabled=["running","demo_running"].includes(s.status);$("rag-profile").disabled=["running","demo_running"].includes(s.status);$("refresh-rags").disabled=["running","demo_running"].includes(s.status);$("demo").hidden=s.status!=="planned";$("demo").disabled=s.status==="demo_running";if(s.error)showMessage(s.error,"error");else if(s.status==="planned")showMessage(`Route planned: ${s.result?.path_length_m??"?"} m, ${s.result?.transition_count??0} floor transition(s). You can start the RViz2 simulation.`,"success");else if(s.status==="awaiting_selection")showMessage("Multiple places match. Choose a destination.","warning");else if(s.status==="target_not_found")showMessage("RAG could not ground this destination in the scene graph. Rephrase the request or add semantic labels to the graph.","warning");renderChoices(s.candidate_groups);
 const list=$("events");list.replaceChildren();for(const e of (s.events||[])){const li=document.createElement("li"),time=document.createElement("time"),span=document.createElement("span");time.textContent=e.time||"";span.textContent=e.message||e.stage||"";li.append(time,span);list.append(li)}if(s.demo_result){const pre=document.createElement("pre");pre.className="result";pre.textContent=JSON.stringify({status:s.demo_result.status,reached_stops:s.demo_result.reached_stops,error:s.demo_result.error},null,2);$("result").replaceChildren(pre)}else if(s.result){const pre=document.createElement("pre");pre.className="result";pre.textContent=JSON.stringify(s.result,null,2);$("result").replaceChildren(pre)}list.scrollTop=list.scrollHeight}
async function poll(){try{render(await (await fetch("/api/state")).json())}catch(e){}timer=setTimeout(poll,700)}
$("instruction").addEventListener("input",()=>{candidateGroupsKey=null});
$("rag-profile").onchange=()=>{const select=$("rag-profile");select.disabled=true;post("/api/rag-profile",{profile_id:select.value}).then(()=>{candidateGroupsKey=null;$("choices").replaceChildren();showMessage("RAG profile changed. Previous results and outputs were cleared.","success")}).catch(e=>showMessage(e.message,"error")).finally(()=>{select.disabled=false})};
$("refresh-rags").onclick=()=>{const button=$("refresh-rags");button.disabled=true;$("rag-refresh-status").textContent="Scanning configured run folders...";post("/api/rag-profiles/refresh").then(result=>{$("rag-refresh-status").textContent=result.error||("Scan complete. "+result.discovered+" new profile(s) found.")}).catch(e=>{$("rag-refresh-status").textContent=e.message}).finally(()=>{button.disabled=false})};
$("plan").onclick=()=>{const instruction=$("instruction").value.trim();if(!instruction){showMessage("Enter a navigation request.","warning");return}post("/api/plan",{instruction}).then(()=>showMessage("Request submitted. Starting the simulation and analyzing the intent.")) .catch(e=>showMessage(e.message,"error"))};
$("demo").onclick=()=>post("/api/demo").then(()=>showMessage("RViz2 is running the point-robot simulation.","success")).catch(e=>showMessage(e.message,"error"));
$("stop").onclick=()=>post("/api/stop").then(()=>showMessage("Simulation stack stopped."));
fetch("/api/state").then(r=>r.json()).then(s=>{if(s.startup_error)showMessage(s.startup_error,"error");render(s)}).finally(poll);
</script></main></body></html>"""


class NavigationController:
    def __init__(self):
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
        self.state = {"status": "idle", "events": [], "candidate_groups": [],
                      "stage_outputs": {}, "result": None,
                      "demo_result": None, "error": None}
        self.last_result = None
        self.last_mission_path = None
        self.nav2_process = self.rviz_process = self.demo_process = None
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
            if self.stop_requested and stage != "stopped":
                return
            self.state["events"].append(event)
            self.state["events"] = self.state["events"][-120:]
            self.state["stage"] = stage
            self.state["stage_message"] = message

    @staticmethod
    def _compact_stage_output(value):
        if isinstance(value, dict):
            compact = {}
            for key, item in value.items():
                item = NavigationController._compact_stage_output(item)
                if item is None or item == "" or item == [] or item == {}:
                    continue
                compact[key] = item
            return compact
        if isinstance(value, list):
            return [item for item in (
                NavigationController._compact_stage_output(item) for item in value)
                if item is not None and item != "" and item != [] and item != {}]
        return value

    def _record_progress(self, event):
        if self.stop_requested:
            return
        stage = event.get("stage", "planner")
        message = PROGRESS_MESSAGES.get(
            stage, f"Processing: {stage.replace('_', ' ')}.")
        details = event.get("details") or {}
        key, value = None, None
        if stage == "intent_parsed":
            key, value = "intent", self._compact_stage_output(details.get("intent") or {})
        elif stage == "rag_grounded":
            key, value = "rag", details.get("grounding")
        elif stage == "plan_ready":
            key, value = "task_chain", {
                "status": details.get("status"),
                "task_plan": details.get("task_plan"),
                "grounded_task": details.get("grounded_task"),
            }
        elif stage == "mission_ready":
            mission_result = details.get("mission_result") or {}
            mission = mission_result.get("mission") or {}
            key, value = "mission", {
                "status": mission_result.get("status"),
                "task_id": mission.get("task_id"),
                "initial_floor": mission.get("initial_floor"),
                "stop_count": len(mission.get("stops") or []),
                "stop_floors": [stop.get("floor_id") for stop in mission.get("stops") or []],
            }
        elif stage == "plan_complete":
            plan_result = details.get("plan_result") or {}
            route = (plan_result.get("nav2_plan") or {}).get("route_selection") or {}
            key, value = "nav2", {
                "status": plan_result.get("status"),
                "path_length_m": route.get("total_path_length_m"),
                "transition_count": route.get("transition_count"),
                "execution_performed": plan_result.get("execution_performed", False),
            }
        elif stage == "pipeline_stopped":
            pipeline = details.get("pipeline_result") or {}
            key, value = "task_chain", {
                "status": pipeline.get("status"), "error": pipeline.get("error"),
                "validation_errors": pipeline.get("validation_errors"),
            }
        if key:
            with self.state_lock:
                if self.stop_requested:
                    return
                outputs = dict(self.state.get("stage_outputs") or {})
                outputs[key] = value
                self.state["stage_outputs"] = outputs
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
        if profile_id not in self.rag_profiles:
            raise ValueError("Unknown RAG profile.")
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError("Wait for the current planning or simulation task to finish.")
        try:
            if profile_id == self.selected_rag_profile_id:
                return
            self._activate_rag_profile(profile_id)
            self.stop_requested = False
            self.last_result = None
            self.last_mission_path = None
            self._set_state(status="idle", events=[], candidate_groups=[],
                            stage_outputs={}, result=None, demo_result=None, error=None)
            label = self.rag_profiles[profile_id]["label"]
            self._append_event("rag_profile_selected", f"Selected RAG profile: {label}.")
        finally:
            self.job_lock.release()

    def start_plan(self, instruction, selections=None):
        if self.startup_error:
            raise RuntimeError(self.startup_error)
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("Enter a navigation request.")
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError("A navigation planning request is already in progress.")
        self.stop_requested = False
        if selections is None:
            self.last_result = None
            self.last_mission_path = None
            self._set_state(status="running", session_id=uuid.uuid4().hex,
                            instruction=instruction.strip(), events=[], candidate_groups=[],
                            stage_outputs={}, result=None, demo_result=None, error=None)
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

    def _start_nav2(self):
        if self.nav2_process and self.nav2_process.poll() is None:
            return
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
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "Nav2 discovery took longer than 20 seconds. The existing stack was "
                "left untouched; wait briefly and retry.") from error
        planner_state = (planner_probe.stdout + planner_probe.stderr).strip().lower()
        if planner_probe.returncode != 0:
            raise RuntimeError(
                "ROS 2 Nav2 state check failed. Start the interface with "
                "experiment/navigation-ui-demo/run_demo.sh so the ROS Jazzy environment is loaded.")
        if "active" in planner_state:
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
            self._append_event(
                "nav2_ready", "An active Nav2 stack is using the same map. Reusing it.")
            return
        if "not found" not in planner_state:
            raise RuntimeError(
                "Nav2 exists in this ROS domain but is not active. Stop or recover "
                "that stack before starting another one.")
        command = ["ros2", "launch", "khronos_nav2_bridge", "hospital_nav2.launch.py",
                   f"map:={map_yaml}", f"params_file:={self.params_path}",
                   f"start_x:={pose.get('x', 0.0)}", f"start_y:={pose.get('y', 0.0)}",
                   f"start_yaw:={pose.get('yaw', 0.0)}"]
        self.nav2_process = subprocess.Popen(
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
                if probe.returncode == 0 and "active" in last_probe.lower():
                    self._append_event("nav2_ready", "Nav2 is ready to calculate a route.")
                    return
            except subprocess.SubprocessError as error:
                last_probe = str(error)
            time.sleep(1)
        raise TimeoutError(f"Nav2 did not become active in 60 seconds: {last_probe}")

    def _read_logs(self, process, source):
        try:
            for line in process.stdout:
                # Drain child output to prevent a full pipe; terminal diagnostics
                # stay out of the user-facing progress timeline.
                _ = line
        except Exception:
            return

    def _runtime_args(self, instruction):
        return argparse.Namespace(
            instruction=instruction, model=self.defaults.get("model"), db_dir=str(self.rag_db),
            bundle=str(self.bundle_path), defaults=str(self.defaults_path), output=None,
            intent_prompt_profile="hospital_taskchain_surface_v1",
            task_chain_backend="grounded_deterministic", num_ctx=4096, num_predict=512,
            request_timeout_s=360, floor_tolerance_m=None,
            robot_model=self.defaults.get("robot_model", "point"),
            ros_domain_id=self.ros_domain_id)

    def _plan_worker(self, instruction, selections):
        try:
            self._append_event("planning_request", "Starting the LLM1, RAG, and task-chain planning pipeline.")
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
            pipeline = result.get("pipeline_result") or {}
            groups = build_candidate_groups(pipeline, self.canonical_graph or {})
            status = result.get("status", "system_error")
            summary = self._summarize_result(result, output_path)
            if status == "ambiguous" and groups:
                self._set_state(status="awaiting_selection", result=summary,
                                candidate_groups=groups, error=result.get("error"))
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
            self._set_state(status="system_error", error=f"{type(error).__name__}: {error}")
            self._append_event("system_error", str(error))
            self._stop_process("nav2_process")
        finally:
            self.job_lock.release()

    @staticmethod
    def _result_message(result):
        status = result.get("status")
        messages = {
            "planned": "Nav2 route planning succeeded.",
            "target_not_found": "RAG could not find a matching destination in the scene graph.",
            "ambiguous": "Multiple destinations match. User selection is required.",
            "unsupported": "This navigation request is outside the supported capabilities.",
            "system_error": "The pipeline encountered a system error.",
        }
        return messages.get(status, f"Pipeline finished: {status}")

    @staticmethod
    def _summarize_result(result, output_path):
        nav = result.get("nav2_plan") or {}
        route = nav.get("route_selection") or {}
        mission = result.get("mission_preparation") or {}
        return {"status": result.get("status"),
                "execution_performed": result.get("execution_performed", False),
                "error": result.get("error"), "model": result.get("model"),
                "rag_build_id": (result.get("provenance") or {}).get("rag_build_id"),
                "map_bundle_id": (result.get("provenance") or {}).get("map_bundle_id"),
                "path_length_m": route.get("total_path_length_m"),
                "transition_count": route.get("transition_count"),
                "mission_status": mission.get("status"), "output_path": str(output_path)}

    def start_demo(self):
        if not self.last_result or self.last_result.get("status") != "planned":
            raise RuntimeError("Start the RViz2 demo only after a route has been planned successfully.")
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError("Another task is already running.")
        threading.Thread(target=self._demo_worker, daemon=True).start()

    def _demo_worker(self):
        try:
            mission = (self.last_result.get("mission_preparation") or {}).get("mission")
            if not isinstance(mission, dict):
                raise RuntimeError("The plan does not contain a navigation mission to demonstrate.")
            session_id = self.state["session_id"]
            mission_path = self.output_dir / f"{session_id}-demo-mission.json"
            result_path = self.output_dir / f"{session_id}-demo-result.json"
            mission_path.write_text(json.dumps(mission, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
            if not self.rviz_process or self.rviz_process.poll() is not None:
                self.rviz_process = subprocess.Popen(
                    ["rviz2", "-d", str(self.rviz_path)], cwd=ROOT,
                    env=self._ros_env(), stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
            self._set_state(status="demo_running", demo_result=None)
            self._append_event("rviz_started", "RViz2 is running the point-robot simulation.")
            command = ["/usr/bin/python3", str(RUNNER_SCRIPT),
                       "--mission", str(mission_path), "--bundle", str(self.bundle_path),
                       "--output", str(result_path), "--robot-model",
                       str(self.defaults.get("robot_model", "point"))]
            self.demo_process = subprocess.Popen(
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
            self._set_state(status="demo_complete" if return_code == 0 else "demo_failed",
                            demo_result=demo_result,
                            stage_outputs=outputs,
                            error=None if return_code == 0 else demo_result.get("error"))
            self._append_event("demo_complete", f"Simulation finished: {demo_result.get('status')}.",
                               output_path=str(result_path))
        except Exception as error:
            if self.stop_requested:
                return
            self._set_state(status="demo_failed", error=f"{type(error).__name__}: {error}")
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

    def stop(self):
        with self.state_lock:
            self.stop_requested = True
        for attr in ("demo_process", "rviz_process", "nav2_process"):
            self._stop_process(attr)
        self.last_result = None
        self.last_mission_path = None
        self._set_state(status="stopped", candidate_groups=[], stage_outputs={},
                        result=None, demo_result=None, error=None)
        self._append_event("stopped", "Nav2/RViz simulation processes stopped.")


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
        if urlparse(self.path).path == "/api/state":
            state = self.controller.snapshot()
            state["startup_error"] = self.controller.startup_error
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
            if path == "/api/plan":
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
