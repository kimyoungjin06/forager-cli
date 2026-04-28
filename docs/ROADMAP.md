# ROADMAP

## 1. Purpose
- 본 문서는 `docs/CONSTITUTION.md` 조항을 구현 항목으로 분해한 실행 체크리스트다.
- 운영 우선순위는 `안정성 -> 자동화 -> 자율성` 순서로 적용한다.

## 2. Status Legend
- `DONE`: 운영에 사용 중이며 기본 검증 완료
- `IN_PROGRESS`: 부분 구현 완료, 정책/자동화 추가 필요
- `PLANNED`: 설계 합의만 완료, 구현 미착수

## 3. Constitution Trace Matrix
| Clause | Requirement | Status | Evidence | Next Action |
|---|---|---|---|---|
| C2-Task-first | 태스크 단위 추적/상태 확인 | DONE | `/monitor`, `/check`, `/task`, `T-xxx` alias | task 의존성(선행/후행) 추가 |
| C2-TF-isolation | 태스크별 TF 생성/종료 + 캐시/반출 정책 | DONE | `AOE_TF_EXEC_MODE=worktree`, `.aoe-team/tf_exec_map.json`, `.aoe-team/tf_runs/`, 성공만 보존(실패 시 자동 정리) + TTL GC(`AOE_TF_EXEC_CACHE_TTL_HOURS=72`) | TTL 정책 운영값 튜닝(디스크/속도) |
| C2-Human-readable | 사람 친화 별칭 우선 | DONE | `O1..` project alias, `T-xxx` task alias | 별칭 충돌 자동 복구 정책 추가 |
| C2-Operator-minimal | 상위 Orch 중심 운영 | IN_PROGRESS | Telegram slash 중심 제어 | Mother-Orch가 다중 Orch 큐 직접 스케줄링 |
| C2-Remote-parity | 로컬/원격 제어 동등성 | IN_PROGRESS | Telegram 운영 명령군 + replay | tmux page/hintbar 상태를 Telegram 요약에 반영 |
| C2-Safety | 고위험 확인/권한 경계 | DONE | `/ok`, ACL, owner mode, lockme, replay read/write auth split | owner/admin 정책 감사 자동화 |
| C2-Evidence | 로그/증거 기반 추적 | IN_PROGRESS | `gateway_events.jsonl`, `/kpi`, poll_state | TF 결과물 링크 표준 스키마 확정 |
| C2-Ephemeral-vs-Canonical | 에페메럴 로그(room)와 canonical evidence 분리 | DONE | `/room` + `.aoe-team/logs/rooms` (jsonl) + 기본 보관 14일(`AOE_ROOM_RETENTION_DAYS=14`) | TF 주요 이벤트 auto-post(옵션) 검토 |
| C4-Lifecycle | plan->execute->critic->integrate 루프 | IN_PROGRESS | retry/replan/cancel 기반 루프 | Critic fail 시 재실행/에스컬레이션 자동 전이 |
| C5-Local-UX | Alt 숫자 즉시 전환/페이지 | DONE | `telegram_tmux.sh ui/page/refresh` + 2-line status hint bar + tmux hooks(auto refresh) | refresh 속도 최적화(aoe meta cache/디바운스) |
| C6-Remote-control | Telegram 운영 콘솔화 | IN_PROGRESS | `/status` `/monitor` `/map` `/replay` | 승인/거부/우선순위 변경 명령을 워크플로에 결합 |
| C7-Governance | 정책 준수 + 감사 가능성 | IN_PROGRESS | `CONSTITUTION`, `CHARTER`, `RUNBOOK` | 정책 위반 시 자동 차단/감사 이벤트 강화 |
| C8-Success-metrics | 성공 기준 수치화 | PLANNED | charter 성공 기준 정의 | KPI 대시보드 텍스트 리포트 정규화 |

## 4. Execution Phases
### Phase A: Operational Hardening
- [x] replay 권한 분리(list/show vs run/purge)
- [x] 실패 큐 TTL 도입(`AOE_GATEWAY_FAILED_TTL_HOURS`)
- [x] 상태 요약 강화(`failed_queue_total`, `last_failed_at`, `active_tf_count`)
- [x] 헬스체크 실패 원인 코드화(`telegram_tmux.sh health` -> `E_HEALTH_*`)
- [x] tmux side panel 제거(복사/붙여넣기 방해 요소 제거) + 2-line status hint bar 고정

### Phase B: TF Automation
- [x] 멀티에이전트 TF 문서 템플릿/스캐폴드 구축(`docs/templates/multi_agent_tf_ops_template`, `docs/investigations_mo`)
- [x] 태스크 접수 시 TF 템플릿 자동 생성 (multi-project scaffold sync)
- TF 종료 조건(성공/실패/재시도) 상태머신 고정
- [x] TF 종료 시 반출/아카이브(기본) 자동화 (`tf_close_index.csv`, handoff placeholder fill, `archive/close_summary.md`)
- 결과/근거 반출 포맷 표준화
- [x] TF 실행 캐시(worktree/run_dir) TTL GC (`AOE_TF_EXEC_CACHE_TTL_HOURS=72`, `0` disables)

### Phase B2: Ephemeral Agent Comms
- [x] room(에페메럴 게시판) `/room` + jsonl 저장 + 기본 14일 GC (`AOE_ROOM_RETENTION_DAYS=14`, `0` disables)

### Phase C: Mother-Orch Scheduling
- 다중 Orch todo 큐 스케줄러
- 우선순위/윈도우 기반 실행 정책
- 에스컬레이션 정책(자동 vs 승인지점) 분리
- [x] 프로젝트 Todo 큐 최소 구현 (`/todo add|done|list|next`)
- [x] Mother-Orch global next (`/next`)

### Phase D: Autonomous Recovery
- 예측 불가 상황 분류(환경/권한/의존성/모델오류)
- 분류별 자동 대응 플레이북
- 대응 실패 시 즉시 operator 보고/승인 루프

## 5. Near-term Sprint (Next 2 cycles)
- [x] replay 권한 분리 완성
- [x] failed queue TTL + purge 정책 자동화
- [x] Critic fail 상태를 기준으로 재실행 3회 + 에스컬레이션 훅 구현(`--exec-critic`, `--exec-critic-retry-max`)
- [x] Telegram 보고 레벨(짧게/보통/상세) 스위치 추가(`/report`)

## 6. Current Orch Bug Bundle
- [x] planning 시작 직후 provisional task에 `phase1_*` 메타와 candidate roles를 즉시 반영
- [x] `planning_planner` / `planning_critic` 이벤트에 `project_key`, `request_id`, `task_short_id`를 항상 싣기
- [x] writer/reporting 성격 평문을 `orch-monitor`가 아니라 Mother-Orch `dispatch_task`로 우선 분류
- [x] explicit `phase1_role_preset` / `phase2_team_preset` 분류와 operator 노출
- [x] `Phase2` lane template가 preset(`writer/analysis/build/data/review/mixed`)을 기준으로 planner drift를 보정

## 7. Current Provider Capacity Handling
- [x] Claude rate limit 시 Codex fallback
- [x] Codex rate limit 시 Claude fallback
- [x] 양쪽 provider 모두 rate limit이면 task를 `tf_phase=rate_limited`로 유지
- [x] `retry_after_sec`와 함께 absolute `retry_at` 저장
- [x] `rate_limited` task가 전체 queue를 영구 busy로 막지 않도록 parked 상태로 분리
- [x] `retry_at` 도래 시 scheduler/offdesk 자동 resume
- [x] provider capacity summary를 `/auto status`, `/offdesk review`에 표시
- [x] provider capacity cooldown memory(`provider_capacity.json`) 저장
- [x] operator override history(`/auto off`) 기록
- [x] planning / worker runtime이 active provider cooldown memory를 보고 선제 fallback
- [ ] provider별 backoff level 세분화 정책 고도화

## 8. Current Priority Plan
### 8.0 Rebased Execution Order
- [x] current completion review and rebased execution order fixed
  - 문서:
    - `docs/CURRENT_COMPLETION_REVIEW_20260327.md`
  - 핵심 판단:
    - first-wave `Live Runtime Verification`은 완료됨
    - 다음 큰 축은 `Project Flow Compiler`를 통한 문서/런타임 수렴
    - 그 다음은 external runner pickup/ack productization
  - 우선순위:
    1. `8.8 Document Registry + Dashboard Convergence`
    2. `8.4 Background / Remote Execution` external pickup/ack 후속
    3. `8.6 Retention and Storage`
    4. `8.8 doctor / setup / migration` 후속 마무리
    5. `8.8 compatibility / deprecation` 후속 마무리
    6. `8.8 learned runbook extraction`
    7. `8.7 Structural Debt` 후속
- [x] hot harness benchmark import baseline fixed
  - 문서:
    - `docs/HOT_HARNESS_IMPORT_PLAN_20260404.md`
    - `docs/HARNESS_ADOPTION_PLAN.md`
  - 핵심 판단:
    - 현재 전략 우선순위는 `scenario-level planner seam`보다 `operator workflow productization`
    - `OMC`를 포함한 상위 하네스들의 공통 강점은:
      - plan-first interaction
      - background / remote execution
      - operator dashboard
      - governance / audit / permissions
  - 재정렬된 큰 축:
    1. `Execution Brief`
    2. `Ondesk / Offdesk state model`
    3. `Background / Remote Execution`
    4. `Project Progress Board`
    5. `Governance / Permissions / Usage`
    6. `Project Flow Compiler`
    7. completed deep rerun/manual-followup verification now feeds productization work
  - reference discipline:
    - benchmark-driven imports must cite `docs/HOT_HARNESS_IMPORT_PLAN_20260404.md` reference IDs
- [x] control plane + executor adapter architecture fixed
  - 문서:
    - `docs/EXECUTOR_ADAPTER_ARCHITECTURE.md`
    - `docs/HARNESS_ADOPTION_PLAN.md`
    - `docs/BACKGROUND_REMOTE_EXECUTION_SPEC.md`
  - 핵심 판단:
    - 우리 제품 경계는 `native harness monolith`가 아니라 `operator control plane + execution adapters`
    - canonical core는:
      - `RequestContract`
      - `ExecutionBrief`
      - `FollowupBrief`
      - `Background Run Ticket`
      - run lock / slot / scheduler / audit / dashboard
    - runner-specific launch and pickup behavior는 adapter 책임으로 분리
  - 구현 기준선:
    - `scripts/gateway/aoe_tg_executor_adapter.py`
- [x] model + harness routing basis fixed
  - 문서:
    - `docs/MODEL_HARNESS_ROUTING_BASIS_20260408.md`
  - 핵심 판단:
    - `on-desk` 기본값은 terminal-native coding shell
    - `off-desk` 기본값은 current control plane
    - `LangGraph`는 orchestration re-platform option이지 기본 off-desk 답이 아님
    - `OpenClaw`는 daemon/gateway option이지 canonical task/runtime truth가 아님
    - premium models are for judgment, open/local models are for execution
- [x] model endpoint adapter seam fixed
  - 문서:
    - `docs/MODEL_ENDPOINT_ADAPTER_SPEC.md`
  - 구현 기준선:
    - `scripts/gateway/aoe_tg_model_endpoint_adapter.py`
  - 핵심 판단:
    - 실제 GPU/Ollama endpoint 정보는 config로 바인딩
    - canonical control truth에는 secret/host를 박지 않음
    - `/orch status`와 dashboard가 route binding/unbound 상태를 직접 보여줌
- [x] stack manifest compiler baseline fixed
  - 문서:
    - `docs/AOE_STACK_MANIFEST_SPEC.md`
    - `docs/HOST_NATIVE_EXECUTION_STRATEGY.md`
  - 구현 기준선:
    - `scripts/gateway/aoe_tg_stack_compile.py`
  - 핵심 판단:
    - topology는 raw `.env`만으로 운영하지 않음
    - `stack manifest + env overlay + compiler`로 canonical runtime artifact를 생성
    - control plane은 compiled artifact만 읽고 실행
    - backend execution 기본값은 host-native Python + systemd, container는 later remote worker에 한정
- [x] upstream harness authoring adapter baseline fixed
  - 문서:
    - `docs/HARNESS_AUTHORING_ADAPTER_SPEC.md`
    - `docs/UPSTREAM_VENDORING_STRATEGY.md`
  - 구현 기준선:
    - `scripts/gateway/aoe_tg_harness_authoring_adapter.py`
    - `scripts/gateway/aoe_tg_harness_authoring_export.py`
  - 핵심 판단:
    - `revfactory/harness`는 off-desk runtime이 아니라 authoring/generation module
    - upstream import 기본 전략은 `git subtree`
    - runtime truth는 계속 current control plane이 소유

### 8.1 Cleanup and Naming
- [x] stale PR 정리 완료
  - 목표: 오래 열린 PR(`#37`, `#16`)를 닫고 현재 기준선만 남긴다.
  - 이유: 이미 후속 merged change에 흡수된 이력이 남아 있어 review queue를 오염시킨다.
- [x] 문서 기준 canonical 용어 재정리
  - `Mother-Orch` -> `Control Plane`
  - `Project Orch` / `Orch` -> `Project Runtime`
  - `TF` -> `Task Team`
  - 원칙: 문서/오퍼레이터 표기부터 바꾸고, 코드 identifier는 alias 호환을 유지하며 단계적으로 정리한다.
  - 후속: operator surface와 code identifier는 단계적으로 수렴시킨다.
- [x] 용어 전환 2단계
  - operator surface 문구 치환
  - code alias convergence
  - legacy term deprecation checkpoint

### 8.2 Control Dashboard MVP
- [x] 대시보드 MVP spec 문서화
  - 화면:
    - `Overview`
    - `Offdesk Prep`
    - `Active Tasks`
    - `Task Detail`
  - 데이터 원천:
    - 기존 `/task`, `/monitor`, `/offdesk`, `/auto status` state/view를 재사용
  - 금지:
    - dashboard 전용 상태계
    - dashboard 전용 business logic
- [x] read-only MVP 구현
  - 현재 구현: `http.server.ThreadingHTTPServer + Jinja2`
  - note:
    - current repo baseline에 `FastAPI`/`uvicorn` dependency가 없으므로 Phase 1은 stdlib server로 고정
    - route/action contract는 유지하고, 필요 시 이후 ASGI shell로 교체 가능
  - 우선 노출:
    - provider capacity
    - offdesk readiness
    - active task preset
    - phase1/phase2
    - lane state
    - rerun/followup
    - backend contract
- [x] shared read-only state adapter 구현
  - 요구:
    - side-effect-free runtime file reads
    - no mutating loader reuse
    - control-root/team-dir canonical wiring
    - per-file freshness and stale fallback metadata
- [x] dashboard DTO contract 구현
  - 요구:
    - `ControlSummaryDTO`
    - `RuntimeCardDTO`
    - `ActiveTaskRowDTO`
    - `TaskDetailDTO`
    - dashboard adapter가 policy layer가 되지 않도록 shared helper ownership 정리
- [x] `Project Runtime Detail` 2단계 설계/구현
  - 목적:
    - condensed board card와 task detail 사이의 중간 관제면 추가
  - 포함:
    - runtime readiness / sync health
    - proposal pressure
    - recent blocked/completed tasks
    - provider pressure / repeat memory
    - runtime-scoped first action / next focus
- [x] `Recovery` read-only view 구현
  - `nightly-session-summary/latest.json` artifact 재사용
  - current snapshot + last nightly artifact를 함께 표시
  - runtime/task drill-down 링크 유지
- [x] read-only MVP 1단계 설계 문서화
  - 라우트:
    - `Overview`
    - `Offdesk Prep`
    - `Active Tasks`
    - `Task Detail`
  - 재사용 원칙:
    - `aoe_tg_task_state.py`
    - `aoe_tg_task_view.py`
    - `aoe_tg_offdesk_flow.py`
    - `aoe_tg_scheduler_control_handlers.py`
  - 구현 범위:
    - read-only only
    - dashboard 전용 상태/정책 금지
- [ ] action wiring 2차 구현
  - 전제:
    - Phase 1 read-only parity 완료
    - shared read-only state adapter / DTO contract 안정화
    - `Project Runtime Detail` page 추가 완료
    - shared operator action contract에서 `safe` / `phase2` 분리 완료
    - initial HTTP shortlist 고정
      - `retry`
      - `followup`
      - `sync preview`
      - `auto recover`
  - `auto on/off`
  - `recover`
  - `retry`
  - `followup`

### 8.3 Preset Completion Contract
- [x] preset 분류/shape/quality defaults 연결
- [x] preset completion matrix 문서화/고정
  - 문서:
    - `docs/PRESET_COMPLETION_MATRIX.md`
  - 포함:
    - required evidence
    - expected execution/review shape
    - done/rerun/manual followup 기준
- [x] completion matrix를 operator/recovery surface wording에 연결

### 8.4 Live Runtime Verification
- [x] `Live Runtime Verification` spec
  - 문서:
    - `docs/LIVE_RUNTIME_VERIFICATION_SPEC.md`
- [x] `Planning Convergence` spec
  - 문서:
    - `docs/PLANNING_CONVERGENCE_SPEC.md`
  - 핵심 판단:
    - `planning_ready`는 최소 `3`회 비판 검토를 통과해야 한다
    - `contract_incomplete` / unsafe / invalid dependency는 즉시 `blocked` 가능
    - 반복 blocker는 `stalled`로 분리한다
- [x] `Request Contract` architecture spec
  - 문서:
    - `docs/REQUEST_CONTRACT_SPEC.md`
  - 핵심 판단:
    - 평문은 intake UI로 남긴다
    - planning truth는 `Request Contract`로 전환한다
    - `D1` 이후 `data -> build -> review -> mixed` 순서로 extractor를 도입한다
- [x] preset verification scenario inventory
  - 문서:
    - `docs/LIVE_RUNTIME_VERIFICATION_SCENARIOS.md`
- [x] first-wave verification artifacts and doc consistency guard
  - 문서:
    - `docs/runtime_verification/README.md`
    - `docs/runtime_verification/phase2/TEMPLATE.md`
    - `docs/runtime_verification/phase2/build/`
    - `docs/runtime_verification/phase2/data/`
    - `docs/runtime_verification/phase2/review/`
    - `docs/runtime_verification/phase2/mixed/`
    - `tests/gateway/test_runtime_verification_docs.py`
- [ ] `Execution Brief` / on-desk -> off-desk handoff 계약 도입
  - 목적:
    - on-desk의 마지막 작업을 `실행 가능성 판정 + 실행계약 확정`으로 고정
    - off-desk는 확정된 실행계약만 실행하게 한다
  - 현재 상태:
    - `IN_PROGRESS`
    - 1차 구현 완료:
      - `RequestContract -> ExecutionBrief -> OrchTaskSpec` 상태 필드 추가
      - `/task`, dashboard, offdesk, recovery에 brief truth 노출
      - off-desk priority가 brief blocked 상태를 직접 읽음
    - 남은 것:
      - preset별 executable slice 정교화
      - followup lineage/history surface 정리
      - `FollowupBrief` external runner eligibility 정교화
  - 새 상태모델:
    - `executable`
    - `underspecified`
    - `infeasible`
    - `partially_executable`
    - `operator_decision_required`
  - 핵심 산출물:
    - `RequestContract -> ExecutionBrief -> OrchTaskSpec`
  - 범위:
    - executable slice
    - blocked slice
    - non-goals
    - done/rerun criteria
    - operator-only decisions
  - benchmark import sources:
    - `OpenCode` (`REF-OC-1`, `REF-OC-2`, `REF-OC-3`)
    - `GitHub Copilot coding agent` (`REF-GHCA-1`, `REF-GHCA-2`)
    - `Claude Code` (`REF-CC-1`)
- [ ] `Background / Remote Execution` rails 도입
  - 목적:
    - off-desk work를 foreground live session에 묶지 않고 durable queue / remote worker / runner로 옮긴다
  - 문서:
    - `docs/BACKGROUND_REMOTE_EXECUTION_SPEC.md`
  - 현재 상태:
    - `IN_PROGRESS`
    - 1차 구현 완료:
      - `Background Run Ticket`
      - `background_runs.json`
      - same-process `local_background` daemon
      - `background_worker.json`
      - `bgw-*`, `bgq-clean`, runner preference
      - `run_lock_mode=test_only` to suppress non-test internal launches during development
      - `background_runner_slot_limit` to cap concurrent tmux/external launches per runtime
      - `local_tmux` retry/replan launch path
      - `local_tmux` initial detached no-wait path for serializable gateway runs
      - tmux log/result artifact persistence + polling
      - `github_runner` / `remote_worker` handoff manifest emission for externalizable retry/followup paths
      - `github_runner` / `remote_worker` `worker-run` pickup entrypoint with ack/result/log sidecars
      - `github_runner` bundle export/materialize bridge and GitHub Actions worker workflow
      - external sidecar artifact import + local poll bridge
      - `local_tmux` followup-execute proof for B3/D3/R3/M3
    - 남은 것:
      - non-serializable `initial detached no-wait` cases의 externalizable 분리
      - credentials / transport policy, issue/PR ergonomics, and automated artifact retrieval policy
  - 최소 범위:
    - background queue
    - request-to-run audit trail
    - remote runner abstraction
    - durable evidence bundle
  - benchmark import sources:
    - `OpenHands` (`REF-OH-1`)
    - `Claude Code Action` (`REF-CC-2`)
    - `GitHub Copilot coding agent` (`REF-GHCA-1`, `REF-GHCA-2`)
- [ ] `Project Progress Board`
  - 목적:
    - operator가 프로젝트별 진행도, brief status, blocked slice, next intervention을 dashboard에서 본다
  - 현재 상태:
    - `IN_PROGRESS`
    - 1차 구현 완료:
      - overview/offdesk/runtime/recovery에 brief + background queue + worker truth 노출
      - queue stale/depth 기반 정렬과 remediation hints 연결
    - 남은 것:
      - dedicated board view
      - queue/brief severity 기반 더 강한 정렬 정책
  - 표면:
    - `/control`
    - `/control/runtimes/{project_alias}`
    - later: dedicated project progress board
  - benchmark import sources:
    - `OpenHands` (`REF-OH-1`)
    - `Goose` (`REF-GS-1`, `REF-GS-2`)
    - our existing dashboard/runtime truth shell
- [ ] `Governance / Permissions / Usage`
  - 목적:
    - off-desk execution을 권한/예산/감사 기준으로 운영 가능한 수준까지 올린다
  - 범위:
    - permission policy
    - usage reporting
    - budget boundaries
    - audit trail
    - secret redaction
  - benchmark import sources:
    - `Amp` (`REF-AMP-1`, `REF-AMP-2`)
    - `Goose` (`REF-GS-1`, `REF-GS-2`)
    - `Claude Code` (`REF-CC-1`, `REF-CC-2`)
- [x] preset별 실제 `Phase2` 완료 흐름 검증
  - 대상:
    - `build`
    - `data`
    - `review`
    - `mixed`
  - 확인 범위:
    - planning
    - execution/review lanes
    - critic verdict
    - rerun/manual followup
    - `/task`
    - `/monitor`
    - `/offdesk review`
    - dashboard `Task Detail`
    - dashboard `Recovery`
  - 현재 상태:
    - all first-wave phase2 scenarios are `executed_done`
    - happy-path proof:
      - `B1`, `D1`, `R1`, `M1`
    - rerun-path proof:
      - `B2`, `D2`, `R2`, `M2`
    - manual-followup proof:
      - `B3`, `D3`, `R3-preview`, `R3-execute`, `M3`
    - external background rail support proof:
      - `R4`
  - 전략 위치:
    - first-wave runtime verification no longer blocks the next product block
    - next priority is document/runtime convergence via `Project Flow Compiler`
- [ ] `Planning Convergence` loop 도입
  - 목적:
    - planner one-shot 가정을 제거
    - `planning_ready`를 최소 `3`회 비판 검토의 결과로 강제
  - 1단계:
    - `plan_review_count`
    - `plan_issue_codes`
    - `plan_issue_history`
    - `plan_convergence_status`
    - `plan_stalled_reason`
    저장
  - 2단계:
    - review pass focus 분리
      - contract/scope
      - ownership/dependency/artifact
      - verification/surface explainability
  - 3단계:
    - `ready`
    - `blocked`
    - `stalled`
    판정 구현
  - note:
    - `D1`의 반복 blocker 이력을 issue taxonomy 초기 corpus로 재사용한다
- [ ] `Request Contract` layer 도입
  - 목적:
    - `text -> request contract -> planning/runtime`
    - marker-only acceptance floor 의존 제거
  - 1단계:
    - `data` contract extractor
    - `RequestContract -> ExecutionBrief -> OrchTaskSpec` assembly seam 명시화
    - `contract_incomplete` / `contract_ambiguous` fail-closed gate
    - preset precedence 구현
      - explicit override
      - existing task lineage
      - artifact/work shape
      - role-preset inference
      - fallback text heuristic
    - `aoe_tg_schema.py`의 data acceptance를 contract 기반으로 전환
    - task/runtime state에 `request_contract_*` canonical subset 저장
    - `/task` / dashboard `Task Detail`에 contract summary와 missing fields 노출
  - 2단계:
    - `build` contract extractor
    - auth/session persisted-state boundary를 contract 기반으로 전환
  - 3단계:
    - `review` / `mixed` contract extractor
    - handoff/review/work separation을 contract 기반으로 전환
  - note:
    - `Execution Brief`가 먼저 on-desk/off-desk handoff를 안정화하고, 그 다음 `Planning Convergence`가 planning truth를 안정화한다

### 8.5 Recovery Summary
- [x] nightly session summary spec 고정
  - 문서:
    - `docs/NIGHTLY_SESSION_SUMMARY.md`
  - 목적:
    - morning recovery용 overnight artifact 정의
- [x] nightly session summary 생성 경로 설계
  - 전제:
    - dashboard/read-only parity 이후
    - structured runtime state reuse
- [x] nightly session summary dashboard recovery view 연결
  - route:
    - `/control/recovery`
  - 범위:
    - latest artifact metadata
    - recovered runtime summaries
    - task detail/runtime detail drill-down
- [x] recent dashboard action audit를 recovery artifact에 연결
  - source:
    - `.aoe-team/dashboard/action-history.jsonl`
  - output:
    - nightly summary JSON/markdown
    - recovery/dashboard history seed

### 8.6 Retention and Storage
- [x] storage retention policy 고정
  - 문서:
    - `docs/STORAGE_RETENTION_POLICY.md`
  - 범위:
    - canonical runtime state
    - evidence/artifacts
    - ephemeral runtime artifacts
    - logs/rooms
- [ ] disk hygiene와 retention policy 연결
  - TTL/cleanup 설정값과 실제 운영 저장소 전략 연결
- [x] dashboard action audit retention 연결
  - tunables:
    - `AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS`
    - `AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS`
  - 정책:
    - append 시 prune/rewrite

### 8.7 Structural Debt
- [ ] 다음 분해 타깃 선정
  - 후보:
    - `scripts/gateway/aoe-telegram-gateway.py`
    - `scripts/gateway/aoe_tg_task_state.py`
    - `scripts/gateway/aoe_tg_tf_exec.py`
    - `scripts/gateway/aoe_tg_offdesk_flow.py`
    - `scripts/gateway/aoe_tg_parse.py`
    - `scripts/gateway/aoe_tg_orch_contract.py`
    - `scripts/gateway/aoe_tg_scheduler_sync.py`
  - 원칙:
    - 새 기능보다 운영 병목이 큰 곳부터 자른다.
    - verification / convergence work를 지원할 때 우선 분해한다.

### 8.8 Harness Adoption
- [x] OMC benchmark / harness trend 정리
  - 문서:
    - `docs/OH_MY_CLAUDE_BENCHMARK_20260327.md`
- [x] harness alignment principles 고정
  - 문서:
    - `docs/HARNESS_ALIGNMENT_PRINCIPLES.md`
- [x] harness adoption master plan 작성
  - 문서:
    - `docs/HARNESS_ADOPTION_PLAN.md`
- [x] `Session Search`
  - source:
    - `gateway_events.jsonl`
    - nightly summary artifacts
    - dashboard action audit
    - runtime/task snapshots
  - surface:
    - Telegram `/history search`
    - dashboard `/control/history`
- [x] `Session Search` Phase 1 spec
  - 문서:
    - `docs/SESSION_SEARCH_SPEC.md`
- [x] `Task Team Observatory`
  - lane age / stale warning
  - last event / bottleneck summary
  - touched file index / conflict hint
  - `/task`, `/monitor`, dashboard detail/recovery 우선
  - note:
    - Phase 2 source plumbing 완료
    - `tool_count`는 explicit backend/runtime 값 우선, `reply_count`/`counts.replies` fallback 사용
    - exact per-tool telemetry는 후속 고도화 항목으로 남긴다
- [x] `Task Team Observatory` Phase 1 spec
  - 문서:
    - `docs/TASK_TEAM_OBSERVATORY_SPEC.md`
- [ ] `Document Registry + Dashboard Convergence`
  - 목표:
    - runtime-centric dashboard에 project document flow를 붙여 개별 프로젝트 진행도를 ondesk/dashboard에서 함께 판단할 수 있게 한다
  - 현재 상태:
    - `IN_PROGRESS`
    - 1차 구현 완료:
      - `WorkspaceBrief`
      - `DocumentRegistry`
      - `ContextPack Compiler`
      - `Project Flow Compiler` minimal artifact
      - `/task`, `/orch status`, dashboard runtime/task detail summary surfaces
    - 기준 spec:
      - `docs/WORKSPACE_ONBOARDING_SPEC.md`
      - `docs/DOCUMENT_REGISTRY_SPEC.md`
      - `docs/CONTEXT_PACK_COMPILER_SPEC.md`
      - `docs/PROJECT_FLOW_COMPILER_SPEC.md`
  - 구현 축:
    - [x] `Project Flow Compiler` implementation baseline
    - [x] per-project compiled flow artifact
    - [x] conservative doc/runtime drift detection
    - [x] dashboard `Project Runtime Detail` `Document Flow` card
    - [x] recovery/nightly doc drift excerpt
  - 실행 순서:
    1. [x] minimal `Project Flow Compiler` artifact 추가
    2. [x] dashboard read-only `Document Flow` card 연결
    3. [x] recovery/nightly drift excerpt 연결
    4. drift detection 강화
- [x] `Project Flow Compiler` spec
  - 문서:
    - `docs/PROJECT_FLOW_COMPILER_SPEC.md`
- [x] `AOE_STATE_DIR`
  - worktree-local `.aoe-team` 위에 centralized state root 도입
  - stable project-id 기반 state continuity
  - note:
    - core resolver precedence 구현 완료
    - `AOE_STATE_DIR` + legacy `.aoe-team` migration-safe fallback 반영
    - resolved state root surface visibility 반영
    - artifact path helper 정렬 반영
    - copy-first migration helper 추가
- [ ] `doctor / setup / migration` discipline
  - bootstrap, health, upgrade, state-root migration guidance
  - note:
    - standalone `doctor` script added for state-root, artifact, runtime-config, and binary health
    - standalone `setup guide` script added for bootstrap/env/migration/systemd/dashboard/doctor next-steps
    - state-root migration helper is already added
    - broader upgrade/migration workflow remains open
- [ ] compatibility / deprecation envelope
  - legacy surface retirements에 deterministic response envelope 추가
  - note:
    - shared deprecation envelope helper added
    - code-centric retired surface inventory added
    - retired surface set now includes:
      - `mother-orch`
      - `swarm`
      - `orch map`
      - `tasks` / `board`
      - `lifecycle`
      - `follow-up`
      - `off-desk`
      - `cleanup`
- [ ] learned runbook extraction
  - repeated blocker / remediation를 durable runbook으로 승격
