# Forager Rename Plan

Forager is the new primary product name. The `aoe` binary and existing storage
paths remain compatibility surfaces during the transition.

## Phase 1

| Surface | Decision |
| --- | --- |
| Primary CLI | Add `forager` |
| Legacy CLI | Keep `aoe` as an alias |
| Help text | Prefer `forager` |
| Profile env | Prefer `FORAGER_PROFILE`, fall back to `AGENT_OF_EMPIRES_PROFILE` |
| Debug env | Prefer `FORAGER_DEBUG`, fall back to `AGENT_OF_EMPIRES_DEBUG` |
| Global data dir | Use `forager`, fall back to `agent-of-empires` |
| Repo config dir | Use `.forager/config.toml`, fall back to `.aoe/config.toml` |
| Legacy sandbox container names | Use stored names, with `forager-sandbox-*` / `aoe-sandbox-*` fallback cleanup |
| Rust crate name | Use `forager` |

## Completed Phase 2

- Fresh installs write global data to `forager`.
- Existing `agent-of-empires` global data remains readable and writable when no
  `forager` directory exists.
- Fresh repo init writes `.forager/config.toml`.
- Existing `.aoe/config.toml` remains readable and writable when no
  `.forager/config.toml` exists.

## Completed Phase 3

- `forager doctor` reports the active profile source, global data path, and
  repo config path without creating storage directories.
- Human and JSON output both identify whether Forager is using primary paths,
  legacy AoE paths, or a new primary path that would be created by normal CLI
  commands.

## Completed Phase 4

- `forager migrate aoe` copies existing legacy AoE global data and current repo
  config into Forager primary paths.
- Migration is conflict-safe: legacy sources are preserved, existing Forager
  targets are not overwritten, and any conflict blocks all copy operations.
- `--dry-run` and `--json` expose the same migration plan for scripts and manual
  review.

## Completed Phase 5

- Invoking the `aoe` compatibility binary now emits a soft deprecation warning
  for human-facing commands: use `forager` instead.
- Script-safe surfaces stay quiet: `--json`, `--quiet`/`-q`, shell completions,
  and `tmux status` do not emit the warning.
- The install script now installs `forager` as the primary command and keeps
  `aoe` as a legacy alias.
- User-facing docs and help examples now prefer `forager`; legacy paths, Docker
  image/container names, and package/repository names remain compatibility
  surfaces for later phases.

## Completed Phase 6

- Release workflows now publish `forager-*` artifacts as the primary download
  names.
- Legacy `aoe-*` artifacts are still published from the same release so old
  installers and direct download links keep working during the transition.
- The install script downloads `forager-*` first and falls back to `aoe-*` for
  older releases.

## Completed Phase 7

- Public docs, website copy, mdBook metadata, contribution docs, demo scripts,
  and social preview assets now use Forager as the visible product name.
- Historical compatibility names remain where they are active surfaces:
  `aoe` binary alias, `.aoe/config.toml` fallback, legacy app data, and legacy
  release artifacts.

## Completed Phase 8

- New tmux agent sessions now use the `forager_` prefix.
- New paired terminal sessions now use `forager_term_`.
- Existing `aoe_` and `aoe_term_` sessions remain discoverable for attach,
  status checks, capture, rename, and kill flows.
- tmux status-bar metadata now uses `@forager_title`, `@forager_branch`, and
  `@forager_sandbox` as the primary options while still writing and reading
  legacy `@aoe_*` options for compatibility.

## Deferred Sandbox Image Work

- The inherited AoE sandbox code remains in the tree only as an audit surface.
- The current classification is tracked in `docs/sandbox-audit.md`.
- Official `forager-sandbox` / `forager-dev-sandbox` image publishing has been
  removed.
- Creating new sandbox sessions is deferred while Forager decides whether to
  benchmark AoE's approach, reimplement the feature, or remove it entirely.

## Completed Phase 10

- Repository metadata, install docs, release links, update checks, sound
  downloads, website links, mdBook links, and contribution docs now point to
  `github.com/kimyoungjin06/forager-cli`.
- GitHub Pages documentation links now use the renamed repository path:
  `https://kimyoungjin06.github.io/forager-cli/`.
- Legacy app data paths, the `aoe` compatibility binary, and fixture paths
  remain compatibility surfaces. The Rust package name was renamed later in
  Phase 20.

## Completed Phase 11

- Homebrew tap automation, install docs, badges, and uninstall handling were
  removed because Forager does not currently operate a Homebrew distribution.
- Docker sandbox guides, official image docs, and image publishing workflows
  were removed because Forager does not currently maintain sandbox images.

## Completed Phase 12

- Remaining inherited sandbox code is classified as compatibility, benchmark,
  or deletion-candidate surface in `docs/sandbox-audit.md`.
- New sandbox session creation is explicitly blocked while stored sandbox
  metadata remains readable for compatibility.
- User-facing docs now describe sandbox status as deferred instead of active.

## Completed Phase 13

- TUI new-session sandbox controls, image/env fields, inherited sandbox settings,
  and sandbox-specific loading copy were removed.
- `NewSessionData` and `InstanceParams` no longer carry sandbox creation
  parameters.
- Background TUI creation now exists for slow hook execution, not sandbox setup.

## Completed Phase 14

- TUI settings no longer exposes sandbox fields or writes sandbox config
  overrides.
- The sandbox custom-instruction editor dialog was removed because it was only
  reachable through the removed settings surface.
- Hook settings descriptions now state that hooks run on the host.

## Completed Phase 15

- Sandbox tests were reclassified as legacy metadata compatibility coverage.
- Docker-daemon lifecycle tests were removed because Forager no longer treats
  inherited sandbox creation as an active product feature.
- Remaining sandbox coverage now verifies readable stored metadata and stable
  Forager/legacy container names without starting Docker.

## Completed Phase 16

- Legacy sandbox sessions are now blocked from starting new Docker runtime
  sessions.
- TUI terminal view always opens the host paired terminal; the inherited
  container-terminal toggle and preview path were removed.
- The inherited sandbox config/environment sync modules were removed because
  they only supported new Docker sandbox creation.
- Legacy sandbox records are labeled as legacy metadata while delete cleanup
  remains available for stored container names.

## Completed Phase 17

- `SandboxConfig` and `SandboxConfigOverride` now retain only the legacy
  `auto_cleanup` policy.
- Retired inherited sandbox config keys remain parse-compatible where useful,
  but no longer exist as active public config fields.
- Container runtime selection is no longer configurable; legacy cleanup uses the
  default Docker-compatible cleanup path.

## Completed Phase 18

- The Apple Container runtime, enum-dispatch container abstraction, sandbox
  image/create/start helpers, and live Docker image tests were removed.
- `src/containers` now only supports detecting and removing stored legacy
  sandbox container names.
- Obsolete sandbox custom-instruction specs were removed because that UI and
  config surface no longer exists.

## Completed Phase 19

- User-facing `agent-of-empires.com` site metadata and generated CNAME output
  were removed in favor of the GitHub Pages repository URL.
- README and mdBook index no longer link to the old Agent of Empires YouTube
  handle.
- CLI update notices no longer recommend Homebrew commands because Forager does
  not currently maintain a Homebrew distribution.

## Completed Phase 20

- The Rust package and library crate are now named `forager`.
- Internal binaries, tests, examples, and xtask documentation generation now
  import the crate as `forager`.
- Debug logging examples now use `RUST_LOG=forager=debug`.
- Compatibility storage paths and the `aoe` binary alias are unaffected by the
  crate rename.

## Completed Phase 21

- The unused container-terminal tmux wrapper and its `forager_cterm_` /
  `aoe_cterm_` prefixes were removed.
- Legacy sandbox cleanup wording in CLI/TUI surfaces now says "legacy sandbox
  container" instead of presenting containers as an active feature.
- tmux status bar docs now describe `@forager_sandbox` as stored legacy metadata
  rather than a normal new-session field.

## Compatibility Retirement Policy

Compatibility surfaces stay only when they protect existing local data,
automation, or direct release downloads. They should not be expanded with new
features.

| Surface | Current Policy | Earliest Removal Gate |
| --- | --- | --- |
| `aoe` compatibility binary | Keep with human-facing deprecation warning. | Remove only after a release notes cycle explicitly says the alias will be removed. |
| `.aoe/config.toml` repo fallback | Keep readable and writable when no `.forager/config.toml` exists. | Remove only after `forager migrate aoe` has existed for at least one release cycle and docs show the migration path. |
| Legacy global data paths (`agent-of-empires`, `.agent-of-empires`) | Keep as fallback when no Forager primary path exists. | Remove only after migration is the documented default and `forager doctor` reports stale legacy paths clearly. |
| `AGENT_OF_EMPIRES_PROFILE` / `AGENT_OF_EMPIRES_DEBUG` | Keep as lower-priority env fallbacks. | Remove after CLI/docs stop mentioning them except in migration notes. |
| `aoe-*` release artifact fallback | Keep install-script fallback for older releases. | Remove after the installer no longer needs to support pre-Forager release names. |
| `@aoe_*` tmux metadata and `aoe_*` tmux session discovery | Keep for attach/status/cleanup of running legacy sessions. | Remove only after Forager stops supporting in-place discovery of already-running AoE tmux sessions. |
| `aoe-sandbox-*` container-name fallback | Keep for deletion of stored legacy sandbox metadata. | Remove when legacy sandbox cleanup support is removed. |

If a future cleanup removes any compatibility surface, it should update this
table, `forager doctor`, and release notes in the same change.

## Later Phases

- Decide whether to add a new custom marketing domain or Forager-owned YouTube
  handle.
- Decide whether sandboxing returns through AoE benchmarking, a Forager-native
  implementation, or full removal of inherited sandbox code.
