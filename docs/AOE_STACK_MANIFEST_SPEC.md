# AOE Stack Manifest Spec

## 1. Purpose
- Define a declarative operator-owned stack file that answers:
  - which harness is on-desk
  - which harness/executor is off-desk
  - which model endpoint is used for each logical route
  - which workspace/runtime defaults should be compiled into canonical artifacts
- Keep raw env and host details out of task/runtime truth.
- Compile once, then let runtime surfaces read only canonical artifacts.

## 2. Why This Layer Exists
- `.env` alone is too flat for:
  - on-desk vs off-desk harness placement
  - worker vs judge vs escalation model routing
  - workspace doc/todo defaults
  - runner policy defaults
- Runtime code should not reinterpret intent from ad hoc env keys every time.
- The correct split is:
  - `stack manifest`
  - `env overlay`
  - `compiler`
  - compiled canonical artifacts

## 3. Canonical Input Layers

### 3.1 Stack Manifest
- recommended filename:
  - `<project_root>/aoe_stack.json`
- purpose:
  - operator-owned topology declaration
- keeps:
  - workspace defaults
  - harness placement
  - model endpoint declarations
  - route binding intent

### 3.2 Env Overlay
- recommended filename:
  - `<project_root>/.env`
- purpose:
  - runtime values such as:
    - base URLs
    - ports
    - env var names
- example:
```dotenv
OLLAMA_BASE_URL=http://172.16.0.37:11434
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
```

### 3.3 Compiler
- helper:
  - `scripts/gateway/aoe_tg_stack_compile.py`
- purpose:
  - convert stack manifest + env overlay into canonical runtime artifacts

## 4. Canonical Output Artifacts
- `<team_dir>/workspace_brief.json`
- `<team_dir>/model_endpoints.json`
- `<team_dir>/model_routing.json`

Policy:
- runtime should read these compiled artifacts
- runtime should not depend on raw manifest or env parsing during normal operation

## 5. Manifest Shape

### 5.1 Top-Level Fields
- `version`
- `profile`
- `workspace`
- `models`
- `routing`
- `harness`

### 5.2 `workspace`
- `workspace_key`
- `project_alias`
- `project_overview`
- `code_roots[]`
- `doc_roots[]`
- `doc_ignore_globs[]`
- `canonical_todo_path`
- `canonical_runbook_paths[]`
- `background_runner_target`
- `run_lock_mode_default`
- `background_runner_slot_limits`

### 5.3 `models`
- map of logical endpoint declarations
- each entry may define:
  - `endpoint_id`
  - `provider_kind`
  - `base_url`
  - `base_url_env`
  - `model`
  - `api_key_env`
  - `enabled`
  - `local`
  - `supports_tools`
  - `supports_json`
  - `route`
  - `routes[]`
  - `notes`

### 5.4 `routing`
- optional explicit override layer
- fields:
  - `profile`
  - `routes`
- each route may define:
  - `endpoint_id`
  - `endpoint_ref`
  - `endpoint_key`
  - `model_key`
  - `family_hint`
  - `model_hint`
  - `fallback_ids[]`
  - `summary_label`
  - `notes`

### 5.5 `harness`
- operator-readable placement only
- current compiler uses this for summary and default executor selection
- fields:
  - `on_desk.kind`
  - `off_desk.kind`
  - `off_desk_executor.kind`

## 6. Hard Rules
1. Secrets are not stored in compiled artifacts.
- only env var names such as `api_key_env` may be preserved

2. Relative paths are resolved against `project_root` during compile.

3. Route binding precedence:
- explicit `routing.routes`
- inferred route from `models.*.route` or `models.*.routes`
- default unbound route hint

4. Compiled artifacts remain canonical.
- `workspace_brief.json`
- `model_endpoints.json`
- `model_routing.json`

## 7. Example
```json
{
  "version": 1,
  "profile": "hybrid_local_exec",
  "workspace": {
    "workspace_key": "alpha",
    "project_alias": "O2",
    "project_overview": "alpha runtime",
    "doc_roots": ["docs"],
    "canonical_todo_path": "TODO.md",
    "canonical_runbook_paths": ["docs/RUNBOOK.md"],
    "background_runner_target": "local_tmux",
    "run_lock_mode_default": "open",
    "background_runner_slot_limits": {
      "local_tmux": 1,
      "github_runner": 1,
      "remote_worker": 1
    }
  },
  "models": {
    "qwen_local": {
      "provider_kind": "ollama",
      "base_url_env": "OLLAMA_BASE_URL",
      "model": "qwen3-coder:30b",
      "route": "background_worker_primary"
    },
    "gptoss_local": {
      "provider_kind": "ollama",
      "base_url_env": "OLLAMA_BASE_URL",
      "model": "gpt-oss:120b",
      "route": "background_worker_escalation"
    },
    "gemma_local": {
      "provider_kind": "ollama",
      "base_url_env": "OLLAMA_BASE_URL",
      "model": "gemma4:26b",
      "route": "research_synthesis"
    },
    "judge_claude": {
      "provider_kind": "anthropic",
      "model": "claude-opus-4.1",
      "api_key_env": "ANTHROPIC_API_KEY",
      "route": "offdesk_judge",
      "local": false
    }
  },
  "harness": {
    "on_desk": {"kind": "claude_code"},
    "off_desk": {"kind": "aoe_orch_control"},
    "off_desk_executor": {"kind": "local_tmux"}
  }
}
```

## 8. Compile Command
```bash
python3 scripts/gateway/aoe_tg_stack_compile.py \
  --project-root /path/to/project \
  --team-dir /path/to/project/.aoe-team \
  --manifest /path/to/project/aoe_stack.json \
  --env-file /path/to/project/.env
```

## 9. Current Scope
- compiler output only
- no live provider inference yet
- no dashboard edit form yet

## 10. References
- `docs/WORKSPACE_ONBOARDING_SPEC.md`
- `docs/MODEL_ENDPOINT_ADAPTER_SPEC.md`
- `docs/EXECUTOR_ADAPTER_ARCHITECTURE.md`
