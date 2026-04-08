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
- dashboard:
  - `Overview`
  - `Offdesk`
  - `Runtime Detail`
  - `Recovery`

## 5. Current Scope
- This layer does not perform live model inference.
- It is a control-plane seam only:
  - registry normalization
  - route normalization
  - route resolution
  - operator summary

## 6. Future Attachment Path
1. Receive GPU server endpoint details
  - host/ip
  - port
  - model name
  - auth env var if needed
2. Add endpoint rows to `model_endpoints.json`
3. Bind route IDs in `model_routing.json`
4. Attach one or more executor/provider adapters to consume the resolved route

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
