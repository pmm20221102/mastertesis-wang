#!/usr/bin/env python3
"""Generate grounding benchmark from RAG data."""

import json
import random
import sys
from pathlib import Path

def generate_benchmark(rag_dir: str, output_path: str, seed: int = 42) -> list:
    """Generate 100-case grounding benchmark."""
    random.seed(seed)
    
    rag_dir = Path(rag_dir)
    
    # Load RAG data
    entities = {}
    with open(rag_dir / "outputs" / "khronos_office_new" / "rag_documents.jsonl", 'r') as f:
        for line in f:
            doc = json.loads(line)
            meta = doc.get('metadata', {})
            if meta.get('chunk_type') == 'entity':
                node_id = meta.get('node_id', '')
                if node_id:
                    text = doc.get('text', '')
                    label = ''
                    if '(' in text and ')' in text:
                        start = text.index('(') + 1
                        end = text.index(')')
                        label = text[start:end]
                    entities[node_id] = {
                        'id': node_id,
                        'type': meta.get('node_type', 'unknown'),
                        'label': label or meta.get('label', ''),
                        'room': meta.get('room', ''),
                    }
    
    # Group by type
    objects = {k: v for k, v in entities.items() if v['type'] == 'objects'}
    places = {k: v for k, v in entities.items() if v['type'] == 'places'}
    
    # Group objects by label
    objects_by_label = {}
    for obj_id, obj in objects.items():
        label = obj['label']
        if label:
            if label not in objects_by_label:
                objects_by_label[label] = []
            objects_by_label[label].append(obj)
    
    # Generate cases
    cases = []
    case_id = 1
    
    # Category 1: Ambiguous queries (15 cases)
    for label, objs in list(objects_by_label.items())[:5]:
        for _ in range(3):
            if len([c for c in cases if 'ambiguous' in c.get('tags', [])]) >= 15:
                break
            cases.append({
                "case_id": f"bench_ambiguous_{case_id:03d}",
                "instruction": f"Find a {label.rstrip('s')}.",
                "expected_grounding": {
                    "pipeline_status": "ambiguous",
                    "target": {"status": "ambiguous", "candidate_type": label}
                },
                "oracle_intent": {"action": "locate", "target": {"text": label.rstrip('s'), "label": label, "node_type": "objects"}},
                "tags": ["ambiguous", "medium"]
            })
            case_id += 1
    
    # Category 2: Room-constrained (15 cases)
    for obj in list(objects.values())[:15]:
        if obj['room']:
            cases.append({
                "case_id": f"bench_room_{case_id:03d}",
                "instruction": f"Find the {obj['label'].rstrip('s')} in place {obj['room']}.",
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved", "node_id": obj['id'], "node_type": "objects", "place_id": obj['room']}
                },
                "oracle_intent": {"action": "locate", "target": {"text": obj['label'].rstrip('s'), "label": obj['label'], "node_type": "objects", "room": obj['room']}},
                "tags": ["room_constraint", "medium"]
            })
            case_id += 1
    
    # Category 3: Set query (10 cases)
    for label, objs in list(objects_by_label.items())[:5]:
        if len(objs) >= 2:
            for _ in range(2):
                if len([c for c in cases if 'set_query' in c.get('tags', [])]) >= 10:
                    break
                cases.append({
                    "case_id": f"bench_set_{case_id:03d}",
                    "instruction": f"Find all {label}.",
                    "expected_grounding": {
                        "pipeline_status": "ready",
                        "target": {"status": "resolved_set", "node_ids": [o['id'] for o in objs]}
                    },
                    "oracle_intent": {"action": "locate", "target": {"text": label, "label": label, "node_type": "objects", "quantifier": "all"}},
                    "tags": ["set_query", "medium"]
                })
                case_id += 1
    
    # Category 4: Not-found (10 cases)
    not_found_items = ["refrigerator", "elevator", "staircase", "escalator", "swimming pool", "gym", "cafeteria", "library", "lab", "workshop"]
    for item in not_found_items:
        cases.append({
            "case_id": f"bench_notfound_{case_id:03d}",
            "instruction": f"Find the {item}.",
            "expected_grounding": {
                "pipeline_status": "not_found",
                "target": {"status": "not_found"}
            },
            "oracle_intent": {"action": "locate", "target": {"text": item, "node_type": "objects"}},
            "tags": ["not_found", "easy"]
        })
        case_id += 1
    
    # Category 5: Spatial (10 cases)
    places_list = list(places.keys())[:10]
    spatial_variations = [
        'Navigate to the nearest place.',
        'Find the nearest location.',
        'Locate the closest spot.',
        'Go to the nearest place.',
        'Find the nearest place.',
        'Locate the nearest location.',
        'Go to the closest place.',
        'Navigate to the nearest spot.',
        'Find the closest place.',
        'Locate the nearest location.',
    ]
    for i, place_id in enumerate(places_list):
        cases.append({
            "case_id": f"bench_spatial_{case_id:03d}",
            "instruction": spatial_variations[i] if i < len(spatial_variations) else f'Navigate to the nearest place #{i+1}.',
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": place_id, "node_type": "places"}
            },
            "oracle_intent": {"action": "navigate", "target": {"text": "nearest place", "node_type": "places", "quantifier": "nearest"}},
            "tags": ["spatial", "medium"]
        })
        case_id += 1
    
    # Category 6: Exact ID (5 cases)
    for obj in list(objects.values())[:5]:
        cases.append({
            "case_id": f"bench_exact_{case_id:03d}",
            "instruction": f"Navigate to node {obj['id']}.",
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": obj['id'], "node_type": "objects"}
            },
            "oracle_intent": {"action": "navigate", "target": {"text": obj['id'], "node_type": "objects"}},
            "tags": ["exact_id", "easy"]
        })
        case_id += 1
    
    # Category 7: Object navigation (15 cases)
    for label, objs in list(objects_by_label.items())[:5]:
        for _ in range(3):
            if len([c for c in cases if 'obj_nav' in c.get('tags', [])]) >= 15:
                break
            cases.append({
                "case_id": f"bench_objnav_{case_id:03d}",
                "instruction": f"Navigate to a {label.rstrip('s')}.",
                "expected_grounding": {
                    "pipeline_status": "ambiguous",
                    "target": {"status": "ambiguous", "candidate_type": label}
                },
                "oracle_intent": {"action": "navigate", "target": {"text": label.rstrip('s'), "label": label, "node_type": "objects"}},
                "tags": ["obj_nav", "medium"]
            })
            case_id += 1
    
    # Category 8: Complex (10 cases)
    for obj in list(objects.values())[:10]:
        cases.append({
            "case_id": f"bench_complex_{case_id:03d}",
            "instruction": f"Find the {obj['label'].rstrip('s')} in the office.",
            "expected_grounding": {
                "pipeline_status": "ambiguous",
                "target": {"status": "ambiguous", "candidate_type": obj['label']}
            },
            "oracle_intent": {"action": "locate", "target": {"text": obj['label'].rstrip('s'), "label": obj['label'], "node_type": "objects"}},
            "tags": ["complex", "hard"]
        })
        case_id += 1
    
    # Trim to 100
    cases = cases[:100]
    
    # Save
    with open(output_path, 'w') as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + '\n')
    
    return cases


if __name__ == "__main__":
    rag_dir = sys.argv[1] if len(sys.argv) > 1 else "../RAG/khronos_3dsg_rag"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "eval/cases/grounding_benchmark_100_v1.jsonl"
    cases = generate_benchmark(rag_dir, output_path)
    print(f"Generated {len(cases)} cases")
