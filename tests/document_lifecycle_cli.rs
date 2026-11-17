use anyhow::{Context, Result};
use forager::document_lifecycle::{
    load_document_lifecycle_ledger_from_root, retention_approval_metadata_sha256_v2, scan_project,
    CatalogOptions, DocumentCatalogV2, DocumentLifecycleLedgerLoadV1, DocumentSearchV2,
    HygieneSeverity, LifecycleLedgerLoadOptions, RetentionApprovalMetadataV2,
};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
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
    serde_json::from_slice(&output.stdout).context("parse lifecycle CLI JSON")
}

fn lifecycle_load_for_search(
    profile: &Path,
    search: &DocumentSearchV2,
) -> Result<(DocumentLifecycleLedgerLoadV1, String)> {
    let expectation = search
        .catalog
        .lifecycle
        .expectation
        .clone()
        .context("search lifecycle expectation")?;
    let root_instance = expectation.root_instance_identity_sha256.clone();
    let observed_at = search
        .catalog
        .lifecycle
        .source_identity
        .as_ref()
        .context("search lifecycle source")?
        .observed_at
        .clone();
    let load = load_document_lifecycle_ledger_from_root(
        profile,
        &expectation,
        &LifecycleLedgerLoadOptions {
            observed_at,
            ..LifecycleLedgerLoadOptions::default()
        },
    )?;
    Ok((load, root_instance))
}

fn mint_document(
    home: &Path,
    project: &Path,
    project_key: &str,
    observation_id: &str,
    reason: &str,
) -> Result<Value> {
    let project_arg = project.to_str().context("UTF-8 project path")?;
    let review = successful_json(
        forager(home)
            .args([
                "project",
                "document-lifecycle-review",
                project_arg,
                "mint-document",
                "--project-key",
                project_key,
                "--observation-id",
                observation_id,
                "--activity-state",
                "active",
                "--reason",
                reason,
                "--json",
            ])
            .output()?,
    )?;
    let proposal_sha256 = review["proposal_sha256"]
        .as_str()
        .context("mint proposal hash")?
        .to_string();
    let request = successful_json(
        forager(home)
            .args([
                "project",
                "document-lifecycle-request",
                project_arg,
                "mint-document",
                "--project-key",
                project_key,
                "--observation-id",
                observation_id,
                "--activity-state",
                "active",
                "--reason",
                reason,
                "--expected-proposal-sha256",
                &proposal_sha256,
                "--json",
            ])
            .output()?,
    )?;
    let approval_id = request["approval_id"]
        .as_str()
        .context("mint approval ID")?
        .to_string();
    successful_json(
        forager(home)
            .args([
                "offdesk",
                "ok",
                &approval_id,
                "--by",
                "test-operator",
                "--json",
            ])
            .output()?,
    )?;
    successful_json(
        forager(home)
            .args([
                "project",
                "document-lifecycle-apply",
                project_arg,
                "--approval-id",
                &approval_id,
                "--json",
            ])
            .output()?,
    )
}

fn supersede_document(
    home: &Path,
    project: &Path,
    project_key: &str,
    document_id: &str,
    successor_document_id: &str,
) -> Result<Value> {
    let project_arg = project.to_str().context("UTF-8 project path")?;
    let reason = "Replace the old document with its exact active successor.";
    let review = successful_json(
        forager(home)
            .args([
                "project",
                "document-lifecycle-review",
                project_arg,
                "activity-transition",
                "--project-key",
                project_key,
                "--document-id",
                document_id,
                "--activity-state",
                "superseded",
                "--successor-document-id",
                successor_document_id,
                "--reason",
                reason,
                "--json",
            ])
            .output()?,
    )?;
    let proposal_sha256 = review["proposal_sha256"]
        .as_str()
        .context("transition proposal hash")?
        .to_string();
    let request = successful_json(
        forager(home)
            .args([
                "project",
                "document-lifecycle-request",
                project_arg,
                "activity-transition",
                "--project-key",
                project_key,
                "--document-id",
                document_id,
                "--activity-state",
                "superseded",
                "--successor-document-id",
                successor_document_id,
                "--reason",
                reason,
                "--expected-proposal-sha256",
                &proposal_sha256,
                "--json",
            ])
            .output()?,
    )?;
    let approval_id = request["approval_id"]
        .as_str()
        .context("transition approval ID")?
        .to_string();
    successful_json(
        forager(home)
            .args([
                "offdesk",
                "ok",
                &approval_id,
                "--by",
                "test-operator",
                "--json",
            ])
            .output()?,
    )?;
    successful_json(
        forager(home)
            .args([
                "project",
                "document-lifecycle-apply",
                project_arg,
                "--approval-id",
                &approval_id,
                "--json",
            ])
            .output()?,
    )
}

fn project_manifest(root: &Path) -> Result<Vec<(String, Vec<u8>)>> {
    fn visit(root: &Path, directory: &Path, rows: &mut Vec<(String, Vec<u8>)>) -> Result<()> {
        let mut children = fs::read_dir(directory)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<std::io::Result<Vec<_>>>()?;
        children.sort();
        for child in children {
            if fs::symlink_metadata(&child)?.is_dir() {
                visit(root, &child, rows)?;
            } else {
                rows.push((
                    child
                        .strip_prefix(root)?
                        .to_string_lossy()
                        .replace('\\', "/"),
                    fs::read(child)?,
                ));
            }
        }
        Ok(())
    }

    let mut rows = Vec::new();
    visit(root, root, &mut rows)?;
    Ok(rows)
}

#[test]
fn retention_v2_catalog_target_deduplicates_and_appends_one_plan_event() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("home");
    let project = temp.path().join("project");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(project.join("docs"))?;
    fs::write(project.join("docs/report.md"), "# Retention target\n")?;
    let catalog = scan_project(
        &project,
        &CatalogOptions {
            project_key: Some("phase2f-cli".to_string()),
            ..CatalogOptions::default()
        },
    )?;
    let minted = mint_document(
        &home,
        &project,
        "phase2f-cli",
        &catalog.entries[0].observation_id,
        "Establish the exact document before retention review.",
    )?;
    let document_id = minted["event"]["payload"]["document_id"]
        .as_str()
        .context("minted document ID")?
        .to_string();
    let project_arg = project.to_str().context("UTF-8 project path")?;
    let project_before = project_manifest(&project)?;

    let review = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-review",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--document-id",
                &document_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(review["schema"], "retention_target_resolution.v2");
    assert_eq!(review["target"]["source_kind"], "document");
    assert_eq!(review["target"]["retention_target_id"], document_id);
    assert_eq!(review["artifact_index"]["schema"], "artifact_index.v2");
    assert_eq!(review["authority"]["read_only"], true);

    let request = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-request",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--document-id",
                &document_id,
                "--action",
                "keep",
                "--request-id",
                "first-retention-request",
                "--task-id",
                "first-retention-task",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(request["approval"]["metadata"]["kind"], "retention_v2");
    assert_eq!(
        request["approval"]["metadata"]["schema"],
        "retention_approval_metadata.v2"
    );
    let approval_id = request["approval"]["approval_id"]
        .as_str()
        .context("retention approval ID")?
        .to_string();

    let duplicate = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-request",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--document-id",
                &document_id,
                "--action",
                "keep",
                "--request-id",
                "different-request",
                "--task-id",
                "different-task",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(duplicate["approval"]["approval_id"], approval_id);

    let conflict = forager(&home)
        .args([
            "project",
            "retention-request",
            project_arg,
            "--project-key",
            "phase2f-cli",
            "--document-id",
            &document_id,
            "--action",
            "preserve",
            "--json",
        ])
        .output()?;
    assert!(!conflict.status.success());
    assert!(String::from_utf8_lossy(&conflict.stderr).contains("different unresolved action"));

    successful_json(
        forager(&home)
            .args([
                "offdesk",
                "ok",
                &approval_id,
                "--by",
                "test-operator",
                "--json",
            ])
            .output()?,
    )?;
    let profile = app_dir(&home).join("profiles/default");
    let application_id = request["approval"]["metadata"]["application_id"]
        .as_str()
        .context("application ID")?;
    let receipt_path = profile
        .join("artifact_retention_applications")
        .join(format!("{application_id}.json"));
    let mut metadata_value = request["approval"]["metadata"].clone();
    metadata_value
        .as_object_mut()
        .context("retention metadata object")?
        .remove("kind");
    let retention_metadata: RetentionApprovalMetadataV2 = serde_json::from_value(metadata_value)?;
    let approval_metadata_sha256 = retention_approval_metadata_sha256_v2(&retention_metadata)?;
    let approval_preview_sha256 = format!(
        "{:x}",
        Sha256::digest(
            request["approval"]["preview"]
                .as_str()
                .context("approval preview")?
                .as_bytes()
        )
    );
    fs::create_dir_all(receipt_path.parent().context("receipt parent")?)?;
    #[cfg(unix)]
    fs::set_permissions(
        receipt_path.parent().context("receipt parent")?,
        fs::Permissions::from_mode(0o700),
    )?;
    fs::write(
        &receipt_path,
        serde_json::to_vec_pretty(&serde_json::json!({
            "schema": "artifact_retention_application.v2",
            "application_id": application_id,
            "approval_id": approval_id,
            "project_key": "phase2f-cli",
            "proposal_sha256": request["approval"]["metadata"]["proposal_sha256"],
            "approval_metadata_sha256": approval_metadata_sha256,
            "approval_preview_sha256": approval_preview_sha256,
            "retention_target_id": document_id,
            "requested_action": "keep",
            "lifecycle_event": {
                "event_id": "document_event_00000000-0000-4000-8000-000000000000",
                "event_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
            },
            "plan_only": true,
            "file_mutation_performed": false
        }))?,
    )?;
    #[cfg(unix)]
    fs::set_permissions(&receipt_path, fs::Permissions::from_mode(0o600))?;
    let orphan_receipt = forager(&home)
        .args([
            "project",
            "retention-apply",
            project_arg,
            "--project-key",
            "phase2f-cli",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(!orphan_receipt.status.success());
    assert!(String::from_utf8_lossy(&orphan_receipt.stderr)
        .contains("receipt exists without its exact durable lifecycle event"));
    fs::remove_file(&receipt_path)?;

    let applied = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-apply",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--approval-id",
                &approval_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(applied["schema"], "artifact_retention_application.v2");
    assert_eq!(applied["operation"]["mutation_performed"], false);
    assert!(applied["lifecycle_event"]["event_id"].is_string());
    assert_eq!(project_manifest(&project)?, project_before);
    let receipt: Value = serde_json::from_str(&fs::read_to_string(
        applied["receipt_path"].as_str().context("receipt path")?,
    )?)?;
    assert_eq!(receipt["lifecycle_event"], applied["lifecycle_event"]);

    let approvals_path = profile.join("pending_action_approvals.json");
    let exact_approvals = fs::read(&approvals_path)?;
    let mut approvals_without_source: Value = serde_json::from_slice(&exact_approvals)?;
    approvals_without_source
        .as_array_mut()
        .context("approval rows")?
        .retain(|approval| approval["approval_id"] != approval_id);
    fs::write(
        &approvals_path,
        serde_json::to_vec_pretty(&approvals_without_source)?,
    )?;
    let missing_approval_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(missing_approval_catalog["lifecycle"]["status"], "corrupt");
    assert_eq!(
        missing_approval_catalog["lifecycle"]["failure_code"],
        "retention_application_integrity_error"
    );
    fs::write(&approvals_path, exact_approvals)?;

    let set_approval_status = |status: &str| -> Result<()> {
        let mut approvals: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
        let approval = approvals
            .as_array_mut()
            .context("approval rows")?
            .iter_mut()
            .find(|approval| approval["approval_id"] == approval_id)
            .context("retention approval row")?;
        approval["status"] = Value::String(status.to_string());
        fs::write(&approvals_path, serde_json::to_vec_pretty(&approvals)?)?;
        Ok(())
    };

    set_approval_status("denied")?;
    let non_applicable_receipt_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        non_applicable_receipt_catalog["lifecycle"]["status"],
        "corrupt"
    );
    assert_eq!(
        non_applicable_receipt_catalog["lifecycle"]["failure_code"],
        "retention_application_integrity_error"
    );
    fs::remove_file(&receipt_path)?;
    let non_applicable_event_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        non_applicable_event_catalog["lifecycle"]["status"],
        "corrupt"
    );
    assert_eq!(
        non_applicable_event_catalog["lifecycle"]["failure_code"],
        "retention_application_integrity_error"
    );

    set_approval_status("approved")?;
    let event_only_retry = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-apply",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--approval-id",
                &approval_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(event_only_retry["status"], "reconciled_existing");
    assert_eq!(event_only_retry["receipt_path"], applied["receipt_path"]);
    assert_eq!(
        event_only_retry["lifecycle_event"],
        applied["lifecycle_event"]
    );

    set_approval_status("approved")?;
    let event_and_receipt_retry = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-apply",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--approval-id",
                &approval_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(event_and_receipt_retry["status"], "reconciled_existing");
    assert_eq!(
        event_and_receipt_retry["receipt_path"],
        applied["receipt_path"]
    );

    fs::write(project.join("docs/report.md"), "# Changed after apply\n")?;
    let completed_after_drift = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-apply",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--approval-id",
                &approval_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(completed_after_drift["status"], "reconciled_existing");
    assert_eq!(
        completed_after_drift["receipt_path"],
        applied["receipt_path"]
    );
    fs::write(project.join("docs/report.md"), "# Retention target\n")?;

    let events_dir = profile.join("document-lifecycle/v1/events");
    let retention_event_id = applied["lifecycle_event"]["event_id"]
        .as_str()
        .context("retention event ID")?;
    let retention_event_path = fs::read_dir(&events_dir)?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .find(|path| {
            fs::read_to_string(path)
                .ok()
                .and_then(|bytes| serde_json::from_str::<Value>(&bytes).ok())
                .is_some_and(|event| event["payload"]["event_id"] == retention_event_id)
        })
        .context("retention event path")?;
    fs::remove_file(retention_event_path)?;
    fs::remove_file(&receipt_path)?;
    let consumed_without_records = forager(&home)
        .args([
            "project",
            "retention-apply",
            project_arg,
            "--project-key",
            "phase2f-cli",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(!consumed_without_records.status.success());
    assert!(String::from_utf8_lossy(&consumed_without_records.stderr)
        .contains("consumed retention approval has no matching durable lifecycle event"));
    let fail_open_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(fail_open_catalog["lifecycle"]["status"], "corrupt");
    assert_eq!(
        fail_open_catalog["lifecycle"]["failure_code"],
        "retention_application_integrity_error"
    );
    assert_eq!(
        fail_open_catalog["lifecycle"]["filtering_authorized"],
        false
    );
    #[cfg(unix)]
    {
        fs::set_permissions(&approvals_path, fs::Permissions::from_mode(0o644))?;
        let unsafe_approval_catalog = successful_json(
            forager(&home)
                .args([
                    "project",
                    "document-catalog",
                    project_arg,
                    "--project-key",
                    "phase2f-cli",
                    "--lifecycle",
                    "--json",
                ])
                .output()?,
        )?;
        assert_eq!(unsafe_approval_catalog["lifecycle"]["status"], "corrupt");
        assert_eq!(
            unsafe_approval_catalog["lifecycle"]["failure_code"],
            "retention_application_integrity_error"
        );
        fs::set_permissions(&approvals_path, fs::Permissions::from_mode(0o600))?;
    }
    fs::write(&approvals_path, b"{not-json")?;
    let corrupt_approval_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2f-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(corrupt_approval_catalog["lifecycle"]["status"], "corrupt");
    assert_eq!(
        corrupt_approval_catalog["lifecycle"]["failure_code"],
        "retention_application_integrity_error"
    );
    assert_eq!(project_manifest(&project)?, project_before);
    Ok(())
}

#[test]
fn retention_v2_unifies_exact_artifact_document_and_path_selectors() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("home");
    let project = temp.path().join("project");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(project.join("outputs"))?;
    fs::write(project.join("outputs/report.html"), "<h1>Report</h1>\n")?;
    fs::write(project.join("outputs/other.html"), "<h1>Other</h1>\n")?;
    let catalog = scan_project(
        &project,
        &CatalogOptions {
            project_key: Some("phase2f-unified".to_string()),
            ..CatalogOptions::default()
        },
    )?;
    let observation = catalog
        .entries
        .iter()
        .find(|entry| entry.relative_path == "outputs/report.html")
        .context("HTML observation")?;
    let minted = mint_document(
        &home,
        &project,
        "phase2f-unified",
        &observation.observation_id,
        "Establish the artifact-backed document.",
    )?;
    let document_id = minted["event"]["payload"]["document_id"]
        .as_str()
        .context("document ID")?;
    let project_arg = project.to_str().context("UTF-8 project path")?;
    let review = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-review",
                project_arg,
                "--project-key",
                "phase2f-unified",
                "--document-id",
                document_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(review["target"]["source_kind"], "artifact");
    let artifact_id = review["target"]["artifact_id"]
        .as_str()
        .context("artifact ID")?;
    assert_eq!(review["target"]["document_id"], document_id);
    assert_eq!(review["target"]["retention_target_id"], artifact_id);

    let request = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-request",
                project_arg,
                "--project-key",
                "phase2f-unified",
                "--artifact-id",
                artifact_id,
                "--path",
                "outputs/report.html",
                "--document-id",
                document_id,
                "--action",
                "preserve",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        request["approval"]["metadata"]["proposal"]["target"]["retention_target_id"],
        artifact_id
    );

    let independent = successful_json(
        forager(&home)
            .args([
                "project",
                "retention-request",
                project_arg,
                "--project-key",
                "phase2f-unified",
                "--path",
                "outputs/other.html",
                "--action",
                "promote",
                "--json",
            ])
            .output()?,
    )?;
    assert_ne!(
        independent["approval"]["approval_id"],
        request["approval"]["approval_id"]
    );

    let mismatch = forager(&home)
        .args([
            "project",
            "retention-request",
            project_arg,
            "--project-key",
            "phase2f-unified",
            "--artifact-id",
            artifact_id,
            "--path",
            "outputs/different.html",
            "--document-id",
            document_id,
            "--action",
            "preserve",
            "--json",
        ])
        .output()?;
    assert!(!mismatch.status.success());
    assert!(
        String::from_utf8_lossy(&mismatch.stderr).contains("do not identify the same current file")
    );

    fs::write(
        project.join("outputs/report.html"),
        "<h1>Changed report</h1>\n",
    )?;
    for selector in [
        vec!["--path", "outputs/report.html"],
        vec!["--artifact-id", artifact_id],
    ] {
        let mut args = vec![
            "project",
            "retention-request",
            project_arg,
            "--project-key",
            "phase2f-unified",
        ];
        args.extend(selector);
        args.extend(["--action", "promote", "--json"]);
        let stale_bypass = forager(&home).args(args).output()?;
        assert!(!stale_bypass.status.success());
        assert!(String::from_utf8_lossy(&stale_bypass.stderr)
            .contains("stale lifecycle identity at the selected path"));
    }
    Ok(())
}

#[test]
fn cli_lifecycle_apply_and_catalog_v2_projection_preserve_files() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("home");
    let project = temp.path().join("project");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(project.join("docs"))?;
    fs::write(project.join("docs/report.md"), "# Current report\n")?;
    let catalog = scan_project(
        &project,
        &CatalogOptions {
            project_key: Some("phase2c-cli".to_string()),
            ..CatalogOptions::default()
        },
    )?;
    let observation_id = &catalog.entries[0].observation_id;
    let before = project_manifest(&project)?;
    let project_arg = project.to_str().context("UTF-8 project path")?;

    let review = successful_json(
        forager(&home)
            .args([
                "project",
                "document-lifecycle-review",
                project_arg,
                "mint-document",
                "--project-key",
                "phase2c-cli",
                "--observation-id",
                observation_id,
                "--activity-state",
                "active",
                "--reason",
                "Establish the reviewed current report.",
                "--json",
            ])
            .output()?,
    )?;
    let proposal_sha256 = review["proposal_sha256"]
        .as_str()
        .context("proposal hash")?;
    assert!(!app_dir(&home).exists());

    let request = successful_json(
        forager(&home)
            .args([
                "project",
                "document-lifecycle-request",
                project_arg,
                "mint-document",
                "--project-key",
                "phase2c-cli",
                "--observation-id",
                observation_id,
                "--activity-state",
                "active",
                "--reason",
                "Establish the reviewed current report.",
                "--expected-proposal-sha256",
                proposal_sha256,
                "--json",
            ])
            .output()?,
    )?;
    let approval_id = request["approval_id"].as_str().context("approval ID")?;
    assert_eq!(request["status"], "pending");

    let approved = successful_json(
        forager(&home)
            .args([
                "offdesk",
                "ok",
                approval_id,
                "--by",
                "test-operator",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(approved["status"], "approved");

    let applied = successful_json(
        forager(&home)
            .args([
                "project",
                "document-lifecycle-apply",
                project_arg,
                "--approval-id",
                approval_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(applied["event"]["payload"]["event_kind"], "mint_document");
    assert_eq!(applied["reused_existing"], false);
    assert_eq!(project_manifest(&project)?, before);

    let retried = successful_json(
        forager(&home)
            .args([
                "project",
                "document-lifecycle-apply",
                project_arg,
                "--approval-id",
                approval_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(retried["reused_existing"], true);
    assert_eq!(
        retried["event"]["event_sha256"],
        applied["event"]["event_sha256"]
    );

    let document_id = applied["event"]["payload"]["document_id"]
        .as_str()
        .context("document ID")?;
    let history = successful_json(
        forager(&home)
            .args([
                "project",
                "document-lifecycle-history",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--document-id",
                document_id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(history["ledger_status"], "valid");
    assert_eq!(
        history["events"]
            .as_array()
            .context("history events")?
            .len(),
        1
    );
    assert_eq!(history["document_body_included"], false);
    assert_eq!(history["file_mutation_authorized"], false);
    assert_eq!(project_manifest(&project)?, before);

    let profile = app_dir(&home).join("profiles/default");
    let profile_before_catalog = project_manifest(&profile)?;
    let exact_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(exact_catalog["schema"], "document_catalog.v2");
    assert_eq!(exact_catalog["lifecycle"]["status"], "valid");
    assert_eq!(exact_catalog["lifecycle"]["filtering_authorized"], true);
    assert_eq!(exact_catalog["entries"][0]["document_id"], document_id);
    assert_eq!(
        exact_catalog["entries"][0]["reviewed_state"]["activity_state"],
        "active"
    );
    assert_eq!(exact_catalog["entries"][0]["lifecycle_stale"], false);
    assert_eq!(exact_catalog["entries"][0]["visibility"], "visible");
    assert_eq!(
        exact_catalog["lifecycle_findings"]
            .as_array()
            .context("exact lifecycle findings")?
            .len(),
        0
    );
    let exact_contract: DocumentCatalogV2 = serde_json::from_value(exact_catalog.clone())?;
    let expectation = exact_contract
        .lifecycle
        .expectation
        .clone()
        .context("lifecycle expectation")?;
    let root_instance = expectation.root_instance_identity_sha256.clone();
    let observed_at = exact_contract
        .lifecycle
        .source_identity
        .as_ref()
        .context("lifecycle source identity")?
        .observed_at
        .clone();
    let lifecycle_load = load_document_lifecycle_ledger_from_root(
        &profile,
        &expectation,
        &LifecycleLedgerLoadOptions {
            observed_at,
            ..LifecycleLedgerLoadOptions::default()
        },
    )?;
    exact_contract.validate_against_lifecycle_load(&lifecycle_load, &root_instance)?;
    assert_eq!(project_manifest(&project)?, before);
    assert_eq!(project_manifest(&profile)?, profile_before_catalog);

    fs::rename(
        project.join("docs/report.md"),
        project.join("docs/moved.md"),
    )?;
    let rebind_candidate = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        rebind_candidate["lifecycle_findings"][0]["code"],
        "lifecycle_rebind_candidate"
    );
    assert_eq!(
        rebind_candidate["lifecycle_findings"][0]["current_observation_ids"]
            .as_array()
            .context("rebind candidates")?
            .len(),
        1
    );
    assert!(rebind_candidate["entries"][0]["document_id"].is_null());
    assert_eq!(rebind_candidate["entries"][0]["lifecycle_stale"], true);
    let rebind_contract: DocumentCatalogV2 = serde_json::from_value(rebind_candidate.clone())?;
    let rebind_expectation = rebind_contract
        .lifecycle
        .expectation
        .as_ref()
        .context("rebind lifecycle expectation")?;
    let rebind_observed_at = rebind_contract
        .lifecycle
        .source_identity
        .as_ref()
        .context("rebind lifecycle source")?
        .observed_at
        .clone();
    let rebind_load = load_document_lifecycle_ledger_from_root(
        &profile,
        rebind_expectation,
        &LifecycleLedgerLoadOptions {
            observed_at: rebind_observed_at,
            ..LifecycleLedgerLoadOptions::default()
        },
    )?;
    rebind_contract.validate_against_lifecycle_load(&rebind_load, &root_instance)?;
    let mut wrong_finding = rebind_contract.clone();
    wrong_finding.lifecycle_findings[0].code = "lifecycle_binding_missing".to_string();
    assert!(wrong_finding
        .validate_against_lifecycle_load(&rebind_load, &root_instance)
        .is_err());
    let mut wrong_message = rebind_contract.clone();
    wrong_message.lifecycle_findings[0].message = "Trust this different claim.".to_string();
    assert!(wrong_message
        .validate_against_lifecycle_load(&rebind_load, &root_instance)
        .is_err());
    let mut wrong_visibility_evidence = rebind_contract.clone();
    wrong_visibility_evidence.entries[0].visibility_evidence =
        "Lifecycle filtering was authorized for this document.".to_string();
    assert!(wrong_visibility_evidence
        .validate_against_lifecycle_load(&rebind_load, &root_instance)
        .is_err());
    let mut wrong_severity = rebind_contract.clone();
    wrong_severity.lifecycle_findings[0].severity = HygieneSeverity::Error;
    assert!(wrong_severity
        .validate_against_lifecycle_load(&rebind_load, &root_instance)
        .is_err());
    let lifecycle_head = rebind_load.events.last().context("rebind lifecycle head")?;
    let mut fabricated_projection = rebind_contract.clone();
    fabricated_projection.entries[0].document_id = Some(lifecycle_head.payload.document_id.clone());
    fabricated_projection.entries[0].lifecycle_head_event_id =
        Some(lifecycle_head.payload.event_id.clone());
    fabricated_projection.entries[0].reviewed_state = Some(lifecycle_head.payload.after.clone());
    fabricated_projection.lifecycle_findings.clear();
    assert!(fabricated_projection
        .validate_against_lifecycle_load(&rebind_load, &root_instance)
        .is_err());
    let mut hidden_staleness = rebind_contract;
    hidden_staleness.entries[0].lifecycle_stale = false;
    assert!(hidden_staleness
        .validate_against_lifecycle_load(&rebind_load, &root_instance)
        .is_err());

    fs::copy(
        project.join("docs/moved.md"),
        project.join("docs/second.md"),
    )?;
    let ambiguous = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        ambiguous["lifecycle_findings"][0]["code"],
        "lifecycle_rebind_ambiguous"
    );
    assert_eq!(
        ambiguous["lifecycle_findings"][0]["current_observation_ids"]
            .as_array()
            .context("ambiguous candidates")?
            .len(),
        2
    );
    assert!(ambiguous["entries"]
        .as_array()
        .context("ambiguous entries")?
        .iter()
        .all(|entry| entry["document_id"].is_null()
            && entry["lifecycle_stale"] == true
            && entry["visibility"] == "visible_fail_open"));
    fs::remove_file(project.join("docs/second.md"))?;
    fs::rename(
        project.join("docs/moved.md"),
        project.join("docs/report.md"),
    )?;
    assert_eq!(project_manifest(&project)?, before);
    assert_eq!(project_manifest(&profile)?, profile_before_catalog);

    let incomplete_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--max-files",
                "0",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(incomplete_catalog["lifecycle"]["status"], "valid");
    assert_eq!(
        incomplete_catalog["base_catalog"]["scan"]["complete"],
        false
    );
    assert_eq!(
        incomplete_catalog["lifecycle"]["filtering_authorized"],
        false
    );
    assert_eq!(
        incomplete_catalog["lifecycle_findings"][0]["code"],
        "lifecycle_binding_unresolved_incomplete_scan"
    );

    fs::write(project.join("docs/report.md"), "# Changed report\n")?;
    let changed_project = project_manifest(&project)?;
    let stale_catalog = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert!(stale_catalog["entries"][0]["document_id"].is_null());
    assert_eq!(stale_catalog["entries"][0]["lifecycle_stale"], true);
    assert_eq!(
        stale_catalog["entries"][0]["visibility"],
        "visible_fail_open"
    );
    assert_eq!(
        stale_catalog["lifecycle_findings"][0]["code"],
        "lifecycle_binding_stale_same_path"
    );
    assert_eq!(project_manifest(&project)?, changed_project);
    assert_eq!(project_manifest(&profile)?, profile_before_catalog);

    let partial = profile.join("document-lifecycle/v1/events/.interrupted.partial");
    fs::write(&partial, b"incomplete")?;
    let profile_with_partial = project_manifest(&profile)?;
    let fail_open = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project_arg,
                "--project-key",
                "phase2c-cli",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(fail_open["lifecycle"]["status"], "partial_write_present");
    assert!(fail_open["entries"][0]["document_id"].is_null());
    assert_eq!(fail_open["entries"][0]["lifecycle_stale"], true);
    assert_eq!(fail_open["entries"][0]["visibility"], "visible_fail_open");
    assert_eq!(
        fail_open["lifecycle_findings"]
            .as_array()
            .context("fail-open lifecycle findings")?
            .len(),
        0
    );
    assert_eq!(project_manifest(&project)?, changed_project);
    assert_eq!(project_manifest(&profile)?, profile_with_partial);
    Ok(())
}

#[test]
fn catalog_v2_reports_rebind_collision_without_staling_exact_owner() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("home");
    let project = temp.path().join("project");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(&project)?;
    fs::write(project.join("old-a.md"), "shared reviewed bytes\n")?;
    let options = CatalogOptions {
        project_key: Some("phase2d-cross-head".to_string()),
        ..CatalogOptions::default()
    };
    let first_observation = scan_project(&project, &options)?
        .entries
        .into_iter()
        .next()
        .context("first observation")?
        .observation_id;
    let first = mint_document(
        &home,
        &project,
        "phase2d-cross-head",
        &first_observation,
        "Mint the first reviewed identity.",
    )?;
    let first_document_id = first["event"]["payload"]["document_id"]
        .as_str()
        .context("first document ID")?
        .to_string();

    fs::rename(project.join("old-a.md"), project.join("current-b.md"))?;
    let current_observation = scan_project(&project, &options)?
        .entries
        .into_iter()
        .next()
        .context("current observation")?;
    let current_observation_id = current_observation.observation_id.clone();
    let second = mint_document(
        &home,
        &project,
        "phase2d-cross-head",
        &current_observation_id,
        "Mint the distinct current identity.",
    )?;
    let second_document_id = second["event"]["payload"]["document_id"]
        .as_str()
        .context("second document ID")?
        .to_string();

    let project_before_catalog = project_manifest(&project)?;
    let profile = app_dir(&home).join("profiles/default");
    let profile_before_catalog = project_manifest(&profile)?;
    let value = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project.to_str().context("UTF-8 project path")?,
                "--project-key",
                "phase2d-cross-head",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(value["lifecycle"]["status"], "valid");
    assert_eq!(value["entries"][0]["document_id"], second_document_id);
    assert_eq!(value["entries"][0]["lifecycle_stale"], false);
    assert_eq!(
        value["lifecycle_findings"][0]["document_id"],
        first_document_id
    );
    assert_eq!(
        value["lifecycle_findings"][0]["code"],
        "lifecycle_rebind_identity_conflict"
    );
    assert_eq!(
        value["lifecycle_findings"][0]["current_observation_ids"][0],
        current_observation_id
    );

    let contract: DocumentCatalogV2 = serde_json::from_value(value)?;
    let expectation = contract
        .lifecycle
        .expectation
        .clone()
        .context("collision lifecycle expectation")?;
    let root_instance = expectation.root_instance_identity_sha256.clone();
    let observed_at = contract
        .lifecycle
        .source_identity
        .as_ref()
        .context("collision lifecycle source")?
        .observed_at
        .clone();
    let load = load_document_lifecycle_ledger_from_root(
        &profile,
        &expectation,
        &LifecycleLedgerLoadOptions {
            observed_at,
            ..LifecycleLedgerLoadOptions::default()
        },
    )?;
    contract.validate_against_lifecycle_load(&load, &root_instance)?;
    let mut false_candidate = contract;
    false_candidate.lifecycle_findings[0].code = "lifecycle_rebind_candidate".to_string();
    assert!(false_candidate
        .validate_against_lifecycle_load(&load, &root_instance)
        .is_err());
    assert_eq!(project_manifest(&project)?, project_before_catalog);
    assert_eq!(project_manifest(&profile)?, profile_before_catalog);

    fs::write(
        project.join("old-a.md"),
        "changed bytes at the prior path\n",
    )?;
    fs::rename(project.join("current-b.md"), project.join("moved-b.md"))?;
    let precedence_project_before_catalog = project_manifest(&project)?;
    let precedence_profile_before_catalog = project_manifest(&profile)?;
    let precedence_value = successful_json(
        forager(&home)
            .args([
                "project",
                "document-catalog",
                project.to_str().context("UTF-8 project path")?,
                "--project-key",
                "phase2d-cross-head",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    let precedence_findings = precedence_value["lifecycle_findings"]
        .as_array()
        .context("precedence lifecycle findings")?;
    let first_finding = precedence_findings
        .iter()
        .find(|finding| finding["document_id"] == first_document_id)
        .context("first stale same-path finding")?;
    assert_eq!(first_finding["code"], "lifecycle_binding_stale_same_path");
    let second_finding = precedence_findings
        .iter()
        .find(|finding| finding["document_id"] == second_document_id)
        .context("second unique rebind finding")?;
    assert_eq!(second_finding["code"], "lifecycle_rebind_candidate");

    let precedence_contract: DocumentCatalogV2 = serde_json::from_value(precedence_value)?;
    let precedence_expectation = precedence_contract
        .lifecycle
        .expectation
        .clone()
        .context("precedence lifecycle expectation")?;
    let precedence_root_instance = precedence_expectation.root_instance_identity_sha256.clone();
    let precedence_observed_at = precedence_contract
        .lifecycle
        .source_identity
        .as_ref()
        .context("precedence lifecycle source")?
        .observed_at
        .clone();
    let precedence_load = load_document_lifecycle_ledger_from_root(
        &profile,
        &precedence_expectation,
        &LifecycleLedgerLoadOptions {
            observed_at: precedence_observed_at,
            ..LifecycleLedgerLoadOptions::default()
        },
    )?;
    precedence_contract
        .validate_against_lifecycle_load(&precedence_load, &precedence_root_instance)?;
    assert_eq!(
        project_manifest(&project)?,
        precedence_project_before_catalog
    );
    assert_eq!(
        project_manifest(&profile)?,
        precedence_profile_before_catalog
    );
    Ok(())
}

#[test]
fn search_v2_filters_only_exact_reviewed_cold_documents_and_stays_read_only() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("home");
    let project = temp.path().join("project");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(&project)?;
    fs::write(
        project.join("active.md"),
        "shared-search-needle active-only-needle\n",
    )?;
    fs::write(
        project.join("old.md"),
        "shared-search-needle old-only-needle\n",
    )?;
    let options = CatalogOptions {
        project_key: Some("phase2e-search".to_string()),
        ..CatalogOptions::default()
    };
    let initial = scan_project(&project, &options)?;
    let active_observation = initial
        .entries
        .iter()
        .find(|entry| entry.relative_path == "active.md")
        .context("active observation")?
        .observation_id
        .clone();
    let old_observation = initial
        .entries
        .iter()
        .find(|entry| entry.relative_path == "old.md")
        .context("old observation")?
        .observation_id
        .clone();
    let active = mint_document(
        &home,
        &project,
        "phase2e-search",
        &active_observation,
        "Mint the active search document.",
    )?;
    let active_document_id = active["event"]["payload"]["document_id"]
        .as_str()
        .context("active document ID")?
        .to_string();
    let old = mint_document(
        &home,
        &project,
        "phase2e-search",
        &old_observation,
        "Mint the old search document.",
    )?;
    let old_document_id = old["event"]["payload"]["document_id"]
        .as_str()
        .context("old document ID")?
        .to_string();
    supersede_document(
        &home,
        &project,
        "phase2e-search",
        &old_document_id,
        &active_document_id,
    )?;

    let project_arg = project.to_str().context("UTF-8 project path")?;
    let profile = app_dir(&home).join("profiles/default");
    let project_before_search = project_manifest(&project)?;
    let profile_before_search = project_manifest(&profile)?;
    let default_value = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "shared-search-needle",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(default_value["schema"], "document_search.v2");
    assert_eq!(default_value["catalog"]["lifecycle"]["status"], "valid");
    assert_eq!(
        default_value["catalog"]["lifecycle"]["filtering_authorized"],
        true
    );
    assert_eq!(default_value["prefilter_match_count"], 2);
    assert_eq!(default_value["visible_match_count"], 1);
    assert_eq!(default_value["cold_matches_omitted"], 1);
    assert_eq!(
        default_value["matches"][0]["entry"]["document_id"],
        active_document_id
    );
    assert_eq!(
        default_value["omitted_cold_matches"][0]["entry"]["document_id"],
        old_document_id
    );
    assert!(default_value["negative_evidence"].is_null());

    let default_contract: DocumentSearchV2 = serde_json::from_value(default_value)?;
    let (load, root_instance) = lifecycle_load_for_search(&profile, &default_contract)?;
    let live_bodies = BTreeMap::from([
        (
            active_observation.clone(),
            "shared-search-needle active-only-needle\n".to_string(),
        ),
        (
            old_observation.clone(),
            "shared-search-needle old-only-needle\n".to_string(),
        ),
    ]);
    default_contract.validate_against_lifecycle_and_live_bodies(
        &load,
        &root_instance,
        &live_bodies,
    )?;

    let include_cold = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "shared-search-needle",
                "--lifecycle",
                "--include-cold",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        include_cold["matches"].as_array().context("matches")?.len(),
        2
    );
    assert_eq!(
        include_cold["matches"][0]["entry"]["document_id"],
        active_document_id
    );
    assert_eq!(
        include_cold["matches"][1]["entry"]["document_id"],
        old_document_id
    );
    assert_eq!(include_cold["cold_matches_omitted"], 0);

    let exact_cold = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--document-id",
                &old_document_id,
                "--relative-path",
                "old.md",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(exact_cold["matches"][0]["entry"]["visibility"], "cold");
    assert!(exact_cold["matches"][0]["cold_warning"]
        .as_str()
        .is_some_and(|warning| warning.contains("remains retrievable")));

    let mismatch = forager(&home)
        .args([
            "project",
            "document-search",
            project_arg,
            "--project-key",
            "phase2e-search",
            "--document-id",
            &active_document_id,
            "--relative-path",
            "old.md",
            "--lifecycle",
            "--json",
        ])
        .output()?;
    assert!(!mismatch.status.success());

    let cold_only = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "old-only-needle",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert!(cold_only["matches"]
        .as_array()
        .context("cold matches")?
        .is_empty());
    assert_eq!(cold_only["cold_matches_omitted"], 1);
    assert_eq!(
        cold_only["negative_evidence"]["content_absence_established"],
        false
    );
    assert_eq!(
        cold_only["negative_evidence"]["owner_absence_authorized"],
        false
    );
    let cold_only_contract: DocumentSearchV2 = serde_json::from_value(cold_only)?;
    let (cold_only_load, cold_only_root_instance) =
        lifecycle_load_for_search(&profile, &cold_only_contract)?;
    cold_only_contract.validate_against_lifecycle_and_live_bodies(
        &cold_only_load,
        &cold_only_root_instance,
        &live_bodies,
    )?;
    let mut hidden_body_match = cold_only_contract;
    hidden_body_match.prefilter_match_count = 0;
    hidden_body_match.cold_matches_omitted = 0;
    hidden_body_match.omitted_cold_matches.clear();
    hidden_body_match
        .authority_boundary
        .lifecycle_filtering_applied = false;
    let negative = hidden_body_match
        .negative_evidence
        .as_mut()
        .context("negative search evidence")?;
    negative.content_absence_established = true;
    negative.reason =
        "The live bounded scan completed with full body coverage and found no matching content."
            .to_string();
    assert!(hidden_body_match
        .validate_against_lifecycle_and_live_bodies(
            &cold_only_load,
            &cold_only_root_instance,
            &live_bodies,
        )
        .is_err());

    let no_match_value = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "not-present-anywhere",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    let no_match: DocumentSearchV2 = serde_json::from_value(no_match_value)?;
    let (no_match_load, no_match_root_instance) = lifecycle_load_for_search(&profile, &no_match)?;
    no_match.validate_against_lifecycle_and_live_bodies(
        &no_match_load,
        &no_match_root_instance,
        &live_bodies,
    )?;
    assert!(
        no_match
            .negative_evidence
            .as_ref()
            .context("no-match evidence")?
            .content_absence_established
    );
    assert!(
        !no_match
            .negative_evidence
            .as_ref()
            .context("no-match evidence")?
            .owner_absence_authorized
    );

    let incomplete = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "shared-search-needle",
                "--max-files",
                "1",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        incomplete["catalog"]["lifecycle"]["filtering_authorized"],
        false
    );
    assert_eq!(incomplete["cold_matches_omitted"], 0);
    assert!(incomplete["matches"]
        .as_array()
        .context("incomplete matches")?
        .iter()
        .all(|result_match| result_match["entry"]["visibility"] == "visible_fail_open"));
    assert_eq!(project_manifest(&project)?, project_before_search);
    assert_eq!(project_manifest(&profile)?, profile_before_search);

    fs::write(
        project.join("active.md"),
        "shared-search-needle active bytes changed after review\n",
    )?;
    let stale_project = project_manifest(&project)?;
    let stale = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "shared-search-needle",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(stale["catalog"]["lifecycle"]["status"], "valid");
    assert_eq!(stale["catalog"]["lifecycle"]["filtering_authorized"], false);
    assert_eq!(stale["cold_matches_omitted"], 0);
    assert_eq!(
        stale["matches"].as_array().context("stale matches")?.len(),
        2
    );
    assert!(stale["matches"]
        .as_array()
        .context("stale matches")?
        .iter()
        .all(|result_match| result_match["entry"]["visibility"] == "visible_fail_open"));
    assert_eq!(project_manifest(&project)?, stale_project);
    assert_eq!(project_manifest(&profile)?, profile_before_search);

    fs::write(
        profile.join("document-lifecycle/v1/events/.phase2e.partial"),
        "interrupted",
    )?;
    let fail_open = successful_json(
        forager(&home)
            .args([
                "project",
                "document-search",
                project_arg,
                "--project-key",
                "phase2e-search",
                "--query",
                "shared-search-needle",
                "--lifecycle",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        fail_open["catalog"]["lifecycle"]["status"],
        "partial_write_present"
    );
    assert_eq!(fail_open["cold_matches_omitted"], 0);
    assert_eq!(
        fail_open["matches"]
            .as_array()
            .context("fail-open matches")?
            .len(),
        2
    );
    assert!(fail_open["matches"]
        .as_array()
        .context("fail-open matches")?
        .iter()
        .all(|result_match| result_match["entry"]["visibility"] == "visible_fail_open"));
    assert_eq!(project_manifest(&project)?, stale_project);
    Ok(())
}

#[cfg(unix)]
#[test]
fn retention_request_rejects_symlinked_artifact_discovery() -> Result<()> {
    use std::os::unix::fs::symlink;

    let temp = tempdir()?;
    let home = temp.path().join("home");
    let project = temp.path().join("project");
    fs::create_dir_all(&home)?;
    fs::create_dir_all(project.join("outputs"))?;
    fs::write(project.join("outputs/report.html"), "<h1>Report</h1>\n")?;
    symlink(".", project.join("outputs/loop"))?;
    let output = forager(&home)
        .args([
            "project",
            "retention-request",
            project.to_str().context("UTF-8 project path")?,
            "--project-key",
            "phase2f-bounded",
            "--path",
            "outputs/report.html",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("classify bounded artifact path"));
    Ok(())
}
