"""Generate Paraphrase Benchmark v3 — 480 cases with proper slot validation.

Fixes from v2 review:
- [P0 #20] 480/480 cases (every source intent produces 3 paraphrases)
- [P0 #21] Slot validator checks instruction text, NOT oracle self-comparison
- [P0 #22] Preserves source/destination in paraphrases
- [P0 #23] Preserves constraints in paraphrases
- [P0 #24] Preserves multi-step structure in paraphrases
- [P0 #25] Balanced style distribution: 160 standard + 160 colloquial + 160 rephrased

Usage:
    python -m eval.generate_paraphrase_benchmark_v3 \\
        --intent-benchmark eval/cases/intent_benchmark_160_v3.jsonl \\
        --output eval/cases/intent_paraphrase_480_v3.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "llm-rag" / "planner"))


# ---------------------------------------------------------------------------
# Slot preservation validator — fixes P0 #21 (no oracle self-comparison)
# ---------------------------------------------------------------------------

def _check_slot_preservation_from_instruction(
    original_oracle: dict,
    paraphrase_instruction: str,
) -> list[str]:
    """Check that critical slots from oracle are visible in the paraphrase instruction.

    This does NOT compare oracle vs oracle (P0 #21 fix).
    Instead, it checks that key entity names, room numbers, and constraint
    keywords appear in the rendered instruction text.
    """
    violations = []
    instr_lower = paraphrase_instruction.lower()

    # Check target label visibility
    target = original_oracle.get("target", {})
    if target:
        label = target.get("label", "")
        if label and label.lower() not in ("unknown", ""):
            if label.lower() not in instr_lower:
                violations.append(f"target_label '{label}' not in instruction")

    # Check room visibility
    if target:
        room = target.get("room")
        if room and room not in paraphrase_instruction:
            violations.append(f"room '{room}' not in instruction")

    # Check relation target visibility
    for rel in target.get("relations", []):
        rel_target = rel.get("target_text", "")
        if rel_target and rel_target.lower() not in instr_lower:
            violations.append(f"relation_target '{rel_target}' not in instruction")

    # Check source/destination visibility (P0 #22)
    for role in ["source", "destination"]:
        ref = original_oracle.get(role)
        if ref:
            text = ref.get("text", "")
            if text and text.lower() not in instr_lower:
                violations.append(f"{role} '{text}' not in instruction")

    # Check actor visibility for escort/guide
    actor = original_oracle.get("actor")
    if actor:
        actor_text = actor.get("text", "")
        if actor_text and actor_text.lower() not in instr_lower:
            # For escort/guide, "person" is acceptable even if oracle says "patient"
            if actor_text.lower() not in ("person", "patient", "someone"):
                violations.append(f"actor '{actor_text}' not in instruction")

    # Check constraint keywords (P0 #23)
    for constr in original_oracle.get("constraints", []):
        constr_type = constr.get("type", "")
        if constr_type == "avoid":
            if not any(w in instr_lower for w in ["avoid", "bypass", "skip", "without", "don't go near", "don't go through", "excluding", "staying away"]):
                violations.append("avoid constraint keyword missing")
        elif constr_type == "prefer":
            if not any(w in instr_lower for w in ["prefer", "shortest", "direct", "fastest", "quickest", "optimal", "efficient", "best path"]):
                violations.append("prefer constraint keyword missing")

    # Check multi-step visibility (P0 #24)
    steps = original_oracle.get("steps")
    if steps and len(steps) >= 2:
        # At least check that the second action verb is present
        second_action = steps[1].get("action", "")
        action_verbs = {
            "navigate": ["navigate", "go to", "move to", "head to", "proceed", "travel"],
            "escort": ["escort", "accompany", "take", "walk", "bring", "accompanied"],
            "guide": ["guide", "lead", "show", "direct"],
            "locate": ["find", "locate", "search"],
        }
        if second_action in action_verbs:
            if not any(v in instr_lower for v in action_verbs[second_action]):
                violations.append(f"step_1 action '{second_action}' not in instruction")

    return violations


# ---------------------------------------------------------------------------
# Paraphrase generators — with source/destination, constraint, multi-step support
# ---------------------------------------------------------------------------

def _paraphrase_colloquial(case: dict, attempt: int = 0) -> str | None:
    """Generate colloquial paraphrase. attempt parameter drives template variation."""
    oracle = case["oracle_intent"]
    action = oracle.get("action", "navigate")
    target = oracle.get("target", {})
    label = target.get("label", target.get("text", "object"))
    # Extract numeric index from case_id for deterministic but diverse selection
    case_id = case.get("case_id", "")
    import re
    m = re.search(r"(\d+)$", case_id)
    case_num = int(m.group(1)) if m else 0
    idx = (case_num + attempt * 3) % 100

    if action == "unsupported":
        templates = [
            f"Can you pick up the {label}?",
            f"I need you to grab the {label}",
            f"Would you get the {label} for me?",
            f"Hey, can you pick up that {label}?",
            f"I'd like you to grab the {label}",
            f"Could you get the {label} for me please?",
            f"Would you mind picking up the {label}?",
            f"Please grab the {label} for me",
        ]
        return templates[idx % len(templates)]

    # Multi-step (P0 #26: MUST be checked BEFORE escort/guide/source-destination)
    steps = oracle.get("steps")
    if steps and len(steps) >= 2:
        step1_target = (steps[0].get("target") or {}).get("label", "something")
        step2_dest = (steps[1].get("destination") or {}).get("text", "")
        step2_actor = (steps[1].get("actor") or {}).get("text", "")
        step2_constraints = steps[1].get("constraints", [])
        avoid_in_step2 = any(c.get("type") == "avoid" for c in step2_constraints)
        avoid_target = ""
        if avoid_in_step2:
            for c in step2_constraints:
                if c.get("type") == "avoid":
                    avoid_target = c.get("target_text", "")

        step2_action = steps[1].get("action", "navigate")
        if step2_action in ("escort", "guide") and step2_actor:
            # locate + escort/guide
            if step2_action == "escort":
                templates = [
                    f"Find the {step1_target} first, then escort the {step2_actor} to {step2_dest}",
                    f"Locate the {step1_target}, then take the {step2_actor} to {step2_dest}",
                    f"First get the {step1_target}, afterwards accompany the {step2_actor} to {step2_dest}",
                ]
            else:
                templates = [
                    f"Find the {step1_target} first, then guide the {step2_actor} to {step2_dest}",
                    f"Locate the {step1_target}, then lead the {step2_actor} to {step2_dest}",
                    f"First get the {step1_target}, afterwards show the {step2_actor} the way to {step2_dest}",
                ]
        elif step2_dest and avoid_target:
            # locate + navigate + avoid
            templates = [
                f"Find the {step1_target} first, then go to {step2_dest} avoiding {avoid_target}",
                f"Locate the {step1_target}, then navigate to {step2_dest} while bypassing {avoid_target}",
                f"First get the {step1_target}, afterwards head to {step2_dest} without going through {avoid_target}",
            ]
        elif step2_dest:
            # locate + navigate
            templates = [
                f"Find the {step1_target} first, then head to {step2_dest}",
                f"Locate the {step1_target}, then go to {step2_dest}",
                f"First get the {step1_target}, afterwards navigate to {step2_dest}",
                f"Find the {step1_target} and then move to {step2_dest}",
            ]
        else:
            templates = [
                f"Find the {step1_target} first",
                f"Locate the {step1_target} first",
            ]
        return templates[idx % len(templates)]

    # Escort/guide (only for non-multi-step)
    if action in ("escort", "guide"):
        actor = (oracle.get("actor") or {}).get("text", "person")
        dest = (oracle.get("destination") or {}).get("text", "destination")
        if action == "escort":
            templates = [
                f"Take the {actor} over to {dest}",
                f"Walk the {actor} to {dest}",
                f"Could you bring the {actor} to {dest}?",
                f"I need you to escort the {actor} to {dest}",
                f"Please take the {actor} to {dest}",
                f"Bring the {actor} over to {dest}",
                f"Can you walk the {actor} to {dest}?",
                f"Would you escort the {actor} to {dest}?",
            ]
        else:
            templates = [
                f"Show the {actor} the way to {dest}",
                f"Lead the {actor} over to {dest}",
                f"Could you direct the {actor} to {dest}?",
                f"Please guide the {actor} to {dest}",
                f"I need someone to show the {actor} the way to {dest}",
                f"Can you lead the {actor} to {dest}?",
                f"Would you guide the {actor} toward {dest}?",
                f"Help the {actor} find the way to {dest}",
            ]
        return templates[idx % len(templates)]

    # Source -> destination (only for non-multi-step)
    source = oracle.get("source")
    destination = oracle.get("destination")
    if source and destination:
        src_text = source.get("text", "start")
        dst_text = destination.get("text", "end")
        templates = [
            f"Go from {src_text} over to {dst_text}",
            f"Move from {src_text} to {dst_text}",
            f"Take me from {src_text} to {dst_text}",
            f"I need to go from {src_text} to {dst_text}",
            f"Travel from {src_text} to {dst_text}",
            f"Head from {src_text} toward {dst_text}",
            f"Navigate from {src_text} to {dst_text}",
            f"I want to go from {src_text} to {dst_text}",
        ]
        return templates[idx % len(templates)]

    # Aggregate
    agg = target.get("aggregate")
    if agg:
        loc = agg.get("location", {})
        loc_text = loc.get("text", "")
        if agg.get("operation") == "count_in_location" and loc_text:
            templates = [
                f"How many {label} are in {loc_text}?",
                f"Can you count the {label} in {loc_text}?",
                f"What's the total number of {label} in {loc_text}?",
                f"How many {label} does {loc_text} have?",
            ]
        elif agg.get("operation") == "max_count":
            templates = [
                f"Which room has the most {label}?",
                f"Where are the most {label} located?",
                f"Which area has the highest number of {label}?",
                f"Where can I find the most {label}?",
            ]
        else:
            templates = [f"Aggregate query about {label}"]
        return templates[idx % len(templates)]

    # Relations
    rels = target.get("relations", [])
    if rels:
        rel = rels[0]
        rel_type = rel.get("relation", "near")
        rel_target = rel.get("target_text", "something")
        if rel_type == "near":
            templates = [
                f"Find a {label} close to {rel_target}",
                f"Where's a {label} near {rel_target}?",
                f"I'm looking for a {label} that's near {rel_target}",
                f"Is there a {label} close by {rel_target}?",
                f"Can you find a {label} near {rel_target}?",
                f"Locate a {label} adjacent to {rel_target}",
            ]
        elif rel_type == "inside":
            templates = [
                f"Where's a {label} inside {rel_target}?",
                f"Find a {label} that's in {rel_target}",
                f"I need a {label} located inside {rel_target}",
                f"Can you find a {label} within {rel_target}?",
                f"Locate a {label} contained in {rel_target}",
            ]
        elif rel_type == "connected_to":
            templates = [
                f"Find a {label} connected to {rel_target}",
                f"Where's a {label} that links to {rel_target}?",
                f"I need a {label} connected with {rel_target}",
                f"Can you find a {label} linked to {rel_target}?",
            ]
        else:
            templates = [f"Find a {label} related to {rel_target}"]
        return templates[idx % len(templates)]

    # Multi-step (check BEFORE constraints to include destination + avoid)
    steps = oracle.get("steps")
    if steps and len(steps) >= 2:
        step1_target = (steps[0].get("target") or {}).get("label", "something")
        step2_dest = (steps[1].get("destination") or {}).get("text", "")
        step2_constraints = steps[1].get("constraints", [])
        avoid_in_step2 = any(c.get("type") == "avoid" for c in step2_constraints)
        avoid_target = ""
        if avoid_in_step2:
            for c in step2_constraints:
                if c.get("type") == "avoid":
                    avoid_target = c.get("target_text", "")

        if step2_dest and avoid_target:
            templates = [
                f"Find the {step1_target} first, then go to {step2_dest} avoiding {avoid_target}",
                f"Locate the {step1_target}, then navigate to {step2_dest} while bypassing {avoid_target}",
                f"First get the {step1_target}, afterwards head to {step2_dest} without going through {avoid_target}",
            ]
        elif step2_dest:
            templates = [
                f"Find the {step1_target} first, then head to {step2_dest}",
                f"Locate the {step1_target}, then go to {step2_dest}",
                f"First get the {step1_target}, afterwards navigate to {step2_dest}",
                f"Find the {step1_target} and then move to {step2_dest}",
            ]
        else:
            templates = [
                f"Find the {step1_target} first",
                f"Locate the {step1_target} first",
            ]
        return templates[idx % len(templates)]

    # Constraints (only for non-multi-step cases)
    constraints = oracle.get("constraints", [])
    has_avoid = any(c.get("type") == "avoid" for c in constraints)
    has_prefer = any(c.get("type") == "prefer" for c in constraints)
    if has_avoid:
        avoid_targets = [c.get("target_text", "") for c in constraints if c.get("type") == "avoid"]
        avoid_str = " and ".join(t for t in avoid_targets if t)
        if avoid_str:
            templates = [
                f"Go to the {label} but don't go near {avoid_str}",
                f"Navigate to the {label} without going through {avoid_str}",
                f"Get to the {label} but avoid {avoid_str}",
                f"Reach the {label} while bypassing {avoid_str}",
            ]
        else:
            templates = [
                f"Get to the {label} but avoid the bad areas",
                f"Navigate to the {label} while staying away from restricted zones",
            ]
        return templates[idx % len(templates)]
    if has_prefer:
        templates = [
            f"Head to the {label}, take the quickest way",
            f"Go to the {label} via the shortest path",
            f"Navigate to the {label} using the most direct route",
            f"Move to the {label} by the fastest route",
        ]
        return templates[idx % len(templates)]

    # Room constraint
    room = target.get("room")
    if room:
        templates = [
            f"Where's the {label} in room {room}?",
            f"Find the {label} that's in room {room}",
            f"I need the {label} located in room {room}",
            f"Can you find the {label} in room {room}?",
        ]
        return templates[idx % len(templates)]

    # Location constraint
    locs = target.get("locations", [])
    if locs:
        loc_texts = [l.get("text", "") for l in locs if l.get("text")]
        if loc_texts:
            loc_str = " and ".join(loc_texts)
            templates = [
                f"Find the {label} in {loc_str}",
                f"Where's the {label} located in {loc_str}?",
                f"I need the {label} that's in {loc_str}",
                f"Can you locate the {label} in {loc_str}?",
            ]
            return templates[idx % len(templates)]

    # Default — large template pool for locate/navigate with same label
    quantifier = target.get("quantifier", "one")
    if quantifier == "all":
        templates = [
            f"I need to find every {label}",
            f"Show me all the {label}",
            f"Where are all the {label}?",
            f"Can you find every {label}?",
            f"List all the {label} for me",
            f"I want to see every {label}",
            f"Get me all the {label}",
            f"Locate every {label} in the area",
            f"Where can I find all the {label}?",
            f"I'm looking for every {label}",
            f"Can you show me where all the {label} are?",
            f"Find every single {label}",
            f"Tell me where all the {label} are",
            f"Point me to all the {label}",
            f"I need all the {label} identified",
            f"Where are the {label} located?",
            f"Find all instances of {label}",
            f"Show every {label} in the scene",
            f"Can you locate all the {label}?",
            f"I want to know where all the {label} are",
        ]
    elif quantifier == "nearest":
        templates = [
            f"Where's the closest {label}?",
            f"Find the nearest {label}",
            f"I need the {label} that's closest",
            f"Which {label} is nearest?",
            f"Locate the {label} with the shortest distance",
            f"Show me the closest {label}",
            f"Take me to the nearest {label}",
            f"Where can I find the closest {label}?",
            f"I want the {label} that's nearest to me",
            f"Find the {label} at the shortest distance",
            f"Which {label} has the minimum distance?",
            f"Locate the closest available {label}",
            f"Show me where the nearest {label} is",
            f"I'm looking for the closest {label}",
            f"Can you find the nearest {label}?",
            f"Point me to the closest {label}",
            f"Where is the {label} with the least distance?",
            f"Find the {label} nearest to this position",
            f"Take me to the closest {label}",
            f"I need the {label} that is nearest",
        ]
    else:
        templates = [
            f"Find a {label}",
            f"Locate a {label}",
            f"Where is a {label}?",
            f"I'm looking for a {label}",
            f"Can you find a {label}?",
            f"Help me find a {label}",
            f"I need to locate a {label}",
            f"Could you find a {label} for me?",
            f"Would you locate a {label}?",
            f"Please find a {label}",
            f"I'm trying to find a {label}",
            f"Show me where a {label} is",
            f"Can you help me find a {label}?",
            f"Where can I find a {label}?",
            f"I'd like to find a {label}",
            f"Search for a {label} please",
            f"Any {label} around here?",
            f"Is there a {label} nearby?",
            f"Point me to a {label}",
            f"Take me to a {label}",
        ]
    return templates[idx % len(templates)]


def _paraphrase_rephrased(case: dict, attempt: int = 0) -> str | None:
    """Generate rephrased paraphrase with high diversity."""
    oracle = case["oracle_intent"]
    action = oracle.get("action", "navigate")
    target = oracle.get("target", {})
    label = target.get("label", target.get("text", "object"))
    case_id = case.get("case_id", "")
    import re
    m = re.search(r"(\d+)$", case_id)
    case_num = int(m.group(1)) if m else 0
    idx = (case_num * 5 + attempt * 7) % 100

    if action == "unsupported":
        templates = [
            f"The task involves picking up a {label}",
            f"This requires lifting a {label}",
            f"The operation is to grasp a {label}",
            f"Picking up a {label} is needed",
            f"This is a pick-up task for a {label}",
            f"The goal is to retrieve a {label}",
        ]
        return templates[idx % len(templates)]

    if action in ("escort", "guide"):
        actor = (oracle.get("actor") or {}).get("text", "person")
        dest = (oracle.get("destination") or {}).get("text", "destination")
        if action == "escort":
            templates = [
                f"The {actor} needs to be accompanied to {dest}",
                f"Escort the {actor} to reach {dest}",
                f"The {actor} should be taken to {dest}",
                f"Accompany the {actor} on the way to {dest}",
                f"The {actor} requires escort to {dest}",
                f"Bring the {actor} to {dest}",
            ]
        else:
            templates = [
                f"The {actor} should be guided toward {dest}",
                f"Guide the {actor} in the direction of {dest}",
                f"The {actor} needs directions to {dest}",
                f"Lead the {actor} along the path to {dest}",
                f"The {actor} requires guidance to {dest}",
                f"Show the {actor} the route to {dest}",
            ]
        return templates[idx % len(templates)]

    # Multi-step (P0 #26: MUST be first — before source/destination, escort/guide)
    steps = oracle.get("steps")
    if steps and len(steps) >= 2:
        step1_target = (steps[0].get("target") or {}).get("label", "something")
        step2_dest = (steps[1].get("destination") or {}).get("text", "")
        step2_actor = (steps[1].get("actor") or {}).get("text", "")
        step2_constraints = steps[1].get("constraints", [])
        avoid_in_step2 = any(c.get("type") == "avoid" for c in step2_constraints)
        avoid_target = ""
        if avoid_in_step2:
            for c in step2_constraints:
                if c.get("type") == "avoid":
                    avoid_target = c.get("target_text", "")

        step2_action = steps[1].get("action", "navigate")
        if step2_action in ("escort", "guide") and step2_actor:
            if step2_action == "escort":
                templates = [
                    f"Locate the {step1_target}, then escort the {step2_actor} to {step2_dest}",
                    f"Find the {step1_target} and subsequently accompany the {step2_actor} to {step2_dest}",
                    f"First identify the {step1_target}, then take the {step2_actor} to {step2_dest}",
                ]
            else:
                templates = [
                    f"Locate the {step1_target}, then guide the {step2_actor} to {step2_dest}",
                    f"Find the {step1_target} and subsequently lead the {step2_actor} to {step2_dest}",
                    f"First identify the {step1_target}, then show the {step2_actor} the way to {step2_dest}",
                ]
        elif step2_dest and avoid_target:
            templates = [
                f"Locate the {step1_target}, then proceed to {step2_dest} avoiding {avoid_target}",
                f"Find the {step1_target} and subsequently navigate to {step2_dest} while bypassing {avoid_target}",
                f"First identify the {step1_target}, then travel to {step2_dest} excluding {avoid_target}",
            ]
        elif step2_dest:
            templates = [
                f"Locate the {step1_target}, then proceed to {step2_dest}",
                f"Find the {step1_target} and subsequently navigate to {step2_dest}",
                f"First identify the {step1_target}, then travel to {step2_dest}",
                f"Search for the {step1_target}, then move to {step2_dest}",
            ]
        else:
            templates = [
                f"Locate the {step1_target} as the first step",
                f"The first step is to find the {step1_target}",
            ]
        return templates[idx % len(templates)]

    # Source -> destination (only for non-multi-step)
    source = oracle.get("source")
    destination = oracle.get("destination")
    if source and destination:
        src_text = source.get("text", "start")
        dst_text = destination.get("text", "end")
        templates = [
            f"Movement from {src_text} to {dst_text}",
            f"Travel originating at {src_text} and ending at {dst_text}",
            f"A route from {src_text} toward {dst_text}",
            f"Transit from {src_text} to reach {dst_text}",
            f"Navigation from {src_text} to {dst_text}",
            f"Path from {src_text} to {dst_text}",
        ]
        return templates[idx % len(templates)]

    # Aggregate
    agg = target.get("aggregate")
    if agg:
        loc = agg.get("location", {})
        loc_text = loc.get("text", "")
        if agg.get("operation") == "count_in_location" and loc_text:
            templates = [
                f"Count the {label} present in {loc_text}",
                f"The number of {label} within {loc_text}",
                f"Tally of {label} located in {loc_text}",
                f"Quantity of {label} in {loc_text}",
            ]
        elif agg.get("operation") == "max_count":
            templates = [
                f"The room with the highest quantity of {label}",
                f"Maximum count of {label} by room",
                f"The area containing the most {label}",
                f"Room having the greatest number of {label}",
            ]
        else:
            templates = [f"Aggregate analysis of {label}"]
        return templates[idx % len(templates)]

    # Relations
    rels = target.get("relations", [])
    if rels:
        rel = rels[0]
        rel_type = rel.get("relation", "near")
        rel_target = rel.get("target_text", "something")
        if rel_type == "near":
            templates = [
                f"A {label} adjacent to {rel_target}",
                f"A {label} in proximity to {rel_target}",
                f"A {label} positioned near {rel_target}",
                f"A {label} close to {rel_target}",
            ]
        elif rel_type == "inside":
            templates = [
                f"A {label} contained within {rel_target}",
                f"A {label} situated inside {rel_target}",
                f"A {label} enclosed by {rel_target}",
                f"A {label} found inside {rel_target}",
            ]
        elif rel_type == "connected_to":
            templates = [
                f"A {label} linked to {rel_target}",
                f"A {label} associated with {rel_target}",
                f"A {label} joined to {rel_target}",
                f"A {label} connected with {rel_target}",
            ]
        else:
            templates = [f"A {label} related to {rel_target}"]
        return templates[idx % len(templates)]
        step2_constraints = steps[1].get("constraints", [])
        avoid_in_step2 = any(c.get("type") == "avoid" for c in step2_constraints)
        avoid_target = ""
        if avoid_in_step2:
            for c in step2_constraints:
                if c.get("type") == "avoid":
                    avoid_target = c.get("target_text", "")

        if step2_dest and avoid_target:
            templates = [
                f"Locate the {step1_target}, then proceed to {step2_dest} avoiding {avoid_target}",
                f"Find the {step1_target} and subsequently navigate to {step2_dest} while bypassing {avoid_target}",
                f"First identify the {step1_target}, then travel to {step2_dest} excluding {avoid_target}",
            ]
        elif step2_dest:
            templates = [
                f"Locate the {step1_target}, then proceed to {step2_dest}",
                f"Find the {step1_target} and subsequently navigate to {step2_dest}",
                f"First identify the {step1_target}, then travel to {step2_dest}",
                f"Search for the {step1_target}, then move to {step2_dest}",
            ]
        else:
            templates = [
                f"Locate the {step1_target} as the first step",
                f"The first step is to find the {step1_target}",
            ]
        return templates[idx % len(templates)]

    # Constraints (only for non-multi-step cases)
    constraints = oracle.get("constraints", [])
    has_avoid = any(c.get("type") == "avoid" for c in constraints)
    has_prefer = any(c.get("type") == "prefer" for c in constraints)
    if has_avoid:
        avoid_targets = [c.get("target_text", "") for c in constraints if c.get("type") == "avoid"]
        avoid_str = " and ".join(t for t in avoid_targets if t)
        if avoid_str:
            templates = [
                f"Navigate to the {label} while avoiding {avoid_str}",
                f"Reach the {label} bypassing {avoid_str}",
                f"Proceed to the {label} excluding {avoid_str}",
                f"Go to the {label} without entering {avoid_str}",
            ]
        else:
            templates = [
                f"Navigate to the {label} with avoidance",
                f"Reach the {label} while avoiding restricted areas",
            ]
        return templates[idx % len(templates)]
    if has_prefer:
        templates = [
            f"Navigate to the {label} via the optimal route",
            f"Proceed to the {label} using the most efficient path",
            f"Reach the {label} by the shortest available route",
            f"Go to the {label} using the best path",
        ]
        return templates[idx % len(templates)]

    # Room constraint
    room = target.get("room")
    if room:
        templates = [
            f"The {label} located in room {room}",
            f"A {label} found within room {room}",
            f"The {label} residing in room {room}",
            f"A {label} in room {room}",
        ]
        return templates[idx % len(templates)]

    # Location constraint
    locs = target.get("locations", [])
    if locs:
        loc_texts = [l.get("text", "") for l in locs if l.get("text")]
        if loc_texts:
            loc_str = " and ".join(loc_texts)
            templates = [
                f"The {label} situated in {loc_str}",
                f"A {label} placed within {loc_str}",
                f"The {label} positioned at {loc_str}",
                f"A {label} at {loc_str}",
            ]
            return templates[idx % len(templates)]

    # Default — large template pool
    quantifier = target.get("quantifier", "one")
    if quantifier == "all":
        templates = [
            f"Every {label} in the scene",
            f"All instances of {label}",
            f"The complete set of {label}",
            f"Every available {label}",
            f"All {label} present in the environment",
            f"The full collection of {label}",
            f"The total inventory of {label}",
            f"All {label} within the space",
            f"The complete inventory of {label}",
            f"Each {label} in the environment",
            f"The entirety of {label}",
            f"All recognized {label}",
            f"The aggregate of all {label}",
            f"Every detected {label}",
            f"All {label} that exist",
            f"The full roster of {label}",
            f"Each available {label}",
            f"All {label} currently present",
            f"The total count of {label}",
            f"Every {label} on record",
        ]
    elif quantifier == "nearest":
        templates = [
            f"The {label} with the shortest distance",
            f"The closest available {label}",
            f"The {label} at minimum distance",
            f"The {label} nearest to current position",
            f"The {label} with the least distance",
            f"The {label} at the nearest point",
            f"The {label} with the smallest gap",
            f"The {label} positioned most closely",
            f"The {label} at the nearest location",
            f"The {label} with minimal separation",
            f"The closest reachable {label}",
            f"The {label} at the shortest range",
            f"The {label} with the tiniest offset",
            f"The most proximate {label}",
            f"The {label} nearest to the observer",
            f"The {label} at the closest range",
            f"The {label} with the smallest displacement",
            f"The nearest discoverable {label}",
            f"The {label} at the closest waypoint",
            f"The {label} with the least separation",
        ]
    else:
        if action == "locate":
            templates = [
                f"The location of a {label}",
                f"Position of a {label}",
                f"Where a {label} can be found",
                f"Finding a {label}",
                f"A {label} that exists in the scene",
                f"The presence of a {label}",
                f"A {label} within the environment",
                f"Identifying a {label}",
                f"The spatial position of a {label}",
                f"A {label} in the current view",
                f"Retrieving the location of a {label}",
                f"Query about a {label}",
            ]
        else:
            templates = [
                f"Movement toward the {label}",
                f"Navigation to the {label}",
                f"Proceeding in the direction of the {label}",
                f"Approaching the {label}",
                f"Transit to the {label}",
                f"Routing toward the {label}",
                f"Course set for the {label}",
                f"Direction of travel: the {label}",
            ]
    return templates[idx % len(templates)]


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_paraphrase_benchmark_v3(
    intent_benchmark_path: str,
    output_path: str,
    seed: int = 42,
    max_retries: int = 30,
) -> dict:
    """Generate v3 paraphrase benchmark with proper validation.

    Ensures 480/480 cases: every source intent → 3 paraphrases.
    """
    random.seed(seed)

    intent_path = Path(intent_benchmark_path)
    output = Path(output_path)

    # Load intent cases
    cases = []
    with open(intent_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    paraphrase_cases: list[dict] = []
    violations_total = 0
    duplicates_skipped = 0
    seen_instructions: set[str] = set()
    missing_sources: list[str] = []

    # Pass 1: Generate ALL standard styles first (priority)
    for case in cases:
        oracle = case["oracle_intent"]
        source_id = case["case_id"]
        instr = case["instruction"]
        instr_key = instr.lower().strip()
        if instr_key not in seen_instructions:
            seen_instructions.add(instr_key)
            violations = _check_slot_preservation_from_instruction(oracle, instr)
            # P0 #27: Detect sentence type properly
            if "?" in instr:
                stype = "question"
            elif instr.rstrip(".").startswith(("The ", "A ", "Every ", "All ", "Count ")):
                stype = "declarative"
            elif not instr[0].isupper():
                stype = "fragment"
            else:
                stype = "imperative"
            paraphrase_cases.append({
                "case_id": f"{source_id}_std",
                "source_case_id": source_id,
                "instruction": instr,
                "language_style": "standard",
                "sentence_type": stype,
                "category": case["category"],
                "oracle_intent": oracle,
                "required_roles": case["required_roles"],
                "tags": case["tags"],
                "slot_violations": violations,
            })
            violations_total += len(violations)

    # Pass 2: Generate colloquial and rephrased (avoid collisions with standards)
    for case in cases:
        oracle = case["oracle_intent"]
        source_id = case["case_id"]
        generated_count = 0

        # Colloquial
        for attempt in range(max_retries):
            colloquial = _paraphrase_colloquial(case, attempt=attempt)
            if colloquial is None:
                continue
            colloquial_key = colloquial.lower().strip()
            if colloquial_key in seen_instructions:
                duplicates_skipped += 1
                continue

            violations = _check_slot_preservation_from_instruction(oracle, colloquial)
            seen_instructions.add(colloquial_key)
            paraphrase_cases.append({
                "case_id": f"{source_id}_col",
                "source_case_id": source_id,
                "instruction": colloquial,
                "language_style": "colloquial",
                "sentence_type": "imperative" if "?" not in colloquial else "question",
                "category": case["category"],
                "oracle_intent": oracle,
                "required_roles": case["required_roles"],
                "tags": case["tags"],
                "slot_violations": violations,
            })
            violations_total += len(violations)
            generated_count += 1
            break

        # Rephrased
        for attempt in range(max_retries):
            rephrased = _paraphrase_rephrased(case, attempt=attempt)
            if rephrased is None:
                continue
            rephrased_key = rephrased.lower().strip()
            if rephrased_key in seen_instructions:
                duplicates_skipped += 1
                continue

            violations = _check_slot_preservation_from_instruction(oracle, rephrased)
            seen_instructions.add(rephrased_key)
            paraphrase_cases.append({
                "case_id": f"{source_id}_rep",
                "source_case_id": source_id,
                "instruction": rephrased,
                "language_style": "rephrased",
                "sentence_type": "fragment" if not rephrased[0].isupper() or rephrased.endswith(("object", "place", "room")) else "imperative",
                "category": case["category"],
                "oracle_intent": oracle,
                "required_roles": case["required_roles"],
                "tags": case["tags"],
                "slot_violations": violations,
            })
            violations_total += len(violations)
            generated_count += 1
            break

        if generated_count < 2:
            missing_sources.append(f"{source_id}: generated {generated_count}/2 (col+rep)")

    # Sort by case_id
    paraphrase_cases.sort(key=lambda c: c["case_id"])

    # Validation
    total_sources = len(cases)
    total_paraphrases = len(paraphrase_cases)
    style_dist: dict[str, int] = {}
    for c in paraphrase_cases:
        s = c["language_style"]
        style_dist[s] = style_dist.get(s, 0) + 1

    checks = {
        "total_480": (total_paraphrases == 480, f"Expected 480, got {total_paraphrases}"),
        "all_sources_covered": (len(missing_sources) == 0, f"{len(missing_sources)} sources incomplete"),
        "balanced_styles": (
            all(v >= 150 for v in style_dist.values()),
            f"Styles: {style_dist}"
        ),
        "zero_violations": (violations_total == 0, f"{violations_total} violations"),
    }

    all_passed = all(v[0] for v in checks.values())
    if not all_passed:
        print("VALIDATION FAILED:")
        for name, (passed, detail) in checks.items():
            if not passed:
                print(f"  FAIL {name}: {detail}")
    else:
        print("All validation checks PASSED")

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for case in paraphrase_cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Compute hashes
    with open(output, "rb") as f:
        bench_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    # Category distribution
    cat_dist: dict[str, int] = {}
    sentence_dist: dict[str, int] = {}
    for c in paraphrase_cases:
        cat = c["category"]
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
        st = c.get("sentence_type", "unknown")
        sentence_dist[st] = sentence_dist.get(st, 0) + 1

    manifest = {
        "benchmark_status": "development",
        "benchmark_version": "v3",
        "benchmark_file": str(output.name),
        "benchmark_file_hash": bench_hash,
        "source_benchmark": str(intent_path.name),
        "source_count": total_sources,
        "case_count": total_paraphrases,
        "paraphrases_per_intent": 3,
        "language_style_distribution": style_dist,
        "sentence_type_distribution": sentence_dist,
        "categories": cat_dist,
        "duplicates_skipped": duplicates_skipped,
        "slot_violations_total": violations_total,
        "missing_sources": missing_sources,
        "seed": seed,
        "validation": {k: {"passed": v[0], "detail": v[1]} for k, v in checks.items()},
    }

    manifest_path = output.with_name(output.stem + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {total_paraphrases} paraphrase cases (v3)")
    print(f"  Duplicates skipped: {duplicates_skipped}")
    print(f"  Slot violations: {violations_total}")
    print(f"  Missing sources: {len(missing_sources)}")
    print(f"Benchmark: {output}")
    print(f"Manifest: {manifest_path}")
    print(f"Styles: {style_dist}")

    for name, (passed, detail) in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Paraphrase Benchmark v3")
    parser.add_argument(
        "--intent-benchmark",
        default="eval/cases/intent_benchmark_160_v3.jsonl",
    )
    parser.add_argument(
        "--output",
        default="eval/cases/intent_paraphrase_480_v3.jsonl",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    generate_paraphrase_benchmark_v3(args.intent_benchmark, args.output, args.seed)


if __name__ == "__main__":
    main()
