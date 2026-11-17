# Document Lifecycle Contract Fixtures

These fixtures freeze the Phase 0 and detached Phase 2A wire contracts. They do
not represent live project scans or authorize search filtering, lifecycle state
changes, profile writes, file movement, quarantine, or disposal.

- `software/` covers a bounded tracked software repository.
- `research_longrun/` covers reviewed durable identities, an artifact
  crosswalk, and a metadata-only PDF that remains active and preserved.
- `non_git/` covers a bounded project without Git metadata.
- `mixed_git_states/` proves that ignored and untracked documents stay
  observable and unclassified rather than becoming disposal candidates.
- `document_identity_crosswalk.v1.json` freezes identity precedence and the
  single approval-ledger retention target behavior.
- `document_hygiene_report.v1.json` freezes an advisory incomplete-assessment
  report that cannot claim clean coverage.
- `phase2a/` freezes two canonical append-only event envelopes, their approval
  source, payload, event, and ordered-chain hashes. The files are inert loader
  inputs and do not authorize a production ledger append.

All paths are inert fixture identities. Tests must parse these files without
reading or writing real user state.
