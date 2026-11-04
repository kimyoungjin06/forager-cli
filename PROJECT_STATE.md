# Project State

Updated: 2026-11-04

## Current focus

This branch publishes the qualified Forager core snapshot used for the local
maintenance cutover. The source checkpoint commit `3e01fbc` is based on
`8172701` and preserves the exact 487 core source, test, helper, and
configuration inputs. A publication cleanup trims EOF blank lines in five
Python files with identical parsed ASTs. It is not a new tagged release.
The newer `origin/main` remote-session policy commit `2c8a01f` needs a separate
integration review before merging this snapshot into main.

The checkpoint covers constrained closeout writes, exact accepted-review
summaries, weekly allowance checks, bounded default Tokio workers, and shared
forager/aoe dispatch. The qualification boundary and practical limits are in the
[v0.14.0 maintenance notes](docs/releases/v0.14.0.md).

The next implementation package is **QA.1: isolate integration fixtures and
prove cleanup after success and failure**. Continue with source integration and
build identity, repeatable frontend replacement, and clearer review/delivery
holds. The sequence and completion criteria are in the existing
[project direction](docs/project-direction.md#maintenance-sequence-after-the-2026-11-04-core-checkpoint).

## Plan ownership and execution boundaries

Task IDs refer to the existing operator-owned continuity plan. Its full history,
plan transaction journal, live queues, credentials, operational backups, and
local qualification evidence are private workspace artifacts. Resolve that
existing plan before applying task dispositions. The public roadmap is a
product summary rather than a second authoritative queue.

C5.1a, independent local-model coding and context recovery, and C6.3-local, the
complete local-adapter two-project cycle, remain unqualified. Core acceptance
does not accept other projects' scientific findings. Planning does not launch
a new Offdesk run or authorize another service cutover.

## First reads

- [Repository instructions](AGENTS.md)
- [Project direction and maintenance sequence](docs/project-direction.md)
- [Operation cycle and morning agreement](docs/guides/operation-cycle.md)
- [Core maintenance notes](docs/releases/v0.14.0.md)
- [Hosted harness contract](docs/hosted-harness-agents.md)
- [Product overview](README.md)

## Validation

The core snapshot was qualified with targeted core tests, strict all-target
Clippy, formatting, and an optimized dev-release build. Publication checks run
from an isolated source worktree with private HOME/XDG directories, a separate
build directory, and denied provider/SSH command paths. The maintenance commit
records the exact checks and results. No website or cross-platform CI result is
inferred from those local checks.
