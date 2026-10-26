# LLM + Semantic RAG + Graph Grounding + Simple Planner
# End-to-End (E2E) Test Implementation Guide

---

# 0. Phase Objective

The following components have already been completed:

```text
Intent + Semantic RAG + Graph Grounding integration test
Independent Simple Planner v3 test
Deterministic Compiler
Plan Validator
```

Current Planner v3 results:

```text
granite4.1:3b
Strict Symbolic Plan Success = 100%
Final Validation Pass        = 100%

mistral:latest
Strict Symbolic Plan Success ≈ 98.3%
Final Validation Pass        ≈ 98.3%
```

The project is therefore ready to proceed to end-to-end testing.

This phase addresses one question:

> Given a natural-language task, can the complete system generate a correct, safe, executable final plan without using any oracle intermediate answers?

Complete pipeline:

```text
Natural Language
→ LLM Intent Parser
→ Semantic Vector RAG
→ Graph Grounding
→ Simple Planner
→ Deterministic Compiler
→ Plan Validator
→ Final TaskPlan
```

---

# 1. Keep This Phase Simple

This E2E round must not add features or expand the system boundary.

Do not include:

```text
reasoning about corridor endpoints
inference of room functions
dynamic object re-localization
complex Place topology
long task chains with more than three targets
real Nav2 execution
cross-dataset experiments
```

This round tests only capabilities that are already implemented and independently validated:

```text
single-target navigation
Room-constrained navigation
explicit source → destination
escort
avoid / prefer
ambiguous
not_found
unsupported
simple two-step tasks
```

---

# 2. Components to Freeze Before Testing

Before running the E2E test, record hashes for:

```text
Intent Parser Prompt
Intent schema
Semantic Retriever configuration
Embedding model
Vector DB
Canonical Graph
Graph Grounding API
Simple Planner Prompt
SimplePlan schema
Deterministic Compiler
Plan Validator
E2E Benchmark
E2E Evaluator
```

Once the formal E2E test begins:

```text
Do not modify prompts based on Test failures.
Do not change the semantic threshold.
Do not change the ground truth.
Do not remove difficult cases.
Do not modify the planner schema.
```

Only interface bugs and evaluator bugs may be fixed using the Dev set.

---

# 3. Model Selection

Test only two models in the first round:

```text
Primary model:
granite4.1:3b

Comparison model:
mistral:latest
```

Rationale:

```text
Granite Planner v3 = 100%
Granite has lower latency.

Mistral joint grounding is more stable.
Mistral Planner v3 ≈ 98.3%
```

Do not rerun all seven models.

Shared configuration:

```text
temperature = 0
repeats = 3
```

If a model has a special thinking mode:

```text
Disable thinking.
Ensure that `content` returns a structured result.
```

---

# 4. Split the E2E Benchmark into Dev and Test Sets

## 4.1 Dev Set

Recommended size:

```text
15–20 cases
```

Purpose:

```text
debug module integration
fix field propagation
fix the evaluator
validate `failure_stage`
```

The Dev set may be rerun and changed.

---

## 4.2 Frozen Test Set

Recommended size:

```text
50 cases
```

Purpose:

```text
formal thesis results
model comparison
final failure analysis
```

Do not tune parameters after freezing the Test set.

Dev and Test sets:

```text
may contain the same task categories
must not reuse natural-language phrasings
must not reuse ground-truth cases
```

---

# 5. Recommended Distribution for the 50-Case E2E Test Set

|Category|Count|
|---|---:|
|single_navigate|12|
|room_constrained_navigate|8|
|ordered_source_destination|8|
|escort|6|
|navigate_with_avoid|4|
|navigate_with_prefer|4|
|ambiguous_gate|3|
|not_found_gate|3|
|unsupported_gate|2|
|Total|50|

Do not reintroduce the complex categories that have been removed:

```text
single_locate
locate_then_navigate
ambiguous `multi_step`
step-specific references
complex relation / aggregate planning
```

---

# 6. Category Designs

## 6.1 `single_navigate`: 12 Cases

Example user instructions:

```text
Go to something people can sit on.
Navigate to the wall-mounted storage unit.
Go to the large office machine.
```

Complete pipeline:

```text
Intent:
action = navigate

Semantic RAG:
surface target → canonical label

Graph Grounding:
Multiple instances → `ambiguous`
One unique instance → `resolved`

Planner:
navigate_to target

Compiler:
ref → real node ID / navigation Place

Validator:
PASS
```

For an executable plan, a `single_navigate` case should select:

```text
one target that is unique after applying a Room constraint or another implemented constraint
```

If an unconstrained query has multiple candidates, put it in the `ambiguous` category rather than `single_navigate`.

---

## 6.2 `room_constrained_navigate`: 8 Cases

Examples:

```text
Go to the chair in room 0756.
Navigate to the sign in room 0756.
Go to the large office machine in room 0764.
```

Requirements:

```text
LLM correctly extracts the Room.
Semantic RAG maps the label correctly.
Graph Grounding finds one unique entity in the Room.
The planner outputs a `navigate_to` target.
The compiler binds the correct ID.
```

---

## 6.3 `ordered_source_destination`: 8 Cases

The instruction must specify the order explicitly:

```text
First go to room 0752, then go to room 0756.
```

Avoid ambiguous wording such as:

```text
Navigate from 0752 to 0756.
```

Expected symbolic plan：

```json
{
  "status": "valid",
  "steps": [
    {"action": "navigate_to", "ref": "source"},
    {"action": "navigate_to", "ref": "destination"}
  ]
}
```

---

## 6.4 `escort`: 6 Cases

Use a high-level macro action.

Instruction：

```text
Escort the person from room 0752 to room 0756.
```

Expected symbolic plan：

```json
{
  "status": "valid",
  "steps": [
    {"action": "escort_to", "ref": "destination"}
  ]
}
```

The compiler binds these automatically:

```text
actor
source
destination
executor
```

Do not ask the LLM to output `role_refs` or internal IDs.

---

## 6.5 `navigate_with_avoid`: 4 Cases

Instruction：

```text
Navigate to the target while avoiding room 0752.
```

The LLM only needs to output:

```json
{
  "steps": [
    {"action": "navigate_to", "ref": "target"}
  ]
}
```

Intent / Grounding retain the avoided Room.

The compiler adds the actual avoided Room ID.

Do not ask the planner to output a constraint reference.

---

## 6.6 `navigate_with_prefer`: 4 Cases

Example:

```text
Navigate to the target and prefer the shortest route.
```

`shortest_route` is a:

```text
routing policy
```

It is not a graph node ID.

The compiler adds:

```json
{
  "routing_policy": "shortest_path"
}
```

---

## 6.7 `ambiguous_gate`: 3 Cases

Example:

```text
Go to a chair.
```

The scene contains multiple chairs.

Expected behavior:

```text
Grounding status = ambiguous
Final pipeline status = blocked / needs_clarification
No executable plan
```

The planner should not be called, or it should return only:

```text
ambiguous
```

Do not choose an ID arbitrarily.

---

## 6.8 `not_found_gate`: 3 Cases

Query an explicit concept that is not supported by the current ontology.

Expected behavior:

```text
Semantic / Grounding = not_found
No executable plan
No node ID
```

---

## 6.9 `unsupported_gate`: 2 Cases

Use a task for which the robot currently has no executor.

Expected behavior:

```text
unsupported
No executable plan
```

---

# 7. Ground-Truth Design

Each E2E case must include:

```text
Natural-language instruction
Oracle Intent
Expected Semantic Label
Expected Grounding
Expected Symbolic Plan
Expected Compiled Plan
Expected Final Status
```

Oracle fields must only be used for scoring.

Recommended schema:

```json
{
  "case_id": "e2e_001",
  "category": "room_constrained_navigate",
  "instruction": "Go to the chair in room 0756.",

  "oracle_intent": {
    "action": "navigate",
    "target": {
      "text": "chair",
      "node_type": "object",
      "quantifier": "one",
      "locations": [
        {
          "node_type": "room",
          "text": "0756",
          "node_id": "FULL_ROOM_ID"
        }
      ]
    }
  },

  "expected_semantic": {
    "status": "matched",
    "canonical_label": "chairs"
  },

  "expected_grounding": {
    "status": "resolved",
    "node_ids": ["OBJECT_ID"],
    "candidate_node_ids": ["OBJECT_ID"]
  },

  "expected_symbolic_plan": {
    "status": "valid",
    "steps": [
      {
        "action": "navigate_to",
        "ref": "target"
      }
    ]
  },

  "expected_compiled_plan": {
    "status": "valid",
    "steps": [
      {
        "action": "navigate_to",
        "target_id": "OBJECT_ID",
        "executor": "nav2"
      }
    ]
  },

  "expected_final_status": "ready"
}
```

---

# 8. Ground-Truth Generation Rules

Identify the real nodes from the canonical graph before writing the natural-language instruction.

Order of operations:

```text
select a real Room / Object / Agent
→ generate the complete grounding candidate set
→ determine the status
→ define the symbolic plan
→ generate the expected compiled plan with the compiler
→ write the instruction last
```

Do not:

```text
write an instruction first
→ then guess the ground truth
```

Generate the expected compiled plan from the oracle symbolic plan using the same deterministic compiler whenever possible. This avoids inconsistencies from manual authoring.

---

# 9. E2E Benchmark Validator

The validator must check:

```text
case IDs are unique
all expected IDs exist
Room / Object hierarchy is correct
the expected semantic label exists
the expected candidate set is complete
the expected status is consistent with the candidate count
expected symbolic references exist
expected actions are valid
compiled IDs match the references
ambiguous / not_found / unsupported cases have no executable plan
the benchmark hash is correct
```

The validator must recompute grounding from the canonical graph.

---

# 10. Prevent Oracle Leakage

The prediction function may only receive:

```python
run_e2e(
    instruction,
    model,
    runtime_config,
)
```

The prediction path must not receive:

```text
oracle_intent
expected_label
expected_grounding
expected_symbolic_plan
expected_compiled_plan
```

Recommended structure:

```python
prediction = run_e2e(instruction, model, runtime)
score = evaluate_e2e(prediction, case_ground_truth)
```

Add this test:

```text
test_prediction_path_has_no_oracle_fields
```

---

# 11. Production E2E Execution Flow

Each case must invoke the production modules:

```text
1. Intent Parser
2. Semantic Vector Retriever
3. SceneGraphRAG / GroundingOrchestrator
4. Simple Planner v3
5. compile_simple_plan
6. Plan Validator
```

Do not use:

```text
test-only `ground_label()`
manually constructed predicted grounding
oracle symbolic plan
```

---

# 12. Planner Invocation Gate

Only when:

```text
Grounding status = ready / resolved
```

Only then may the planner be called.

For these statuses:

```text
ambiguous
not_found
unsupported
```

the system must:

```text
not call the planner
not generate an executable plan
```

Record:

```text
planner_called = false
```

---

# 13. Data to Save for Every Result

```json
{
  "case_id": "e2e_001",
  "instruction": "...",

  "intent_result": {
    "raw_output": "...",
    "parsed_intent": {},
    "schema_valid": true,
    "latency_ms": 0
  },

  "semantic_result": {
    "query": "...",
    "status": "matched",
    "top1_label": "...",
    "label_ranking": [],
    "latency_ms": 0
  },

  "grounding_result": {
    "status": "resolved",
    "node_ids": [],
    "candidate_node_ids": [],
    "latency_ms": 0
  },

  "planner_result": {
    "called": true,
    "first_pass_raw": "...",
    "first_pass_symbolic_plan": {},
    "repair_used": false,
    "final_symbolic_plan": {},
    "latency_ms": 0
  },

  "compiled_plan": {},

  "validation_result": {
    "passed": true,
    "errors": []
  },

  "stage_scores": {
    "intent_success": true,
    "semantic_success": true,
    "grounding_success": true,
    "planner_success": true,
    "compiler_success": true,
    "validator_success": true
  },

  "e2e_success": true,
  "failure_stage": null,
  "all_errors": [],
  "total_latency_ms": 0
}
```

---

# 14. Intent Scoring

Check at least:

```text
Schema Validity
Action Accuracy
Quantifier Accuracy
Room / Location Accuracy
Spurious Location Rate
Wrong-provided Node Type Rate
Intent Gate Success
```

Rules:

```text
missing `node_type`
→ do not fail immediately; record the provided rate

incorrect `node_type`
→ Intent failure

ground truth has no Location
but the LLM adds a Location
→ Intent failure
```

Evaluate the semantic correctness of target text with Semantic Retrieval; do not require an exact string match.

---

# 15. Semantic Retrieval Scoring

For an expected `matched` result:

```python
semantic_success = (
    top1_label == expected_label
    and status in {"matched", "ambiguous"}
)
```

If:

```text
the top-1 label is correct
but `status = not_found`
```

the Semantic stage still fails.

For an expected not-found result:

```python
semantic_success = status == "not_found"
```

Also report:

```text
Label Hit@1
Recall@5
MRR
Not-found F1
```

---

# 16. Grounding Scoring

## Resolved

```text
the status is correct
AND the target ID is correct
```

## Ambiguous

```text
status = ambiguous
AND candidate-set F1 = 1.0
```

## Resolved Set

If the E2E test includes set-based grounding:

```text
status = resolved_set
AND exact set match
```

## Not-found

```text
status = not_found
AND node_ids = []
```

---

# 17. Planner Scoring

Use the Planner v3 simple metrics:

```text
Symbolic Schema Validity
Status Accuracy
Exact High-level Action Sequence
Role Ref Accuracy
Strict Symbolic Plan Success
Repair Rate
```

Do not score these as LLM outputs:

```text
executor
target_type
constraint refs
step number
node IDs
```

The compiler is responsible for these fields.

---

# 18. Compiler Scoring

The compiler is deterministic. Target:

```text
Executor Mapping Accuracy       = 100%
Target-ID Binding Accuracy      = 100%
Constraint Attachment Accuracy  = 100%
Escort Actor Binding Accuracy   = 100%
Step Numbering Accuracy         = 100%
```

If the compiler fails, record the failure as:

```text
compiler
```

not as a planner failure.

---

# 19. Validator Scoring

Check at least:

```text
Allowed actions
Action order
Role refs
Real node IDs
Correct-role IDs
Executor
Constraints
Final plan schema
```

Safety metrics:

```text
Invalid Node ID Rate
Ungrounded ID Rate
Wrong-role ID Rate
Missing Required ID Rate
```

The target for every metric is:

```text
0
```

---

# 20. E2E Strict Success Definition

For each case:

```python
e2e_success = (
    intent_success
    and semantic_success
    and grounding_success
    and planner_success
    and compiler_success
    and validator_success
)
```

For:

```text
ambiguous
not_found
unsupported
```

Success means:

```text
correctly blocked
the planner was not called
no executable plan was produced
no node ID leaked
```

---

# 21. Failure Attribution by Stage

Record the first blocking stage:

```text
intent_schema
intent_action
intent_quantifier
intent_location
semantic_retrieval
grounding_status
grounding_id
grounding_candidate_set
planner_schema
planner_action
planner_ref
compiler
validator
```

Save for each failure:

```json
{
  "failure_stage": "intent_location",
  "all_errors": [
    "room_missing",
    "final_plan_not_generated"
  ]
}
```

The number of lines in `failures.jsonl` must equal the failure count in the summary.

---

# 22. Primary E2E Metrics

## Overall

```text
E2E Strict Success
Final Validation Pass Rate
Final Action Sequence Accuracy
Final Target-ID Accuracy
Total Latency
```

## By Stage

```text
Intent Gate Success
Semantic Stage Success
Grounding Exact Success
Planner Strict Success
Compiler Success
Validator Success
```

## Safety

```text
Invalid Node ID Rate
Ungrounded ID Rate
Wrong-role ID Rate
Missing Required ID Rate
```

## Failure Attribution

```text
Failure by Stage
```

---

# 23. Repeated Runs and Statistics

For each model:

```text
50 cases × 3 repeats = 150 runs
```

Report:

```text
Mean Success
Standard Deviation
Per-case Consistency
First-pass Planner Success
Repair Rate
```

For status-gate cases, require:

```text
all 3 of 3 runs to block correctly
```

---

# 24. Latency Metrics

Save:

```text
Intent latency
Semantic retrieval latency
Grounding latency
Planner latency
Compiler latency
Validator latency
Total latency
```

Report:

```text
Mean
Median
P95
```

Do not include the model's first-load time in ordinary warm-query latency.

Report cold-start latency separately.

---

# 25. Recommended File Structure

```text
eval/e2e/
├── cases/
│   ├── e2e_dev_v1.jsonl
│   ├── e2e_test_50_v1.jsonl
│   └── e2e_test_50_v1.manifest.json
├── generate_e2e_benchmark.py
├── validate_e2e_benchmark.py
├── run_e2e_eval.py
├── metrics.py
├── README.md
└── tests/
    └── test_e2e_eval.py
```

Results:

```text
eval/results/e2e_v1/
├── granite/
│   ├── summary.json
│   ├── per_case_results.jsonl
│   ├── failures.jsonl
│   └── run_manifest.json
└── mistral/
    ├── summary.json
    ├── per_case_results.jsonl
    ├── failures.jsonl
    └── run_manifest.json
```

---

# 26. Run Manifest

Must save:

```text
Git commit
E2E benchmark hash
Evaluator hash
Intent prompt hash
Semantic config hash
Vector DB fingerprint
Canonical graph hash
Planner prompt hash
SimplePlan schema hash
Compiler hash
Validator hash
Model
Temperature
Context length
Repeat count
Command
Environment
```

---

# 27. Tests to Add

## Leakage Prevention

```text
test_e2e_prediction_receives_only_instruction
test_no_expected_label_in_prediction_path
test_no_expected_grounding_in_prediction_path
test_no_expected_plan_in_planner_prompt
```

## Status gate

```text
test_ambiguous_does_not_call_planner
test_not_found_does_not_call_planner
test_unsupported_does_not_call_planner
test_blocked_case_has_no_executable_plan
```

## Module Integration

```text
test_intent_target_text_enters_semantic_retrieval
test_semantic_top1_enters_grounding
test_grounded_refs_enter_simple_planner
test_symbolic_plan_enters_compiler
test_compiled_plan_enters_validator
```

## Safety

```text
test_final_ids_exist
test_final_ids_come_from_grounded_refs
test_wrong_role_id_rejected
test_invalid_id_rate_zero
```

## Scoring

```text
test_spurious_location_causes_intent_failure
test_positive_semantic_not_found_is_failure
test_e2e_requires_all_stages
test_failure_count_matches_failures_file
```

---

# 28. E2E Acceptance Criteria

At least one model must achieve:

```text
E2E Strict Success             ≥ 80%
Final Validation Pass          ≥ 85%
Intent Gate Success            ≥ 85%
Semantic Stage Success         ≥ 90%
Grounding Exact Success        ≥ 90%
Planner Strict Success         ≥ 90% (conditional on the planner being called)
Compiler Success               = 100%
Status Gate Accuracy           = 100%
Final Target-ID Accuracy       ≥ 95%
Invalid Node ID Rate           = 0
Ungrounded ID Rate             = 0
Wrong-role ID Rate             = 0
Failure Stage Coverage         = 100%
```

An E2E score lower than the independently tested module scores is expected.

Do not use:

```text
must be close to 100%
```

as an acceptance criterion.

---

# 29. Repair Policy Constraints

Before the formal Test, fixes on the Dev set may:

```text
fix interface bugs
fix field propagation
fix the evaluator
fix the validator
```

Do not:

```text
modify prompts based on Test cases
add aliases to Test queries
change the semantic threshold to fit the Test set
change expected plans
remove failed cases
add rule-based fallbacks to conceal LLM errors
```

Allow at most one small prompt fix targeted at the Dev set.

---

# 30. E2E Result Analysis

The report must include at least:

```text
overall Granite vs. Mistral results
success rate at each stage
failure-stage distribution
success rate by category
latency
repair usage rate
safety metrics
consistency across three repeats
```

Focus the analysis on:

```text
whether failures remain concentrated in Intent Room / Quantifier
whether the planner retains high accuracy
whether module integration introduces new interface errors
```

---

# 31. Nav2 Execution Is Not Required in This Phase

The final output of this E2E phase is:

```text
Validated TaskPlan
```

It is not:

```text
physical robot arrival at the target
```

Nav2/simulation execution belongs to the next evaluation layer:

```text
Execution Evaluation
```

Do not mix navigation success rates into this round.

---

# 32. Correct Meaning of E2E in the Thesis

In this thesis, E2E means:

```text
natural language
→ final validated executable TaskPlan
```

It does not mean:

```text
natural language
→ the robot completing the task in the real world
```

The thesis must clearly distinguish:

```text
Planning End-to-End
and
Physical Execution End-to-End
```

---

# 33. Final Deliverables

```text
[ ] e2e_dev_v1.jsonl
[ ] e2e_test_50_v1.jsonl
[ ] e2e_test_50_v1.manifest.json
[ ] E2E benchmark generator
[ ] E2E benchmark validator
[ ] E2E evaluator
[ ] E2E unit/integration tests
[ ] Granite results
[ ] Mistral results
[ ] E2E comparison report
[ ] Complete failures
[ ] Run manifests
[ ] E2E stage conclusion
```

---

# 34. Recommended Execution Order

```text
Phase 0
freeze Joint Grounding and Planner v3

Phase 1
create a 15–20-case E2E Dev set

Phase 2
integrate the production modules

Phase 3
fix interface and evaluator bugs on Dev

Phase 4
generate and validate the frozen 50-case E2E Test set

Phase 5
run Granite and Mistral with `repeats=3`

Phase 6
analyze failures by stage

Phase 7
freeze the results and write the thesis
```

---

# 35. Final Principle

Do not assign deterministic work to the LLM during the E2E phase.

Keep the system responsibilities separated:

```text
LLM Intent Parser：
understand natural language

Semantic RAG：
map to a canonical label

Graph Grounding：
resolve the real entity and ID

Simple Planner：
output a high-level action sequence and role reference

Compiler：
add executor, ID, type, constraints, and step number

Validator：
reject invalid plans
```

Final objective:

> Verify whether these independently validated modules can be integrated to reliably generate a safe, correct, executable final task plan from natural language.
