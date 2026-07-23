"""Generate Natural-Language Paraphrase Benchmark.

Creates 3 paraphrases per intent from the controlled benchmark:
1. Standard (original from grounding benchmark)
2. Colloquial (casual/conversational)
3. Rephrased (different sentence structure)

Usage:
    python -m eval.generate_paraphrase_benchmark \
        --intent-benchmark eval/cases/intent_benchmark_100_v1.jsonl \
        --output eval/cases/intent_paraphrase_300_v1.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# --- Paraphrase templates ---

# Action verb mappings
NAVIGATE_SYNONYMS = ["Navigate to", "Go to", "Move to", "Head to", "Take me to"]
LOCATE_SYNONYMS = ["Find", "Locate", "Search for", "Look for", "Where is"]
ESCORT_SYNONYMS = ["Escort", "Guide", "Accompany", "Walk with"]
UNSUPPORTED_SYNONYMS = ["Pick up", "Grab", "Lift", "Carry"]

# Quantifier paraphrases
ALL_SYNONYMS = ["all", "every single", "each and every"]
NEAREST_SYNONYMS = ["nearest", "closest", "most nearby"]

# Location paraphrases
ROOM_PATTERNS = [
    "in room {room}",
    "in Room {room}",
    "located in room {room}",
    "that's in room {room}",
    "inside room {room}",
]
PLACE_PATTERNS = [
    "in place {place}",
    "at place {place}",
    "located at {place}",
    "that's at {place}",
]

# Relation paraphrases
NEAR_PATTERNS = [
    "near the {target}",
    "close to the {target}",
    "next to the {target}",
    "by the {target}",
    "beside the {target}",
]
INSIDE_PATTERNS = [
    "inside {target}",
    "in {target}",
    "within {target}",
    "that's inside {target}",
]
CONNECTED_PATTERNS = [
    "connected to {target}",
    "linked to {target}",
    "adjacent to {target}",
]

# Aggregate paraphrases
COUNT_PATTERNS = [
    "How many {labels} are in {location}?",
    "Count the {labels} in {location}",
    "What's the number of {labels} in {location}?",
    "How many {labels} does {location} have?",
]
MAX_COUNT_PATTERNS = [
    "Which room has the most {labels}?",
    "What room contains the highest number of {labels}?",
    "Where are the most {labels}?",
    "Which location has the largest count of {labels}?",
]


def _get_label_for_display(label: str) -> str:
    """Convert canonical label to display form."""
    # chairs -> chairs, small_office_objects -> small office objects
    return label.replace("_", " ")


def _paraphrase_colloquial(case: dict) -> str:
    """Generate a colloquial/casual version of the instruction."""
    oracle = case["oracle_intent"]
    action = oracle.get("action", "navigate")
    target = oracle.get("target", {})

    label_display = _get_label_for_display(target.get("label", target.get("text", "")))
    room = target.get("room")
    locations = target.get("locations", [])
    relations = target.get("relations", [])
    quantifier = target.get("quantifier", "one")
    aggregate = target.get("aggregate")

    # Build colloquial instruction
    if action == "unsupported":
        return random.choice(["Pick up the " + label_display, "Grab a " + label_display, "Can you lift the " + label_display + "?"])

    if action == "escort":
        actor_text = (oracle.get("actor") or {}).get("text", "person")
        dest_text = (oracle.get("destination") or {}).get("text", "destination")
        return random.choice([
            f"Take the {actor_text} to {dest_text}",
            f"Walk the {actor_text} over to {dest_text}",
            f"Could you escort the {actor_text} to {dest_text}?",
        ])

    # Aggregate
    if aggregate:
        op = aggregate.get("operation", "count_in_location")
        loc = aggregate.get("location", {})
        loc_text = _get_label_for_display(loc.get("text", "")) if loc else ""
        if op == "count_in_location" and loc_text:
            return random.choice([
                f"How many {label_display} are in {loc_text}?",
                f"What's the count of {label_display} in {loc_text}?",
                f"Count up the {label_display} in {loc_text}",
            ])
        elif op == "max_count":
            return random.choice([
                f"Which room has the most {label_display}?",
                f"Where are the most {label_display}?",
                f"What room has the most {label_display}?",
            ])

    # Quantifier
    if quantifier == "all":
        verb = random.choice(LOCATE_SYNONYMS)
        return random.choice([
            f"{verb} all the {label_display}",
            f"Where are all the {label_display}?",
            f"I need to find every {label_display}",
        ])
    elif quantifier == "nearest":
        return random.choice([
            f"Where's the closest {label_display}?",
            f"Find the nearest {label_display}",
            f"I need the {label_display} that's closest",
        ])

    # Relation
    if relations:
        rel = relations[0]
        rel_type = rel.get("relation", "near")
        rel_target = _get_label_for_display(rel.get("target_text", ""))
        if rel_type == "near":
            return random.choice([
                f"Where's a {label_display} near the {rel_target}?",
                f"Find a {label_display} close to the {rel_target}",
                f"I need a {label_display} by the {rel_target}",
            ])
        elif rel_type == "inside":
            return random.choice([
                f"Find a {label_display} inside {rel_target}",
                f"Where's a {label_display} in {rel_target}?",
                f"I need a {label_display} that's inside {rel_target}",
            ])

    # Location
    if locations:
        loc_texts = [_get_label_for_display(l.get("text", "")) for l in locations if l.get("text")]
        loc_str = " and ".join(loc_texts)
        verb = random.choice(LOCATE_SYNONYMS if action == "locate" else NAVIGATE_SYNONYMS)
        return random.choice([
            f"{verb} the {label_display} in {loc_str}",
            f"I need the {label_display} that's in {loc_str}",
            f"Where's the {label_display} in {loc_str}?",
        ])

    if room:
        verb = random.choice(NAVIGATE_SYNONYMS if action == "navigate" else LOCATE_SYNONYMS)
        return random.choice([
            f"{verb} the {label_display} in room {room}",
            f"Where's the {label_display} in room {room}?",
            f"I need the {label_display} from room {room}",
        ])

    # Default
    if action == "locate":
        verb = random.choice(LOCATE_SYNONYMS)
        return random.choice([
            f"{verb} the {label_display}",
            f"Where can I find a {label_display}?",
            f"I'm looking for a {label_display}",
        ])
    else:  # navigate
        verb = random.choice(NAVIGATE_SYNONYMS)
        return random.choice([
            f"{verb} the {label_display}",
            f"Take me to the {label_display}",
            f"I need to get to the {label_display}",
        ])


def _paraphrase_rephrased(case: dict) -> str:
    """Generate a rephrased version with different sentence structure."""
    oracle = case["oracle_intent"]
    action = oracle.get("action", "navigate")
    target = oracle.get("target", {})
    label_display = _get_label_for_display(target.get("label", target.get("text", "")))
    room = target.get("room")
    locations = target.get("locations", [])
    relations = target.get("relations", [])
    quantifier = target.get("quantifier", "one")
    aggregate = target.get("aggregate")

    if action == "unsupported":
        return random.choice([
            f"The task involves picking up a {label_display}",
            f"I need to grasp and carry the {label_display}",
        ])

    if action == "escort":
        actor_text = (oracle.get("actor") or {}).get("text", "person")
        dest_text = (oracle.get("destination") or {}).get("text", "destination")
        return random.choice([
            f"The {actor_text} needs to be brought to {dest_text}",
            f"Guide the {actor_text} toward {dest_text}",
        ])

    # Aggregate
    if aggregate:
        op = aggregate.get("operation", "count_in_location")
        loc = aggregate.get("location", {})
        loc_text = _get_label_for_display(loc.get("text", "")) if loc else ""
        if op == "count_in_location" and loc_text:
            return f"The number of {label_display} present in {loc_text}"
        elif op == "max_count":
            return f"The room containing the highest quantity of {label_display}"

    # Quantifier
    if quantifier == "all":
        return random.choice([
            f"Every {label_display} in the scene",
            f"The complete set of {label_display}",
        ])
    elif quantifier == "nearest":
        return random.choice([
            f"The {label_display} with the shortest distance",
            f"The {label_display} that is closest by",
        ])

    # Relation
    if relations:
        rel = relations[0]
        rel_type = rel.get("relation", "near")
        rel_target = _get_label_for_display(rel.get("target_text", ""))
        if rel_type == "near":
            return f"A {label_display} that is adjacent to the {rel_target}"
        elif rel_type == "inside":
            return f"A {label_display} contained within {rel_target}"

    # Location
    if locations:
        loc_texts = [_get_label_for_display(l.get("text", "")) for l in locations if l.get("text")]
        loc_str = " and ".join(loc_texts)
        return f"The {label_display} situated in {loc_str}"

    if room:
        return random.choice([
            f"The {label_display} belonging to room {room}",
            f"A {label_display} found in room {room}",
        ])

    # Default
    if action == "locate":
        return random.choice([
            f"The location of a {label_display}",
            f"A {label_display} that exists in the scene",
        ])
    else:
        return random.choice([
            f"Movement toward the {label_display}",
            f"Destination: {label_display}",
        ])


def generate_paraphrase_benchmark(
    intent_benchmark_path: str | Path,
    output_path: str | Path,
    seed: int = 42,
) -> dict:
    """Generate paraphrase benchmark from intent benchmark."""
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
    for case in cases:
        oracle = case["oracle_intent"]

        # Style 1: Standard (original instruction)
        paraphrase_cases.append({
            "case_id": f"{case['case_id']}_std",
            "source_case_id": case["case_id"],
            "instruction": case["instruction"],
            "language_style": "standard",
            "category": case["category"],
            "oracle_intent": oracle,
            "required_roles": case["required_roles"],
            "tags": case["tags"],
        })

        # Style 2: Colloquial
        colloquial = _paraphrase_colloquial(case)
        paraphrase_cases.append({
            "case_id": f"{case['case_id']}_col",
            "source_case_id": case["case_id"],
            "instruction": colloquial,
            "language_style": "colloquial",
            "category": case["category"],
            "oracle_intent": oracle,
            "required_roles": case["required_roles"],
            "tags": case["tags"],
        })

        # Style 3: Rephrased
        rephrased = _paraphrase_rephrased(case)
        paraphrase_cases.append({
            "case_id": f"{case['case_id']}_rep",
            "source_case_id": case["case_id"],
            "instruction": rephrased,
            "language_style": "rephrased",
            "category": case["category"],
            "oracle_intent": oracle,
            "required_roles": case["required_roles"],
            "tags": case["tags"],
        })

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
    with open(intent_path, "rb") as f:
        source_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    # Manifest
    style_dist = {}
    cat_dist = {}
    for c in paraphrase_cases:
        s = c["language_style"]
        style_dist[s] = style_dist.get(s, 0) + 1
        cat = c["category"]
        cat_dist[cat] = cat_dist.get(cat, 0) + 1

    manifest = {
        "benchmark_status": "ready",
        "benchmark_file": str(output.name),
        "benchmark_file_hash": bench_hash,
        "source_benchmark": str(intent_path.name),
        "source_benchmark_hash": source_hash,
        "case_count": len(paraphrase_cases),
        "paraphrases_per_intent": 3,
        "language_style_distribution": style_dist,
        "categories": cat_dist,
        "seed": seed,
    }

    manifest_path = output.with_name(output.stem + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(paraphrase_cases)} paraphrase cases ({len(cases)} intents × 3 styles)")
    print(f"Benchmark: {output}")
    print(f"Manifest: {manifest_path}")
    print(f"Styles: {style_dist}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Paraphrase Benchmark")
    parser.add_argument(
        "--intent-benchmark",
        default="eval/cases/intent_benchmark_100_v1.jsonl",
    )
    parser.add_argument(
        "--output",
        default="eval/cases/intent_paraphrase_300_v1.jsonl",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    generate_paraphrase_benchmark(args.intent_benchmark, args.output, args.seed)


if __name__ == "__main__":
    main()
