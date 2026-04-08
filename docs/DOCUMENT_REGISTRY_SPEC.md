# Document Registry Spec

## 1. Purpose
- Define the canonical metadata layer for project documents.
- Treat documents as structured knowledge objects, not just files on disk.
- Make dashboard/runtime/document-flow features depend on a stable registry instead of arbitrary folder reads.

## 2. Why This Layer Is Needed
- A repo `docs/` directory is not a knowledge system by itself.
- The control plane needs to know:
  - which documents are canonical
  - which are stale
  - which belong to runbooks, specs, ADRs, incidents, or references
  - which documents should be loaded into task-scoped context packs
- Without a registry, agents either:
  - over-read entire trees
  - or miss the one document that actually governs the work

## 3. Canonical Object

### 3.1 DocumentRecord
- `doc_id`
- `workspace_key`
- `path`
- `doc_type`
  - `spec`
  - `runbook`
  - `adr`
  - `ops`
  - `research`
  - `incident`
  - `reference`
  - `note`
- `source_kind`
  - `markdown`
  - `pdf`
  - `docx`
  - `external`
  - `other`
- `title`
- `owner`
- `tags[]`
- `keywords[]`
- `summary_card`
- `canonical`
- `freshness_class`
  - `fresh`
  - `review_soon`
  - `stale`
- `updated_at`
- `depends_on[]`
- `supersedes[]`
- `related_runtime_surfaces[]`
- `ingest_status`
  - `indexed`
  - `ignored`
  - `stale`
  - `error`

### 3.2 Registry Policy
- `DocumentRegistry` is metadata truth, not fulltext truth.
- The registry must make canonical references explicit instead of forcing downstream code to infer them from filenames.
- External references must keep provenance in the record; copied snippets without source identity are not canonical registry entries.
- A document may be present in the filesystem and still be:
  - `ignored`
  - `stale`
  - non-canonical

## 4. Build Lifecycle

### 4.1 Scan Inputs
- source of truth:
  - `WorkspaceBrief.doc_roots`
  - `WorkspaceBrief.doc_ignore_globs`
- optional supplemental sources:
  - exported external docs
  - generated summaries
  - incident artifacts

### 4.2 Classify
- determine:
  - `doc_type`
  - `source_kind`
  - canonical/non-canonical status
  - freshness class
- classification should be deterministic and inspectable

### 4.3 Summarize
- every indexed record should have a short `summary_card`
- summary generation is not a license to delete provenance
- the registry record should point back to the file path and upstream relations

### 4.4 Relate
- registry should capture:
  - which runbooks support which runtime surface
  - which specs supersede earlier specs
  - which incidents or research notes depend on other docs

## 5. Hard Rules
- The registry must not silently treat every `.md` file as equally canonical.
- Files under generated/runtime artifact paths should be ignored unless explicitly designated as durable knowledge.
- A document marked `stale` must stay visible as stale; do not silently hide it from downstream selection.
- Registry build failures must be surfaced as structured ingest errors, not hidden console warnings.

## 6. Suggested Artifact Shape
- canonical path:
  - `<team_dir>/document_registry.json`
- optional future per-record detail:
  - `<team_dir>/document_registry_cards/<doc_id>.json`

## 7. Relation To Other Layers
- upstream:
  - `WorkspaceBrief`
- downstream:
  - `ContextPack`
  - `Project Flow Compiler`
  - dashboard document-flow cards
  - future history/search surfaces
- non-goal:
  - replacing the raw documents themselves

## 8. Dashboard / Operator Usage
- operator surfaces should be able to answer:
  - what are the canonical docs for this project?
  - which runbooks/specs are stale?
  - which doc is governing the current work?
- initial read-only surfaces are enough; editing/recategorization UI can come later.

## 9. Near-Term Implementation Order
1. registry artifact format and loader
2. deterministic scanner over `WorkspaceBrief.doc_roots`
3. dashboard runtime card summary
4. compiler input for task-scoped context packs

## 10. References
- `docs/WORKSPACE_ONBOARDING_SPEC.md`
- `docs/PROJECT_FLOW_COMPILER_SPEC.md`
- `docs/ROADMAP.md`
