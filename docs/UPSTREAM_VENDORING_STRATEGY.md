# Upstream Vendoring Strategy

## 1. Purpose
- Define how upstream reusable modules are imported without turning them into canonical runtime owners.

## 2. Current Candidate
- `revfactory/harness`
  - repo:
    - `https://github.com/revfactory/harness`
  - placement:
    - `vendor/revfactory-harness`
  - current import mode:
    - `git subtree`

## 3. Preferred Method
- use `git subtree`

## 4. Why `git subtree`
1. easier operator workflow than submodules
2. upstream history can still be pulled forward
3. local code can treat vendor path as normal files
4. avoids making runtime/bootstrap depend on submodule init state

## 5. Why Not Fork-First
- we do not want to become primary maintainers of a parallel harness runtime
- the import target is the authoring module, not the execution core

## 6. Hard Rules
1. vendor code stays behind an adapter boundary
2. vendor code does not own task/runtime truth
3. vendor updates are pinned deliberately, not auto-followed
4. runtime can operate even if vendor tree is absent

## 7. Near-Term Steps
1. keep read-only adapter seam in place
2. export authoring plans through:
   - `scripts/gateway/aoe_tg_harness_authoring_export.py`
3. treat generated `.claude/agents` and `.claude/skills` as output products, not canonical runtime state

## 8. References
- `docs/HARNESS_AUTHORING_ADAPTER_SPEC.md`
- `docs/EXECUTOR_ADAPTER_ARCHITECTURE.md`
