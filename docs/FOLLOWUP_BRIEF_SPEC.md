# FollowupBrief Spec

## Purpose
- `FollowupBrief` is the operator-facing artifact that separates:
  - safe follow-up preview
  - explicit follow-up execution eligibility
- It exists because `manual followup` is not automatically an off-desk execution request.

## Position In The Flow
- Current enforced flow:
  - `plain text -> RequestContract -> ExecutionBrief -> OrchTaskSpec`
- `FollowupBrief` sits beside `ExecutionBrief` for manual follow-up branches.
- It answers:
  - what follow-up lanes are in scope
  - whether the current surface is preview-only
  - what operator reasoning blocks execution
  - whether a future executable follow-up path is authorized

## States
- `preview_only`
  - safe inspection only
  - no background launch allowed
  - current `/followup` maps here
- `executable`
  - an explicit follow-up execution slice exists
  - execution surface may launch against that slice
- `partially_executable`
  - only part of the follow-up scope is executable
  - blocked/operator-owned remainder must stay visible

## Required Fields
- `followup_brief_version`
- `followup_brief_status`
- `followup_brief_summary`
- `followup_brief_execution_lane_ids[]`
- `followup_brief_review_lane_ids[]`
- `followup_brief_reason`

## Preview Surface Seed Contract
- gateway preview surfaces now treat `followup_brief_*` as the canonical lane/reason source
- legacy `exec_critic.manual_followup_*` fields remain as a fallback for older task records without follow-up brief lane ids
- the preview surface derives allowed lane targets from:
  - `followup_brief_execution_lane_ids[]`
  - `followup_brief_review_lane_ids[]`
  - `followup_brief_reason`
- minimum preview-open seed:
  - `followup_brief_status=preview_only`
  - `followup_brief_execution_lane_ids[]`
  - `followup_brief_review_lane_ids[]`
  - `followup_brief_reason`
- verification rule:
  - `/followup`, `/task`, and dashboard surfaces must show the same lane ids and operator-owned reason

## Surface Split
### Preview Surface
- command:
  - `/followup <task>`
- dashboard HTTP:
  - `POST /control/actions/task/followup`
- semantics:
  - read-only
  - show lane targets, reason, and next safe drill-down

### Execute Surface
- command:
  - `/followup-exec <task>`
- dashboard HTTP:
  - `POST /control/actions/task/followup-execute`
- semantics:
  - phase2 / mutation candidate
  - must refuse execution when `followup_brief_status=preview_only`

## Current Implementation
- `FollowupBrief` is implemented and surfaced in:
  - `/task`
  - dashboard task detail
  - dashboard runtime detail
- conservative derivation is now implemented:
  - `preview_only`
    - no execution slice exists
  - `executable`
    - execution slice exists and no review/manual slice remains
  - `partially_executable`
    - execution slice exists but review/manual slice remains visible
- `followup execute` surface now reuses the existing rerun rail:
  - foreground run bridge
  - `local_tmux` background launch when runner preference allows it
- current behavior:
  - if `preview_only`, block with `followup_execute_brief_required`
  - if `executable` or `partially_executable`, launch only the declared execution lanes
  - review/manual remainder stays in preview scope and is not auto-launched

## Next Implementation Steps
1. prove manual-followup path again under the new split:
   - preview proof
     - `docs/runtime_verification/phase2/review/R3_manual_followup_preview.md`
   - execute proof
     - `docs/runtime_verification/phase2/review/R3_manual_followup_execute.md`
2. add richer follow-up launch specs and external runner eligibility
3. decide whether a dedicated `followup_of` dashboard/history surface is needed

## Benchmark References
- `OpenCode` plan/build split and permission boundary:
  - `REF-OC-1`
  - `REF-OC-2`
  - `REF-OC-3`
- `GitHub Copilot coding agent` async handoff model:
  - `REF-GHCA-1`
  - `REF-GHCA-2`
- `Claude Code` terminal-native command workflow:
  - `REF-CC-1`
- reference index:
  - `docs/HOT_HARNESS_IMPORT_PLAN_20260404.md`
