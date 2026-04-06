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
- `preview_only` is implemented and surfaced in:
  - `/task`
  - dashboard task detail
  - dashboard runtime detail
- `followup execute` surface now exists as a separate command/action contract.
- current behavior:
  - if `preview_only`, block with `followup_execute_brief_required`
  - if `executable` or `partially_executable`, the surface exists but execution wiring is still pending

## Next Implementation Steps
1. derive executable follow-up slices from critic/runtime state
2. attach launch spec / runner target for executable follow-up
3. prove manual-followup path again under the new split:
   - preview proof
   - execute proof

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
