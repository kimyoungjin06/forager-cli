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
- [ ] preset별 실제 `Phase2` 완료 흐름 검증
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
    - `scripts/gateway/aoe_tg_run_handlers.py`
    - `scripts/gateway/aoe_tg_scheduler_handlers.py`
  - 원칙:
    - 새 기능보다 운영 병목이 큰 곳부터 자른다.
    - 대시보드 MVP 후에 착수한다.

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
  - 구현 축:
    - `Project Flow Compiler`
    - per-project compiled flow artifact
    - doc/runtime drift detection
    - dashboard `Project Runtime Detail` `Document Flow` card
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
    - broader migration/deprecation workflow remains open
- [ ] compatibility / deprecation envelope
  - legacy surface retirements에 deterministic response envelope 추가
  - note:
    - shared deprecation envelope helper added
    - initial retired surfaces: `mother-orch`, `swarm`
- [ ] learned runbook extraction
  - repeated blocker / remediation를 durable runbook으로 승격
