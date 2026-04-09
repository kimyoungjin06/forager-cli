# Model Endpoint Adapter Spec

## 1. Purpose
- Define a swappable model-serving seam for:
  - premium API models
  - local Ollama-served models
  - future OpenAI-compatible or custom endpoints
- Keep model endpoint ownership outside the canonical runtime truth.
- Ensure the control plane can inspect model routing without embedding secrets or hardcoding hosts.

## 2. Canonical Objects

### 2.1 Endpoint Registry
- Path:
  - `<team_dir>/model_endpoints.json`
- Purpose:
  - declare available model endpoints
  - declare whether they are enabled
  - expose only non-secret connection metadata
- Canonical fields:
  - `endpoint_id`
  - `provider_kind`
    - `anthropic`
    - `google`
    - `openai`
    - `openai_compatible`
    - `ollama`
    - `custom`
  - `base_url`
  - `model`
  - `api_key_env`
  - `enabled`
  - `local`
  - `supports_tools`
  - `supports_json`
  - `roles[]`
  - `notes`

### 2.2 Routing Policy
- Path:
  - `<team_dir>/model_routing.json`
- Purpose:
  - decide which endpoint should fill each logical model role
- Canonical routes:
  - `on_desk_primary`
  - `research_synthesis`
  - `offdesk_judge`
  - `background_worker_primary`
  - `background_worker_escalation`
- Canonical fields per route:
  - `endpoint_id`
  - `family_hint`
  - `model_hint`
  - `fallback_ids[]`
  - `summary_label`
  - `notes`

### 2.3 Project Runtime Hooks
- Stored in manager state:
  - `model_routing_profile`
  - `model_endpoint_overrides`
- Current use:
  - operator-facing summary only
- Future use:
  - project-specific routing override without changing global registry files

## 3. Hard Rules
1. Secrets do not live in manager state.
- only env var names such as `api_key_env` are stored in registry config

2. Model endpoint routing is not execution truth.
- canonical task/runtime truth remains:
  - `RequestContract`
  - `ExecutionBrief`
  - `FollowupBrief`
  - `Background Run Ticket`

3. Endpoint config must stay swappable.
- host, port, model name, and provider kind must be replaceable without code edits

4. Routing visibility must be operator-readable.
- `/orch status`
- dashboard runtime/offdesk/recovery surfaces
must all expose:
  - `model_routing_summary`
  - `model_registry_summary`

## 4. Current Operator Surface
- `/orch status O#`
  - `model_routing: ...`
  - `model_registry: ...`
- CLI probe:
  - `scripts/gateway/aoe_tg_model_endpoint_probe.py`
- dashboard:
  - `Overview`
  - `Offdesk`
  - `Runtime Detail`
  - `Recovery`

## 5. Current Scope
- Runtime automation does not yet perform general route-driven inference.
- Current shipped scope:
  - registry normalization
  - route normalization
  - route resolution
  - route/endpoint probing
  - operator summary
  - explicit bounded invoke helpers for provider-adapter testing
- Current explicit invoke helpers:
  - `scripts/gateway/aoe_tg_model_provider_adapter.py`
  - `scripts/gateway/aoe_tg_model_provider_invoke.py`
- example:
```bash
python3 scripts/gateway/aoe_tg_model_provider_invoke.py \
  --team-dir /path/to/.aoe-team \
  --route-id background_worker_primary \
  --prompt "Summarize the retry scope in 5 bullets."
```
- example:
```bash
python3 scripts/gateway/aoe_tg_model_provider_invoke.py \
  --team-dir /path/to/.aoe-team \
  --kind judge \
  --pack-profile followup_preview \
  --prompt "Decide whether the followup preview is actionable."
```
- Current policy:
  - background worker routes may block launch on failed worker probe
  - judge/escalation routes are recorded as launch metadata and surfaced in status/detail views
  - explicit invoke is opt-in for ad-hoc bounded checks
  - `local_background` may also consume a `provider_invoke` launch spec and execute one worker-bound provider call through the same route seam

## 6. Future Attachment Path
0. Declare topology in a stack manifest and compile it first.
  - `docs/AOE_STACK_MANIFEST_SPEC.md`
  - `scripts/gateway/aoe_tg_stack_compile.py`
1. Receive GPU server endpoint details
  - host/ip
  - port
  - model name
  - auth env var if needed
2. Add endpoint rows to `model_endpoints.json`
3. Bind route IDs in `model_routing.json`
4. Attach one or more executor/provider adapters to consume the resolved route

### 6.1 Seed Helper
- helper:
  - `scripts/gateway/aoe_tg_model_endpoint_seed.py`
- purpose:
  - write `model_endpoints.json` and `model_routing.json` for an Ollama-served model set
- example:
```bash
python3 scripts/gateway/aoe_tg_model_endpoint_seed.py \
  --team-dir /path/to/.aoe-team \
  --ollama-base-url http://172.16.0.37:11434 \
  --qwen-model qwen3-coder:30b \
  --gpt-oss-model gpt-oss:120b \
  --gemma-model gemma4:26b
```
- default binding policy:
  - `background_worker_primary` -> `qwen3-coder`
  - `background_worker_escalation` -> `gpt-oss`
  - `research_synthesis` -> `gemma4`
  - premium on-desk / judge routes remain unbound by default

## 7. Example
```json
{
  "version": 1,
  "endpoints": [
    {
      "endpoint_id": "ollama-qwen3",
      "provider_kind": "ollama",
      "base_url": "http://10.0.0.8:11434",
      "model": "qwen3-coder:30b",
      "enabled": true,
      "local": true,
      "supports_tools": false,
      "supports_json": true,
      "roles": ["background_worker_primary"]
    }
  ]
}
```

## 8. Stack Compiler
- compiler:
  - `scripts/gateway/aoe_tg_stack_compile.py`
- role:
  - read stack manifest + env overlay
  - write:
    - `workspace_brief.json`
    - `model_endpoints.json`
    - `model_routing.json`
- policy:
  - runtime reads compiled artifacts
  - runtime does not need raw manifest/env parsing for normal operation

```json
{
  "version": 1,
  "profile": "default",
  "routes": {
    "background_worker_primary": {
      "endpoint_id": "ollama-qwen3"
    }
  }
}
```
