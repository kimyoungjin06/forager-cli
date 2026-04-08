# Host-Native Execution Strategy

## 1. Recommendation
- current default backend execution mode should be:
  - host-native Python for the control plane
  - host-native local executors for `local_background` and `local_tmux`
  - Docker or runner-based isolation only for future remote workers

## 2. Why Not Full Docker Right Now
- current operator workflow depends on host-native access to:
  - project folders
  - `.aoe-team`
  - `tmux`
  - local model servers such as Ollama
- full containerization would add path, mount, session, and permission complexity before the stack topology is stable

## 3. Recommended Split

### 3.1 Host-Native
- gateway
- dashboard
- stack compiler
- workspace/document/context compilers
- `local_background`
- `local_tmux`

### 3.2 Later Container / Runner Targets
- `github_runner`
- `remote_worker`
- isolated batch workers

## 4. Operational Order
1. author `aoe_stack.json`
2. provide `.env` overlay
3. compile:
```bash
python3 scripts/gateway/aoe_tg_stack_compile.py \
  --project-root /path/to/project \
  --team-dir /path/to/project/.aoe-team \
  --manifest /path/to/project/aoe_stack.json \
  --env-file /path/to/project/.env
```
4. inspect:
  - `/orch status O#`
  - dashboard `Runtime Detail`
5. install host-native services if needed:
```bash
bash scripts/systemd/install_user_services.sh
```

## 5. Systemd Position
- `systemd --user` is the right near-term execution shell for:
  - always-on gateway
  - dashboard
  - heal/restart loop
- this keeps the control plane host-native while still making it operationally durable

## 6. Docker Position
- use Docker later for:
  - remote executor images
  - CI/runner parity
  - isolated off-desk worker environments
- do not force local control-plane services into Docker first

## 7. Policy
- compile topology first
- keep control-plane reads on compiled artifacts
- containerize only where isolation is worth the operational cost

## 8. References
- `docs/AOE_STACK_MANIFEST_SPEC.md`
- `docs/SYSTEMD_USER_SETUP.md`
- `docs/EXECUTOR_ADAPTER_ARCHITECTURE.md`
