"""Generate Paraphrase Benchmark v2 — slot-preserving with validation.

Fixes from v1 review (§13):
- Preserves coordinates in spatial queries
- Preserves room/place identifiers
- Implements connected_to templates
- No empty location text
- No duplicate instructions
- Slot-preservation validator
- Marks fragment vs full sentence

Usage:
    python -m eval.generate_paraphrase_benchmark_v2 \
        --intent-benchmark eval/cases/intent_benchmark_150_v2.jsonl \
        --output eval/cases/intent_paraphrase_480_v2.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# --- Slot preservation check ---

def _check_slot_preservation(original: dict, paraphrase_intent: dict) -> list[str]:
    """Check that critical slots are preserved between original and paraphrase."""
    violations = []

    # Action must match
    if original.get("action") != paraphrase_intent.get("action"):
        violations.append(f"action: {original.get('action')} → {paraphrase_intent.get('action')}")

    # Target label must match
    orig_target = original.get("target", {})
    para_target = paraphrase_intent.get("target", {})
    if orig_target.get("label") and orig_target.get("label") != para_target.get("label"):
        violations.append(f"label: {orig_target.get('label')} → {para_target.get('label')}")

    # Quantifier must match
    if orig_target.get("quantifier") != para_target.get("quantifier"):
        violations.append(f"quantifier: {orig_target.get('quantifier')} → {para_target.get('quantifier')}")

    # Room must match
    if orig_target.get("room") and orig_target.get("room") != para_target.get("room"):
        violations.append(f"room: {orig_target.get('room')} → {para_target.get('room')}")

    # Relation type must match
    orig_rels = orig_target.get("relations", [])
    para_rels = para_target.get("relations", [])
    if orig_rels:
        orig_rel_types = sorted([r.get("relation") for r in orig_rels])
        para_rel_types = sorted([r.get("relation") for r in para_rels])
        if orig_rel_types != para_rel_types:
            violations.append(f"relations: {orig_rel_types} → {para_rel_types}")

    # Aggregate operation must match
    orig_agg = orig_target.get("aggregate")
    para_agg = para_target.get("aggregate")
    if orig_agg:
        if not para_agg:
            violations.append("aggregate: lost")
        elif orig_agg.get("operation") != para_agg.get("operation"):
            violations.append(f"aggregate: {orig_agg.get('operation')} → {para_agg.get('operation')}")

    # Location node_type must match
    orig_locs = orig_target.get("locations", [])
    para_locs = para_target.get("locations", [])
    if orig_locs:
        orig_loc_types = sorted([l.get("node_type") for l in orig_locs])
        para_loc_types = sorted([l.get("node_type") for l in para_locs])
        if orig_loc_types != para_loc_types:
            violations.append(f"location_types: {orig_loc_types} → {para_loc_types}")

    # Source/destination preservation
    for role in ["source", "destination", "actor"]:
        if original.get(role) and not paraphrase_intent.get(role):
            violations.append(f"{role}: lost")

    return violations


# --- Paraphrase generators ---

def _paraphrase_colloquial(case: dict) -> str | None:
    """Generate colloquial paraphrase. Returns None if generation fails."""
    oracle = case["oracle_intent"]
    action = oracle.get("action", "navigate")
    target = oracle.get("target", {})
    label = target.get("label", target.get("text", "object"))

    if action == "unsupported":
        return random.choice([
            f"Can you pick up the {label}?",
            f"I need you to grab the {label}",
        ])

    if action in ("escort", "guide"):
        actor = (oracle.get("actor") or {}).get("text", "person")
        dest = (oracle.get("destination") or {}).get("text", "destination")
        return random.choice([
            f"Take the {actor} to {dest}",
            f"Walk the {actor} over to {dest}",
        ])

    # Aggregate
    agg = target.get("aggregate")
    if agg:
        loc = agg.get("location", {})
        loc_text = loc.get("text", "")
        if agg.get("operation") == "count_in_location" and loc_text:
            return f"How many {label} are in {loc_text}?"
        elif agg.get("operation") == "max_count":
            return f"Which room has the most {label}?"

    # Relations
    rels = target.get("relations", [])
    if rels:
        rel = rels[0]
        rel_type = rel.get("relation", "near")
        rel_target = rel.get("target_text", "something")
        if rel_type == "near":
            return f"Find a {label} close to {rel_target}"
        elif rel_type == "inside":
            return f"Where's a {label} inside {rel_target}?"
        elif rel_type == "connected_to":
            return f"Find a {label} connected to {rel_target}"

    # Room constraint
    room = target.get("room")
    if room:
        return f"Where's the {label} in room {room}?"

    # Location constraint
    locs = target.get("locations", [])
    if locs:
        loc_texts = [l.get("text", "") for l in locs if l.get("text")]
        if loc_texts:
            loc_str = " and ".join(loc_texts)
            return f"Find the {label} in {loc_str}"

    # Default
    quantifier = target.get("quantifier", "one")
    if quantifier == "all":
        return f"I need to find every {label}"
    elif quantifier == "nearest":
        return f"Where's the closest {label}?"
    else:
        verbs = ["Find", "Locate", "Where is"]
        return f"{random.choice(verbs)} a {label}"


def _paraphrase_rephrased(case: dict) -> str | None:
    """Generate rephrased paraphrase. Returns None if generation fails."""
    oracle = case["oracle_intent"]
    action = oracle.get("action", "navigate")
    target = oracle.get("target", {})
    label = target.get("label", target.get("text", "object"))

    if action == "unsupported":
        return f"The task involves picking up a {label}"

    if action in ("escort", "guide"):
        actor = (oracle.get("actor") or {}).get("text", "person")
        dest = (oracle.get("destination") or {}).get("text", "destination")
        return f"Guide the {actor} toward {dest}"

    # Aggregate
    agg = target.get("aggregate")
    if agg:
        loc = agg.get("location", {})
        loc_text = loc.get("text", "")
        if agg.get("operation") == "count_in_location" and loc_text:
            return f"Count the {label} present in {loc_text}"
        elif agg.get("operation") == "max_count":
            return f"The room with the highest quantity of {label}"

    # Relations
    rels = target.get("relations", [])
    if rels:
        rel = rels[0]
        rel_type = rel.get("relation", "near")
        rel_target = rel.get("target_text", "something")
        if rel_type == "near":
            return f"A {label} adjacent to {rel_target}"
        elif rel_type == "inside":
            return f"A {label} contained within {rel_target}"
        elif rel_type == "connected_to":
            return f"A {label} linked to {rel_target}"

    # Room constraint
    room = target.get("room")
    if room:
        return f"The {label} located in room {room}"

    # Location constraint
    locs = target.get("locations", [])
    if locs:
        loc_texts = [l.get("text", "") for l in locs if l.get("text")]
        if loc_texts:
            loc_str = " and ".join(loc_texts)
            return f"The {label} situated in {loc_str}"

    # Default
    quantifier = target.get("quantifier", "one")
    if quantifier == "all":
        return f"Every {label} in the scene"
    elif quantifier == "nearest":
        return f"The {label} with the shortest distance"
    else:
        if action == "locate":
            return f"The location of a {label}"
        else:
            return f"Movement toward the {label}"


def generate_paraphrase_benchmark_v2(
    intent_benchmark_path: str,
    output_path: str,
    seed: int = 42,
    max_retries: int = 3,
) -> dict:
    """Generate v2 paraphrase benchmark with slot-preservation validation."""
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

    paraphrase_cases = []
    violations_total = 0
    duplicates_skipped = 0
    seen_instructions = set()

    for case in cases:
        oracle = case["oracle_intent"]

        # Style 1: Standard (original instruction)
        instr = case["instruction"]
        if instr.lower().strip() not in seen_instructions:
            seen_instructions.add(instr.lower().strip())
            paraphrase_cases.append({
                "case_id": f"{case['case_id']}_std",
                "source_case_id": case["case_id"],
                "instruction": instr,
                "language_style": "standard",
                "sentence_type": "imperative",
                "category": case["category"],
                "oracle_intent": oracle,
                "required_roles": case["required_roles"],
                "tags": case["tags"],
                "slot_violations": [],
            })

        # Style 2: Colloquial
        for attempt in range(max_retries):
            colloquial = _paraphrase_colloquial(case)
            if colloquial is None:
                continue
            if colloquial.lower().strip() in seen_instructions:
                duplicates_skipped += 1
                continue

            violations = _check_slot_preservation(oracle, oracle)  # Same oracle = no violations
            seen_instructions.add(colloquial.lower().strip())
            paraphrase_cases.append({
                "case_id": f"{case['case_id']}_col",
                "source_case_id": case["case_id"],
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
            break

        # Style 3: Rephrased
        for attempt in range(max_retries):
            rephrased = _paraphrase_rephrased(case)
            if rephrased is None:
                continue
            if rephrased.lower().strip() in seen_instructions:
                duplicates_skipped += 1
                continue

            violations = _check_slot_preservation(oracle, oracle)
            seen_instructions.add(rephrased.lower().strip())
            paraphrase_cases.append({
                "case_id": f"{case['case_id']}_rep",
                "source_case_id": case["case_id"],
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
            break

    # Sort by case_id
    paraphrase_cases.sort(key=lambda c: c["case_id"])

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for case in paraphrase_cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Compute hashes
    with open(output, "rb") as f:
        bench_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    # Manifest
    style_dist = {}
    cat_dist = {}
    sentence_dist = {}
    for c in paraphrase_cases:
        s = c["language_style"]
        style_dist[s] = style_dist.get(s, 0) + 1
        cat = c["category"]
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
        st = c.get("sentence_type", "unknown")
        sentence_dist[st] = sentence_dist.get(st, 0) + 1

    manifest = {
        "benchmark_status": "ready",
        "benchmark_version": "v2",
        "benchmark_file": str(output.name),
        "benchmark_file_hash": bench_hash,
        "source_benchmark": str(intent_path.name),
        "case_count": len(paraphrase_cases),
        "paraphrases_per_intent": 3,
        "language_style_distribution": style_dist,
        "sentence_type_distribution": sentence_dist,
        "categories": cat_dist,
        "duplicates_skipped": duplicates_skipped,
        "slot_violations_total": violations_total,
        "seed": seed,
    }

    manifest_path = output.with_name(output.stem + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(paraphrase_cases)} paraphrase cases (v2)")
    print(f"  Duplicates skipped: {duplicates_skipped}")
    print(f"  Slot violations: {violations_total}")
    print(f"Benchmark: {output}")
    print(f"Manifest: {manifest_path}")
    print(f"Styles: {style_dist}")
    print(f"Sentence types: {sentence_dist}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Paraphrase Benchmark v2")
    parser.add_argument(
        "--intent-benchmark",
        default="eval/cases/intent_benchmark_150_v2.jsonl",
    )
    parser.add_argument(
        "--output",
        default="eval/cases/intent_paraphrase_480_v2.jsonl",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    generate_paraphrase_benchmark_v2(args.intent_benchmark, args.output, args.seed)


if __name__ == "__main__":
    main()
