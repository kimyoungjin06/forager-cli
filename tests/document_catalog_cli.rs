use anyhow::{Context, Result};
use forager::document_lifecycle::{
    lookup_observation, scan_project, search_project, BodyCoverage, CatalogOptions,
    ClassificationAuthority, CrosswalkStatus, DocumentCatalogEntry, DocumentCatalogV1,
    DocumentCatalogV2, DocumentSearchV2, ExclusionReason, GitState, IdentityReferenceKind,
    LifecycleProjectionStatusV1, MediaFamily, RetentionDisposition, ScanBudgets, ScanLimit,
};
use git2::Repository;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use tempfile::tempdir;

fn app_dir(home: &Path) -> PathBuf {
    if cfg!(target_os = "linux") {
        home.join("xdg/forager")
    } else {
        home.join(".forager")
    }
}

fn forager(home: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_forager"));
    command
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home.join("xdg"))
        .env_remove("AGENT_OF_EMPIRES_PROFILE")
        .env_remove("FORAGER_PROFILE");
    command
}

fn successful_json(output: Output) -> Result<Value> {
    if !output.status.success() {
        anyhow::bail!(
            "command failed\nstdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
    }
    serde_json::from_slice(&output.stdout).context("parse CLI JSON")
}

fn entry<'a>(catalog: &'a DocumentCatalogV1, relative_path: &str) -> &'a DocumentCatalogEntry {
    catalog
        .entries
        .iter()
        .find(|entry| entry.relative_path == relative_path)
        .unwrap_or_else(|| panic!("missing catalog entry {relative_path}"))
}

fn write(path: &Path, contents: impl AsRef<[u8]>) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, contents)?;
    Ok(())
}

fn initialize_fixture(root: &Path) -> Result<()> {
    fs::create_dir_all(root)?;
    write(
        &root.join("README.md"),
        "# Project\n\n[Guide](docs/guide.md)\n",
    )?;
    write(&root.join("docs/guide.md"), "same exact guide bytes\n")?;
    write(&root.join("docs/guide-copy.md"), "same exact guide bytes\n")?;
    write(&root.join("reports/report.pdf"), b"%PDF-1.7\nneedle-in-pdf")?;
    write(
        &root.join("reports/brief.docx"),
        b"PK\x03\x04needle-in-office",
    )?;
    write(&root.join("ignored/private.md"), "ignored but observable\n")?;
    write(&root.join("draft.txt"), "untracked draft\n")?;
    write(&root.join(".gitignore"), "ignored/\n")?;

    let repository = Repository::init(root)?;
    let mut index = repository.index()?;
    index.add_path(Path::new("README.md"))?;
    index.add_path(Path::new("docs/guide.md"))?;
    index.add_path(Path::new("docs/guide-copy.md"))?;
    index.add_path(Path::new("reports/report.pdf"))?;
    index.add_path(Path::new("reports/brief.docx"))?;
    index.add_path(Path::new(".gitignore"))?;
    index.write()?;
    Ok(())
}

#[test]
fn lifecycle_catalog_with_missing_profile_is_read_only_and_fail_open() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    let home = temp.path().join("home");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(&root)?;
    write(&root.join("README.md"), "# Project\n")?;
    let before = path_manifest(&root)?;

    let output = forager(&home)
        .args([
            "project",
            "document-catalog",
            root.to_str().context("UTF-8 root")?,
            "--project-key",
            "missing-profile",
            "--lifecycle",
            "--json",
        ])
        .output()?;
    let catalog: DocumentCatalogV2 = serde_json::from_value(successful_json(output)?)?;
    catalog.validate()?;
    assert_eq!(
        catalog.lifecycle.status,
        LifecycleProjectionStatusV1::Absent
    );
    assert!(!catalog.lifecycle.filtering_authorized);
    assert!(catalog.entries.iter().all(|entry| {
        entry.document_id.is_none()
            && entry.lifecycle_stale
            && entry.visibility == forager::document_lifecycle::SearchVisibilityV2::VisibleFailOpen
    }));
    assert_eq!(path_manifest(&root)?, before);
    assert!(!app_dir(&home).exists());
    Ok(())
}

#[test]
fn lifecycle_search_with_missing_profile_is_read_only_and_fail_open() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    let home = temp.path().join("home");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(&root)?;
    write(&root.join("README.md"), "# Project search needle\n")?;
    let before = path_manifest(&root)?;

    let output = forager(&home)
        .args([
            "project",
            "document-search",
            root.to_str().context("UTF-8 root")?,
            "--project-key",
            "missing-profile-search",
            "--query",
            "search needle",
            "--lifecycle",
            "--json",
        ])
        .output()?;
    let search: DocumentSearchV2 = serde_json::from_value(successful_json(output)?)?;
    let bodies = BTreeMap::from([(
        search.matches[0].entry.observation.observation_id.clone(),
        "# Project search needle\n".to_string(),
    )]);
    search.validate_against_live_bodies(&bodies)?;
    assert_eq!(
        search.catalog.lifecycle.status,
        LifecycleProjectionStatusV1::Absent
    );
    assert!(!search.catalog.lifecycle.filtering_authorized);
    assert_eq!(search.matches.len(), 1);
    assert_eq!(
        search.matches[0].entry.visibility,
        forager::document_lifecycle::SearchVisibilityV2::VisibleFailOpen
    );
    assert_eq!(search.cold_matches_omitted, 0);
    assert_eq!(path_manifest(&root)?, before);
    assert!(!app_dir(&home).exists());
    Ok(())
}

fn path_manifest(root: &Path) -> Result<Vec<String>> {
    fn visit(root: &Path, directory: &Path, paths: &mut Vec<String>) -> Result<()> {
        let mut children = fs::read_dir(directory)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<std::io::Result<Vec<_>>>()?;
        children.sort();
        for child in children {
            paths.push(
                child
                    .strip_prefix(root)?
                    .to_string_lossy()
                    .replace('\\', "/"),
            );
            if fs::symlink_metadata(&child)?.is_dir() {
                visit(root, &child, paths)?;
            }
        }
        Ok(())
    }

    let mut paths = Vec::new();
    visit(root, root, &mut paths)?;
    Ok(paths)
}

#[test]
fn catalog_observes_links_duplicates_git_and_metadata_without_writes() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    let home = temp.path().join("home");
    fs::create_dir_all(&home)?;
    initialize_fixture(&root)?;
    let before = path_manifest(&root)?;

    let output = forager(&home)
        .args([
            "project",
            "document-catalog",
            root.to_str().context("UTF-8 root")?,
            "--project-key",
            "fixture-project",
            "--json",
        ])
        .output()?;
    let value = successful_json(output)?;
    let catalog: DocumentCatalogV1 = serde_json::from_value(value.clone())?;
    catalog.validate()?;

    assert_eq!(catalog.project_key, "fixture-project");
    assert!(catalog.scan.complete);
    assert_eq!(entry(&catalog, "README.md").git_state, GitState::Tracked);
    assert_eq!(entry(&catalog, "draft.txt").git_state, GitState::Untracked);
    assert_eq!(
        entry(&catalog, "ignored/private.md").git_state,
        GitState::Ignored
    );
    assert_eq!(
        entry(&catalog, "ignored/private.md").retention_disposition,
        RetentionDisposition::Unclassified
    );
    assert!(entry(&catalog, "ignored/private.md")
        .retention_authority
        .is_none());

    let readme = entry(&catalog, "README.md");
    assert_eq!(
        readme.role_authority,
        Some(ClassificationAuthority::Inferred)
    );
    assert!(readme.generated.is_none());
    assert!(readme.generated_authority.is_none());
    let guide = entry(&catalog, "docs/guide.md");
    let guide_copy = entry(&catalog, "docs/guide-copy.md");
    assert!(readme.outbound_refs.iter().any(|reference| {
        reference.kind == IdentityReferenceKind::Observation
            && reference.value == guide.observation_id
    }));
    assert_eq!(guide.inbound_ref_count, 1);
    assert!(guide.entrypoint_refs.iter().any(|reference| {
        reference.kind == IdentityReferenceKind::Observation
            && reference.value == readme.observation_id
    }));
    assert!(guide.exact_duplicate_refs.iter().any(|reference| {
        reference.kind == IdentityReferenceKind::Observation
            && reference.value == guide_copy.observation_id
    }));
    assert!(guide_copy.exact_duplicate_refs.iter().any(|reference| {
        reference.kind == IdentityReferenceKind::Observation
            && reference.value == guide.observation_id
    }));

    for family in [MediaFamily::Pdf, MediaFamily::Docx] {
        let coverage = catalog
            .coverage
            .iter()
            .find(|coverage| coverage.media_family == family)
            .context("metadata-only coverage row")?;
        assert_eq!(coverage.body_coverage, BodyCoverage::MetadataOnly);
        assert_eq!(coverage.body_extracted_files, 0);
    }
    let serialized = serde_json::to_string(&value)?;
    assert!(!serialized.contains("deletion_recommendation"));
    assert!(!serialized.contains("disposal_recommendation"));
    assert_eq!(before, path_manifest(&root)?);
    assert!(!app_dir(&home).exists());
    assert!(!home.join(".config/forager").exists());
    Ok(())
}

#[test]
fn repeated_scans_keep_order_observation_and_snapshot_identity_deterministic() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    initialize_fixture(&root)?;

    let first = scan_project(&root, &CatalogOptions::default())?;
    let second = scan_project(&root, &CatalogOptions::default())?;
    assert_eq!(first.entries, second.entries);
    assert_eq!(first.coverage, second.coverage);
    assert_eq!(first.scan.complete, second.scan.complete);
    assert_eq!(first.scan.reached_limits, second.scan.reached_limits);
    assert_eq!(first.scan.excluded_scope, second.scan.excluded_scope);
    assert_eq!(first.scan.incomplete_scope, second.scan.incomplete_scope);
    assert_eq!(
        first.scan.usage.files_observed,
        second.scan.usage.files_observed
    );
    assert_eq!(
        first.scan.usage.bytes_hashed,
        second.scan.usage.bytes_hashed
    );
    assert_eq!(
        first.source_identities.filesystem_snapshot.identity,
        second.source_identities.filesystem_snapshot.identity
    );
    assert_eq!(
        first.source_identities.filesystem_snapshot.content_sha256,
        second.source_identities.filesystem_snapshot.content_sha256
    );
    Ok(())
}

#[test]
fn live_search_observes_new_text_and_supports_exact_observation_lookup() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    write(&root.join("README.md"), "initial catalog\n")?;
    write(&root.join("reference.pdf"), b"%PDF hidden-needle")?;
    write(&root.join("reference.docx"), b"PK\x03\x04hidden-office")?;

    let snapshot = scan_project(&root, &CatalogOptions::default())?;
    assert_eq!(snapshot.entries.len(), 3);
    write(
        &root.join("notes/new.txt"),
        "a newly created live-search-needle document\n",
    )?;

    let search = search_project(&root, &CatalogOptions::default(), "live-search-needle")?;
    assert!(!search.freshness.stale);
    assert_eq!(search.freshness.mode, "live_bounded_scan");
    assert_eq!(search.matches.len(), 1);
    assert_eq!(search.matches[0].entry.relative_path, "notes/new.txt");
    let observation_id = search.matches[0].entry.observation_id.clone();
    let detail = lookup_observation(&root, &CatalogOptions::default(), &observation_id)?;
    assert_eq!(detail.matches.len(), 1);
    assert_eq!(detail.matches[0].entry.relative_path, "notes/new.txt");
    assert_eq!(detail.matches[0].entry.observation_id, observation_id);

    let missing = search_project(&root, &CatalogOptions::default(), "not-present-anywhere")?;
    let negative = missing
        .negative_evidence
        .as_ref()
        .context("missing query must carry negative evidence")?;
    assert!(!negative.absence_established);
    assert_eq!(
        negative.metadata_only_families,
        vec![MediaFamily::Pdf, MediaFamily::Docx]
    );
    assert!(
        !missing
            .authority_boundary
            .negative_owner_conclusion_authorized
    );
    assert!(!missing.authority_boundary.lifecycle_filtering_applied);

    let mut invalid_authority = missing.clone();
    invalid_authority
        .authority_boundary
        .lifecycle_filtering_applied = true;
    assert!(invalid_authority.validate().is_err());
    let mut missing_negative_evidence = missing.clone();
    missing_negative_evidence.negative_evidence = None;
    assert!(missing_negative_evidence.validate().is_err());
    let mut false_absence = missing;
    false_absence
        .negative_evidence
        .as_mut()
        .context("negative evidence")?
        .absence_established = true;
    assert!(false_absence.validate().is_err());
    Ok(())
}

#[test]
fn complete_supported_text_scan_can_establish_bounded_content_absence() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    write(&root.join("README.md"), "only supported text\n")?;

    let result = search_project(&root, &CatalogOptions::default(), "missing")?;
    assert!(result.scan.complete);
    assert!(
        result
            .negative_evidence
            .as_ref()
            .context("negative evidence")?
            .absence_established
    );
    assert!(
        !result
            .authority_boundary
            .negative_owner_conclusion_authorized
    );
    Ok(())
}

#[test]
fn incomplete_scan_never_establishes_absence() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    write(&root.join("a.md"), "first\n")?;
    write(&root.join("docs/c.md"), "second\n")?;
    write(&root.join("docs/d.md"), "third\n")?;
    write(&root.join("z.md"), "fourth\n")?;
    let options = CatalogOptions {
        project_key: Some("bounded".to_string()),
        budgets: ScanBudgets {
            max_files: 2,
            max_directory_entries: 100,
            max_total_bytes: 1024,
            max_file_bytes: 1024,
            max_elapsed_ms: 5_000,
        },
    };

    let result = search_project(&root, &options, "missing")?;
    assert!(!result.scan.complete);
    assert_eq!(result.scan.reached_limits, vec![ScanLimit::FileCount]);
    assert_eq!(
        result
            .scan
            .incomplete_scope
            .iter()
            .map(|scope| scope.relative_path.as_str())
            .collect::<Vec<_>>(),
        vec!["docs/d.md", "z.md"]
    );
    assert!(
        !result
            .negative_evidence
            .context("negative evidence")?
            .absence_established
    );
    Ok(())
}

#[test]
fn byte_budget_keeps_metadata_but_blocks_false_complete_body_coverage() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    write(&root.join("large.md"), "body-hidden-by-budget\n")?;
    let options = CatalogOptions {
        project_key: Some("bounded-bytes".to_string()),
        budgets: ScanBudgets {
            max_files: 10,
            max_directory_entries: 100,
            max_total_bytes: 1024,
            max_file_bytes: 4,
            max_elapsed_ms: 5_000,
        },
    };

    let result = search_project(&root, &options, "body-hidden-by-budget")?;
    assert!(result.matches.is_empty());
    assert!(!result.scan.complete);
    assert_eq!(result.scan.reached_limits, vec![ScanLimit::PerFileBytes]);
    assert_eq!(result.scan.usage.files_observed, 1);
    assert_eq!(result.scan.usage.bytes_hashed, 0);
    assert_eq!(result.coverage[0].body_coverage, BodyCoverage::Partial);
    assert!(
        !result
            .negative_evidence
            .context("negative evidence")?
            .absence_established
    );
    Ok(())
}

#[test]
fn directory_entry_budget_bounds_enumeration_and_marks_covering_scope() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    for name in ["a.md", "b.md", "c.md", "d.md"] {
        write(&root.join(name), format!("{name}\n"))?;
    }
    let options = CatalogOptions {
        project_key: Some("bounded-directory".to_string()),
        budgets: ScanBudgets {
            max_files: 100,
            max_directory_entries: 2,
            max_total_bytes: 1024,
            max_file_bytes: 1024,
            max_elapsed_ms: 5_000,
        },
    };

    let catalog = scan_project(&root, &options)?;
    assert!(!catalog.scan.complete);
    assert_eq!(
        catalog.scan.reached_limits,
        vec![ScanLimit::DirectoryEntries]
    );
    assert_eq!(catalog.scan.usage.directory_entries_observed, 2);
    assert!(catalog.entries.is_empty());
    assert_eq!(catalog.scan.incomplete_scope.len(), 1);
    assert_eq!(catalog.scan.incomplete_scope[0].relative_path, ".");
    Ok(())
}

#[cfg(unix)]
#[test]
fn broad_roots_and_symlink_traversal_fail_closed() -> Result<()> {
    use std::os::unix::fs::symlink;

    assert!(scan_project(Path::new("/"), &CatalogOptions::default()).is_err());
    assert!(scan_project(Path::new("/tmp"), &CatalogOptions::default()).is_err());

    let temp = tempdir()?;
    let root = temp.path().join("project");
    let outside = temp.path().join("outside");
    fs::create_dir_all(root.join("docs"))?;
    fs::create_dir_all(&outside)?;
    write(&root.join("README.md"), "bounded\n")?;
    write(&outside.join("secret.md"), "outside-secret-needle\n")?;
    symlink(outside.join("secret.md"), root.join("docs/external.md"))?;
    symlink(&root, temp.path().join("project-link"))?;

    assert!(scan_project(
        &temp.path().join("project-link"),
        &CatalogOptions::default()
    )
    .is_err());
    let catalog = scan_project(&root, &CatalogOptions::default())?;
    assert!(catalog
        .scan
        .excluded_scope
        .iter()
        .any(|scope| scope.relative_path == "docs/external.md"
            && scope.reason == ExclusionReason::SymlinkEscape));
    assert!(catalog
        .entries
        .iter()
        .all(|entry| entry.relative_path != "docs/external.md"));
    let result = search_project(&root, &CatalogOptions::default(), "outside-secret-needle")?;
    assert!(result.matches.is_empty());
    Ok(())
}

#[test]
fn snapshot_hash_changes_when_a_new_supported_document_appears() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    write(&root.join("README.md"), "one\n")?;
    let first = scan_project(&root, &CatalogOptions::default())?;
    write(&root.join("new.md"), "two\n")?;
    let second = scan_project(&root, &CatalogOptions::default())?;
    assert_ne!(
        first.source_identities.filesystem_snapshot.content_sha256,
        second.source_identities.filesystem_snapshot.content_sha256
    );
    Ok(())
}

#[test]
fn snapshot_hash_binds_git_state_even_when_document_bytes_do_not_change() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    Repository::init(&root)?;
    write(&root.join("ignored.md"), "unchanged bytes\n")?;
    write(&root.join(".gitignore"), "ignored.md\n")?;

    let ignored = scan_project(&root, &CatalogOptions::default())?;
    assert_eq!(entry(&ignored, "ignored.md").git_state, GitState::Ignored);
    write(&root.join(".gitignore"), "")?;
    let untracked = scan_project(&root, &CatalogOptions::default())?;
    assert_eq!(
        entry(&untracked, "ignored.md").git_state,
        GitState::Untracked
    );
    assert_eq!(
        entry(&ignored, "ignored.md").content_sha256,
        entry(&untracked, "ignored.md").content_sha256
    );
    assert_ne!(
        ignored.source_identities.filesystem_snapshot.content_sha256,
        untracked
            .source_identities
            .filesystem_snapshot
            .content_sha256
    );
    Ok(())
}

#[test]
fn human_output_exposes_freshness_budgets_coverage_and_incomplete_scope() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    let home = temp.path().join("home");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(&root)?;
    write(&root.join("a.md"), "first\n")?;
    write(&root.join("b.md"), "second\n")?;
    write(&root.join("reference.pdf"), b"%PDF metadata only")?;

    let catalog = forager(&home)
        .args([
            "project",
            "document-catalog",
            root.to_str().context("UTF-8 root")?,
            "--max-files",
            "1",
        ])
        .output()?;
    assert!(catalog.status.success());
    let catalog_text = String::from_utf8(catalog.stdout)?;
    assert!(catalog_text.contains("Freshness: live snapshot"));
    assert!(catalog_text.contains("Budget usage: files 1/1"));
    assert!(catalog_text.contains("directory entries"));
    assert!(catalog_text.contains("Reached limits: FileCount"));
    assert!(catalog_text.contains("Coverage:"));
    assert!(catalog_text.contains("Incomplete scope:"));
    assert!(catalog_text.contains("b.md (BudgetTruncation)"));
    assert!(catalog_text.contains("reference.pdf (BudgetTruncation)"));

    let search = forager(&home)
        .args([
            "project",
            "document-search",
            root.to_str().context("UTF-8 root")?,
            "--query",
            "missing",
        ])
        .output()?;
    assert!(search.status.success());
    let search_text = String::from_utf8(search.stdout)?;
    assert!(search_text.contains("Search freshness: live_bounded_scan (stale=false)"));
    assert!(search_text.contains("Reached limits: none"));
    assert!(search_text.contains("Pdf: metadata 1/1, body 0/0, extraction errors 0, MetadataOnly"));
    assert!(search_text.contains("Incomplete scope:\n- none"));
    assert!(search_text.contains("Absence established: false"));
    Ok(())
}

#[test]
fn every_observation_remains_catalog_only_without_lifecycle_authority() -> Result<()> {
    let temp = tempdir()?;
    let root = temp.path().join("project");
    fs::create_dir_all(&root)?;
    write(&root.join("ARCHIVE.md"), "historical but visible\n")?;
    let catalog = scan_project(&root, &CatalogOptions::default())?;

    let by_observation: BTreeMap<_, _> = catalog
        .crosswalk_findings
        .iter()
        .map(|finding| (finding.observation_id.as_str(), finding.status))
        .collect();
    assert_eq!(by_observation.len(), catalog.entries.len());
    for entry in &catalog.entries {
        assert_eq!(
            by_observation.get(entry.observation_id.as_str()),
            Some(&CrosswalkStatus::CatalogOnly)
        );
        assert!(entry.document_id.is_none());
        assert!(entry.artifact_id.is_none());
        assert!(entry.activity_authority.is_none());
        assert!(entry.retention_authority.is_none());
        assert_eq!(
            entry.retention_disposition,
            RetentionDisposition::Unclassified
        );
    }
    assert!(
        !catalog
            .authority_boundary
            .search_visibility_mutation_authorized
    );
    assert!(!catalog.authority_boundary.file_mutation_authorized);
    Ok(())
}
