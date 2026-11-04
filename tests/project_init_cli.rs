use anyhow::{Context, Result};
use serde_json::{json, Value};
use serial_test::serial;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::tempdir;

fn inventory(home: &Path, roots: &[&Path], registry: &Path, extra: &[&str]) -> Result<Value> {
    let mut command = forager_command(home);
    command
        .env("FORAGER_PROFILE", "../invalid-profile")
        .args(["project", "inventory"]);
    for root in roots {
        command.arg(root);
    }
    let output = command
        .arg("--registry")
        .arg(registry)
        .args(extra)
        .arg("--json")
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

fn inventory_snapshot(root: &Path) -> Result<std::collections::BTreeMap<PathBuf, Vec<u8>>> {
    fn walk(root: &Path, map: &mut std::collections::BTreeMap<PathBuf, Vec<u8>>) -> Result<()> {
        for entry in fs::read_dir(root)? {
            let path = entry?.path();
            let metadata = fs::symlink_metadata(&path)?;
            if metadata.is_symlink() {
                map.insert(
                    path.clone(),
                    fs::read_link(&path)?
                        .as_os_str()
                        .as_encoded_bytes()
                        .to_vec(),
                );
            } else if metadata.is_dir() {
                map.insert(path.clone(), Vec::new());
                walk(&path, map)?;
            } else {
                map.insert(path.clone(), fs::read(&path)?);
            }
        }
        Ok(())
    }
    let mut result = std::collections::BTreeMap::new();
    walk(root, &mut result)?;
    Ok(result)
}

#[test]
fn project_inventory_accounts_for_two_roots_and_unmarked_nested_paths_without_writes() -> Result<()>
{
    let temp = tempdir()?;
    let first = temp.path().join("first");
    let second = temp.path().join("second");
    let home = temp.path().join("never-created-home");
    write_file(&first.join("alpha/AGENTS.md"), "alpha\n")?;
    write_file(&first.join("loose/README.md"), "no project marker\n")?;
    write_file(&first.join("group/nested/Cargo.toml"), "nested\n")?;
    write_file(&first.join(".private/Cargo.toml"), "hidden\n")?;
    write_file(&second.join("beta/README.md"), "declared without marker\n")?;
    let registry = temp.path().join("projects.toml");
    write_file(&registry, "schema = 'forager_project_registry.v1'\n[projects.alpha]\nworkspace_patterns = ['alpha']\n[projects.beta]\nworkspace_patterns = ['beta']\n[projects.missing]\nworkspace_patterns = ['absent-project']\n")?;
    let before = inventory_snapshot(temp.path())?;
    let args = ["--project-key", "alpha", "--max-depth", "2"];
    let report = inventory(&home, &[&first, &second], &registry, &args)?;
    assert_eq!(
        report,
        inventory(&home, &[&first, &second], &registry, &args)?
    );
    assert_eq!(inventory_snapshot(temp.path())?, before);
    assert!(!home.exists());
    let entries = report["entries"].as_array().unwrap();
    let status = |suffix: &str| {
        entries
            .iter()
            .find(|entry| entry["path"].as_str().unwrap().ends_with(suffix))
            .unwrap()["status"]
            .clone()
    };
    assert_eq!(status("/alpha"), "registered_project");
    assert_eq!(status("/beta"), "registered_project");
    assert_eq!(status("/group/nested"), "unregistered_project_candidate");
    assert_eq!(status("/.private"), "excluded_hidden");
    assert_eq!(status("/loose"), "unresolved_container_inspected");
    assert_eq!(report["summary"]["registry_entries"], 3);
    assert_eq!(report["summary"]["unobserved_registry_entries"], 1);
    assert_eq!(report["summary"]["enumeration_complete"], true);
    assert_eq!(report["summary"]["offdesk_ready"], false);
    assert_eq!(report["authorizes_dispatch"], false);
    Ok(())
}

#[test]
fn project_inventory_exposes_registry_errors_and_missing_roots_without_creating_profile(
) -> Result<()> {
    let temp = tempdir()?;
    let workspace = temp.path().join("workspace");
    fs::create_dir(&workspace)?;
    let home = temp.path().join("home");
    let missing = temp.path().join("missing-root");
    let registry = temp.path().join("registry.toml");
    let absent = inventory(&home, &[&workspace, &missing], &registry, &[])?;
    assert_eq!(absent["registry"]["status"], "missing");
    assert_eq!(absent["roots"][1]["status"], "unavailable");
    assert_eq!(absent["summary"]["enumeration_complete"], false);
    for text in ["bad toml [", "schema = 'wrong'", "schema = 'forager_project_registry.v1'\n[projects.bad]\nworkspace_patterns = ['bad']\naliases = [7]"] {
        write_file(&registry, text)?;
        let report = inventory(&home, &[&workspace], &registry, &[])?;
        assert_eq!(report["registry"]["status"], "invalid");
        assert!(report["registry"]["error"].is_string());
        assert_eq!(report["summary"]["registry_complete"], false);
    }
    assert!(!home.exists());
    Ok(())
}

#[test]
fn project_inventory_reports_overlap_alias_conflicts_and_explicit_empty_selection() -> Result<()> {
    let temp = tempdir()?;
    let workspace = temp.path().join("workspace");
    write_file(&workspace.join("alpha/AGENTS.md"), "alpha")?;
    let registry = temp.path().join("registry.toml");
    write_file(&registry, "schema = 'forager_project_registry.v1'\n[projects.a]\nworkspace_patterns = ['alpha']\naliases = ['Shared']\n[projects.b]\nworkspace_patterns = ['alpha']\naliases = ['shared']\n")?;
    let report = inventory(temp.path(), &[&workspace], &registry, &["--no-selection"])?;
    assert_eq!(
        report["entries"][0]["status"],
        "unresolved_registry_overlap"
    );
    assert_eq!(report["entries"][0]["project_keys"], json!(["a", "b"]));
    assert_eq!(report["registry"]["alias_conflicts"][0]["alias"], "shared");
    assert_eq!(report["selection"]["explicit_empty"], true);
    assert_eq!(report["entries"][0]["selected"], false);
    Ok(())
}

#[test]
fn project_inventory_declared_subprojects_reuse_root_and_owning_plan_validation() -> Result<()> {
    let temp = tempdir()?;
    let workspace = temp.path().join("workspace");
    let parent = workspace.join("parent");
    write_file(&parent.join("AGENTS.md"), "parent")?;
    write_file(&parent.join("methods/evolution/input.txt"), "owned input")?;
    write_file(&parent.join("TODO.md"), "authored TODO\n")?;
    let registry = temp.path().join("registry.toml");
    write_file(&registry, "schema = 'forager_project_registry.v1'\n[projects.parent]\nworkspace_patterns = ['parent']\n")?;
    let spec = temp.path().join("scope.json");
    let declaration = json!({"schema":"forager_subproject_scope.v1","project_key":"parent",
        "subproject_id":"evolution","label":"Annual evolution","anchor_path":"methods/evolution",
        "owning_plan_path":"TODO.md","read_paths":["methods/evolution/input.txt"]});
    write_file(&spec, &serde_json::to_string(&declaration)?)?;
    let before = inventory_snapshot(temp.path())?;
    let args = ["--subproject-spec", spec.to_str().unwrap()];
    let report = inventory(temp.path(), &[&workspace], &registry, &args)?;
    assert_eq!(inventory_snapshot(temp.path())?, before);
    assert_eq!(
        report["declared_subprojects"][0]["status"],
        "declared_scope_validated"
    );
    assert_eq!(
        report["declared_subprojects"][0]["owning_plan_path"],
        "TODO.md"
    );
    assert_eq!(
        report["declared_subprojects"][0]["project_root"],
        parent.to_str().unwrap()
    );
    let duplicate = inventory(
        temp.path(),
        &[&workspace],
        &registry,
        &[
            "--subproject-spec",
            spec.to_str().unwrap(),
            "--subproject-spec",
            spec.to_str().unwrap(),
        ],
    )?;
    assert_eq!(
        duplicate["declared_subprojects"][1]["status"],
        "unresolved_declaration"
    );
    let mut escaped = declaration;
    escaped["anchor_path"] = json!("../outside");
    write_file(&spec, &serde_json::to_string(&escaped)?)?;
    let rejected = inventory(temp.path(), &[&workspace], &registry, &args)?;
    assert_eq!(
        rejected["declared_subprojects"][0]["status"],
        "unresolved_declaration"
    );
    Ok(())
}

#[test]
fn project_inventory_limits_are_explicit_and_exclusions_are_preserved() -> Result<()> {
    let temp = tempdir()?;
    let workspace = temp.path().join("workspace");
    write_file(&workspace.join("group/deeper/Cargo.toml"), "deeper")?;
    write_file(&workspace.join("excluded/Cargo.toml"), "excluded")?;
    let registry = temp.path().join("registry.toml");
    write_file(&registry, "schema = 'forager_project_registry.v1'")?;
    let excluded = workspace.join("excluded");
    let shallow = inventory(
        temp.path(),
        &[&workspace],
        &registry,
        &[
            "--max-depth",
            "1",
            "--exclude",
            excluded.to_str().unwrap(),
            "--project-key",
            "unknown",
        ],
    )?;
    assert_eq!(shallow["entries"][0]["status"], "excluded_by_operator");
    assert_eq!(
        shallow["entries"][1]["status"],
        "unresolved_marker_free_depth_limit"
    );
    assert_eq!(
        shallow["selection"]["unknown_project_keys"],
        json!(["unknown"])
    );
    let bounded = inventory(
        temp.path(),
        &[&workspace],
        &registry,
        &["--max-entries", "1"],
    )?;
    assert_eq!(bounded["summary"]["truncated"], true);
    assert_eq!(bounded["summary"]["enumeration_complete"], false);
    assert_eq!(bounded["entries"].as_array().unwrap().len(), 1);
    Ok(())
}

#[cfg(unix)]
#[test]
fn project_inventory_deduplicates_symlinks_and_keeps_outside_and_unreadable_paths_visible(
) -> Result<()> {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let temp = tempdir()?;
    let workspace = temp.path().join("workspace");
    write_file(&workspace.join("alpha/AGENTS.md"), "alpha")?;
    write_file(&temp.path().join("outside/Cargo.toml"), "outside")?;
    symlink(workspace.join("alpha"), workspace.join("alias"))?;
    symlink(temp.path().join("outside"), workspace.join("escape"))?;
    let inaccessible = workspace.join("unreadable");
    fs::create_dir(&inaccessible)?;
    fs::set_permissions(&inaccessible, fs::Permissions::from_mode(0o0))?;
    let actually_unreadable = fs::read_dir(&inaccessible).is_err();
    let registry = temp.path().join("registry.toml");
    write_file(&registry, "schema = 'forager_project_registry.v1'\n[projects.alpha]\nworkspace_patterns = ['alpha']\n")?;
    let report = inventory(temp.path(), &[&workspace, &workspace], &registry, &[]);
    fs::set_permissions(&inaccessible, fs::Permissions::from_mode(0o700))?;
    let report = report?;
    let entries = report["entries"].as_array().unwrap();
    let by_name = |name: &str| {
        entries
            .iter()
            .find(|entry| entry["path"].as_str().unwrap().ends_with(name))
            .unwrap()
    };
    assert_eq!(by_name("/alias")["status"], "duplicate_path");
    assert_eq!(by_name("/escape")["status"], "excluded_outside_roots");
    assert_eq!(report["roots"][1]["status"], "duplicate_root");
    if actually_unreadable {
        assert_eq!(by_name("/unreadable")["status"], "unavailable");
    }
    Ok(())
}

fn forager_command(home: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_forager"));
    command.env("HOME", home);
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
    command.env_remove("FORAGER_DEBUG");
    command.env_remove("AGENT_OF_EMPIRES_DEBUG");
    #[cfg(target_os = "linux")]
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command
}

fn profile_dir(home: &Path) -> PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config")
            .join("forager")
            .join("profiles")
            .join("default")
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".forager").join("profiles").join("default")
    }
}

fn write_file(path: &Path, contents: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, contents)?;
    Ok(())
}

fn routing_command(home: &Path, repo: &Path, extra: &[&str]) -> Command {
    let mut command = forager_command(home);
    command
        .env("FORAGER_PROFILE", "../invalid-profile")
        .args(["project", "plan-routing"])
        .arg(repo)
        .args(["--project-key", "demo-project", "--json"])
        .args(extra);
    command
}

fn routing(home: &Path, repo: &Path, extra: &[&str]) -> Result<Value> {
    let output = routing_command(home, repo, extra).output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

fn apply_routing(home: &Path, repo: &Path, extra: &[&str], preview: &Value) -> Result<Value> {
    let output = routing_command(home, repo, extra)
        .args([
            "--reviewed",
            "--expected-preview-sha256",
            preview["preview_sha256"].as_str().unwrap(),
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

#[test]
fn project_plan_routing_discovery_is_read_only_and_requires_owner_selection() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("absent-home");
    let repo = temp.path().join("project");
    write_file(
        &repo.join("PROJECT_STATE.md"),
        "Current plan: [work](docs/team-todo.md).\n[missing](docs/missing.md)\n",
    )?;
    write_file(&repo.join("docs/team-todo.md"), "- [ ] Real task\n")?;
    let before = inventory_snapshot(temp.path())?;
    let report = routing(&home, &repo, &[])?;
    assert_eq!(
        report["preview"]["owner_status"],
        "candidate_requires_selection"
    );
    assert_eq!(report["preview"]["action"], "held");
    assert!(report["preview"]["first_read_links"]
        .as_array()
        .unwrap()
        .iter()
        .any(|link| link["target"] == "docs/missing.md" && link["status"] == "missing_or_unsafe"));
    assert_eq!(report, routing(&home, &repo, &[])?);
    assert_eq!(before, inventory_snapshot(temp.path())?);
    assert!(!home.exists());
    write_file(&repo.join("TODO.md"), "second owner?\n")?;
    assert_eq!(
        routing(&home, &repo, &[])?["preview"]["owner_status"],
        "ambiguous_requires_selection"
    );
    let blocked = routing(
        &home,
        &repo,
        &["--owning-plan", "other.md", "--create-plan"],
    )?;
    assert!(!blocked["preview"]["blockers"]
        .as_array()
        .unwrap()
        .is_empty());
    let result = routing_command(
        &home,
        &repo,
        &["--owning-plan", "other.md", "--create-plan"],
    )
    .args([
        "--reviewed",
        "--expected-preview-sha256",
        blocked["preview_sha256"].as_str().unwrap(),
    ])
    .output()?;
    assert!(!result.status.success());
    assert!(!repo.join("other.md").exists());
    Ok(())
}

#[test]
fn project_plan_routing_preserves_authored_bytes_and_is_idempotent() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    let authored = "# Authored instructions\r\nKeep scientific data unchanged.\r\n";
    write_file(&repo.join("AGENTS.md"), authored)?;
    write_file(&repo.join("TODO.md"), "- [ ] Original task\n")?;
    write_file(&repo.join("notes.md"), "working notes\n")?;
    fs::create_dir_all(repo.join("evidence"))?;
    let extra = [
        "--owning-plan",
        "TODO.md",
        "--document-route",
        "working_notes=notes.md",
        "--document-route",
        "evidence=evidence",
    ];
    let before = inventory_snapshot(&repo)?;
    let preview = routing(&home, &repo, &extra)?;
    assert_eq!(before, inventory_snapshot(&repo)?);
    assert_eq!(preview["preview"]["action"], "append_instructions");
    assert_eq!(
        apply_routing(&home, &repo, &extra, &preview)?["applied"],
        true
    );
    let agents = fs::read_to_string(repo.join("AGENTS.md"))?;
    assert!(agents.starts_with(authored));
    assert!(agents.contains("[TODO.md](TODO.md)"));
    assert!(agents.contains("working_notes: [notes.md](notes.md)"));
    assert_eq!(
        fs::read_to_string(repo.join("TODO.md"))?,
        "- [ ] Original task\n"
    );
    let repeated = routing(&home, &repo, &extra)?;
    assert_eq!(repeated["preview"]["action"], "unchanged");
    assert!(repeated["preview"]["first_read_links"]
        .as_array()
        .unwrap()
        .iter()
        .any(|link| link["target"] == "evidence" && link["status"] == "present_directory"));
    assert_eq!(
        apply_routing(&home, &repo, &extra, &repeated)?["applied"],
        false
    );
    assert_eq!(fs::read_to_string(repo.join("AGENTS.md"))?, agents);
    assert!(!home.exists());
    Ok(())
}

#[test]
fn project_plan_routing_fresh_project_establishes_one_file_before_instructions() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    fs::create_dir_all(repo.join("docs"))?;
    let establish = ["--owning-plan", "docs/TODO.md", "--create-plan"];
    let preview = routing(&home, &repo, &establish)?;
    assert!(!repo.join("docs/TODO.md").exists());
    assert_eq!(
        preview["preview"]["action"],
        "establish_plan_then_review_routing"
    );
    apply_routing(&home, &repo, &establish, &preview)?;
    assert!(repo.join("docs/TODO.md").exists());
    assert!(!repo.join("AGENTS.md").exists());
    let extra = ["--owning-plan", "docs/TODO.md"];
    let preview = routing(&home, &repo, &extra)?;
    apply_routing(&home, &repo, &extra, &preview)?;
    assert!(fs::read_to_string(repo.join("AGENTS.md"))?.contains("[docs/TODO.md](docs/TODO.md)"));
    assert!(!repo.join("NEXT_ACTIONS.md").exists());
    assert_eq!(
        routing(&home, &repo, &[])?["preview"]["owner_status"],
        "candidate_requires_selection"
    );
    Ok(())
}

#[test]
fn project_plan_routing_refuses_stale_hashes_and_generated_section_drift() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(&repo.join("AGENTS.md"), "original\n")?;
    write_file(&repo.join("TODO.md"), "original TODO\n")?;
    let extra = ["--owning-plan", "TODO.md"];
    for (path, changed) in [
        ("TODO.md", "new TODO\n"),
        ("AGENTS.md", "new instructions\n"),
    ] {
        let preview = routing(&home, &repo, &extra)?;
        write_file(&repo.join(path), changed)?;
        let snapshot = inventory_snapshot(&repo)?;
        let result = routing_command(&home, &repo, &extra)
            .args([
                "--reviewed",
                "--expected-preview-sha256",
                preview["preview_sha256"].as_str().unwrap(),
            ])
            .output()?;
        assert!(!result.status.success());
        assert!(String::from_utf8_lossy(&result.stderr).contains("preview hash changed"));
        assert_eq!(snapshot, inventory_snapshot(&repo)?);
    }
    let preview = routing(&home, &repo, &extra)?;
    apply_routing(&home, &repo, &extra, &preview)?;
    let agents = fs::read_to_string(repo.join("AGENTS.md"))?
        .replace("Default to Ondesk.", "Default to Offdesk.");
    write_file(&repo.join("AGENTS.md"), &agents)?;
    let drift = routing(&home, &repo, &extra)?;
    assert_eq!(drift["preview"]["action"], "held");
    assert!(drift["preview"]["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|b| b
            .as_str()
            .unwrap()
            .contains("generated routing section differs")));
    let result = routing_command(&home, &repo, &extra)
        .args([
            "--reviewed",
            "--expected-preview-sha256",
            drift["preview_sha256"].as_str().unwrap(),
        ])
        .output()?;
    assert!(!result.status.success());
    assert_eq!(fs::read_to_string(repo.join("AGENTS.md"))?, agents);
    Ok(())
}

#[test]
fn project_plan_routing_declared_subproject_routes_only_its_anchor() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(&repo.join("AGENTS.md"), "parent instructions\n")?;
    write_file(
        &repo.join("methods/evolution/AGENTS.md"),
        "evolution instructions\n",
    )?;
    write_file(&repo.join("methods/evolution/TODO.md"), "evolution tasks\n")?;
    write_file(&repo.join("methods/other/TODO.md"), "other tasks\n")?;
    let spec = temp.path().join("scope.json");
    fs::write(
        &spec,
        serde_json::to_vec(
            &json!({"schema":"forager_subproject_scope.v1","project_key":"demo-project","subproject_id":"evolution","label":"Evolution","anchor_path":"methods/evolution","owning_plan_path":"methods/evolution/TODO.md"}),
        )?,
    )?;
    let extra = ["--subproject-spec", spec.to_str().unwrap()];
    let preview = routing(&home, &repo, &extra)?;
    assert_eq!(preview["preview"]["scope_anchor"], "methods/evolution");
    assert!(preview["preview"]["subproject_spec_sha256"]
        .as_str()
        .is_some());
    apply_routing(&home, &repo, &extra, &preview)?;
    assert_eq!(
        fs::read_to_string(repo.join("AGENTS.md"))?,
        "parent instructions\n"
    );
    assert_eq!(
        fs::read_to_string(repo.join("methods/other/TODO.md"))?,
        "other tasks\n"
    );
    assert!(
        fs::read_to_string(repo.join("methods/evolution/AGENTS.md"))?
            .contains("(../../methods/evolution/TODO.md)")
    );
    let conflict = routing_command(
        &home,
        &repo,
        &[
            "--subproject-spec",
            spec.to_str().unwrap(),
            "--owning-plan",
            "methods/other/TODO.md",
        ],
    )
    .output()?;
    assert!(!conflict.status.success());
    Ok(())
}

#[cfg(unix)]
#[test]
fn project_plan_routing_preserves_claude_symlink_and_refuses_unsafe_targets() -> Result<()> {
    use std::os::unix::fs::symlink;
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(&repo.join("AGENTS.md"), "authored\n")?;
    write_file(&repo.join("TODO.md"), "tasks\n")?;
    symlink("AGENTS.md", repo.join("CLAUDE.md"))?;
    let extra = ["--owning-plan", "TODO.md"];
    let preview = routing(&home, &repo, &extra)?;
    assert_eq!(
        preview["preview"]["claude_route"],
        "symlink_to_agents_preserved"
    );
    apply_routing(&home, &repo, &extra, &preview)?;
    assert_eq!(
        fs::read_link(repo.join("CLAUDE.md"))?,
        PathBuf::from("AGENTS.md")
    );
    assert_eq!(
        fs::read(repo.join("CLAUDE.md"))?,
        fs::read(repo.join("AGENTS.md"))?
    );
    let outside = temp.path().join("outside.md");
    write_file(&outside, "outside canary\n")?;
    symlink(&outside, repo.join("linked-todo.md"))?;
    let held = routing(&home, &repo, &["--owning-plan", "linked-todo.md"])?;
    assert_eq!(
        held["preview"]["owner_status"],
        "selected_missing_or_unsafe"
    );
    fs::remove_file(repo.join("AGENTS.md"))?;
    symlink(&outside, repo.join("AGENTS.md"))?;
    let held = routing(&home, &repo, &extra)?;
    assert_eq!(held["preview"]["action"], "held");
    assert_eq!(fs::read_to_string(&outside)?, "outside canary\n");
    Ok(())
}

#[test]
fn project_plan_routing_reports_missing_document_routes_and_malformed_markers() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(&repo.join("TODO.md"), "tasks\n")?;
    let extra = [
        "--owning-plan",
        "TODO.md",
        "--document-route",
        "canonical=missing.md",
    ];
    let held = routing(&home, &repo, &extra)?;
    assert!(!held["preview"]["blockers"].as_array().unwrap().is_empty());
    let result = routing_command(&home, &repo, &extra)
        .args([
            "--reviewed",
            "--expected-preview-sha256",
            held["preview_sha256"].as_str().unwrap(),
        ])
        .output()?;
    assert!(!result.status.success());
    assert!(!repo.join("AGENTS.md").exists());
    write_file(
        &repo.join("AGENTS.md"),
        "authored\n<!-- forager:plan-routing:start -->\ntruncated\n",
    )?;
    assert_eq!(
        routing(&home, &repo, &["--owning-plan", "TODO.md"])?["preview"]["action"],
        "held"
    );
    for args in [
        ["--owning-plan", "../outside.md"],
        ["--document-route", "unknown=TODO.md"],
    ] {
        assert!(!routing_command(&home, &repo, &args)
            .output()?
            .status
            .success());
    }
    Ok(())
}

#[test]
fn project_plan_routing_handles_spaced_paths_and_rejects_replaced_project_identity() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(&repo.join("docs/team todo.md"), "- [ ] Agreed work\n")?;
    let extra = ["--owning-plan", "docs/team todo.md"];
    let preview = routing(&home, &repo, &extra)?;
    apply_routing(&home, &repo, &extra, &preview)?;
    assert!(fs::read_to_string(repo.join("AGENTS.md"))?
        .contains("[docs/team todo.md](<docs/team todo.md>)"));
    let repeated = routing(&home, &repo, &extra)?;
    assert_eq!(repeated["preview"]["action"], "unchanged");
    assert!(repeated["preview"]["first_read_links"]
        .as_array()
        .unwrap()
        .iter()
        .any(|link| link["target"] == "docs/team todo.md" && link["status"] == "present"));
    let agents = fs::read(repo.join("AGENTS.md"))?;
    fs::rename(&repo, temp.path().join("old-project"))?;
    write_file(&repo.join("docs/team todo.md"), "- [ ] Agreed work\n")?;
    fs::write(repo.join("AGENTS.md"), agents)?;
    let before = inventory_snapshot(&repo)?;
    let result = routing_command(&home, &repo, &extra)
        .args([
            "--reviewed",
            "--expected-preview-sha256",
            repeated["preview_sha256"].as_str().unwrap(),
        ])
        .output()?;
    assert!(!result.status.success());
    assert_eq!(before, inventory_snapshot(&repo)?);
    Ok(())
}

#[test]
#[serial]
fn project_init_links_explicit_existing_owner_without_project_writes() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(&repo.join("AGENTS.md"), "authored\n")?;
    write_file(&repo.join("docs/TODO.md"), "- [ ] Existing work\n")?;
    write_file(
        &repo.join("PROJECT_STATE.md"),
        &format!(
            "Owning plan: [TODO](docs/TODO.md)\n{}",
            "Preserved history\n".repeat(5000)
        ),
    )?;
    let before = inventory_snapshot(&repo)?;
    let output = forager_command(&home)
        .args(["project", "init"])
        .arg(&repo)
        .args([
            "--project-key",
            "demo-project",
            "--owning-plan",
            "docs/TODO.md",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let packet: Value = serde_json::from_slice(&output.stdout)?;
    let profile: Value = serde_json::from_slice(&fs::read(
        packet["artifacts"]["operation_profile_json"]
            .as_str()
            .unwrap(),
    )?)?;
    assert_eq!(
        profile["scope_model"]["project_target"]["owning_plan_path"],
        "docs/TODO.md"
    );
    assert_eq!(
        profile["owning_plan_routing"]["owner_status"],
        "explicit_existing_owner"
    );
    assert!(profile["owning_plan_routing"]["blockers"]
        .as_array()
        .unwrap()
        .is_empty());
    assert_eq!(before, inventory_snapshot(&repo)?);
    Ok(())
}

#[test]
#[serial]
fn project_governance_hints_reuse_existing_owner_and_hold_missing_owner_links() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    let home = temp.path().join("home");
    write_file(
        &repo.join("PROJECT_STATE.md"),
        "Owning plan: [roadmap](docs/roadmap.md)\n",
    )?;
    write_file(&repo.join("docs/roadmap.md"), "- [ ] Real agreed work\n")?;
    let command = |home: &Path, repo: &Path| {
        let mut cmd = forager_command(home);
        cmd.args(["project", "apply-governance-hints"])
            .arg(repo)
            .args([
                "--project-key",
                "demo-project",
                "--surface",
                "next-actions",
                "--reviewed",
                "--json",
            ]);
        cmd
    };
    let output = command(&home, &repo).output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let applied: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(applied["operations"][0]["status"], "skipped_existing");
    assert_eq!(applied["operations"][0]["path"], "docs/roadmap.md");
    assert!(!repo.join("NEXT_ACTIONS.md").exists());
    fs::remove_file(repo.join("docs/roadmap.md"))?;
    let output = command(&home, &repo).output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let applied: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(applied["operations"][0]["status"], "held_owner_resolution");
    assert!(!repo.join("NEXT_ACTIONS.md").exists());
    Ok(())
}

fn write_synthetic_project(repo: &Path) -> Result<()> {
    write_file(repo.join("AGENTS.md").as_path(), "Project instructions\n")?;
    write_file(repo.join("README.md").as_path(), "# Synthetic Project\n")?;
    write_file(
        repo.join("pyproject.toml").as_path(),
        "[project]\nname='demo'\n",
    )?;
    write_file(
        repo.join("docs/operations/RunLog.md").as_path(),
        "# RunLog\n- initial note\n",
    )?;
    write_file(
        repo.join("modules/01_research/README.md").as_path(),
        "# Module 01\n",
    )?;
    write_file(
        repo.join("modules/01_research/contract.yaml").as_path(),
        "name: module01\n",
    )?;
    write_file(
        repo.join("modules/01_research/scripts/run.sh").as_path(),
        "#!/usr/bin/env bash\necho run\n",
    )?;
    write_file(
        repo.join("modules/01_research/tests/test_placeholder.py")
            .as_path(),
        "def test_placeholder():\n    assert True\n",
    )?;
    Ok(())
}

fn write_module_profile_spec(path: &Path) -> Result<()> {
    write_file(
        path,
        r#"{
  "profile_kind": "demo_module01_operation_profile",
  "project_key": "demo-project",
  "module_path": "modules/01_research",
  "profile_artifact": "module01_operation_profile.json",
  "commands": [
    {
      "purpose": "build_evidence_bundle",
      "command_template": "scripts/build_demo_evidence_bundle.py --repo {repo} --out {evidence_bundle}",
      "writes_target_project_state": false,
      "requires_runtime_approval": false
    },
    {
      "purpose": "review_evidence_bundle",
      "command_template": "scripts/review_demo_evidence_bundle.py --bundle {evidence_bundle} --out {evidence_review}",
      "writes_target_project_state": false,
      "requires_runtime_approval": false
    },
    {
      "purpose": "build_module_operation_profile",
      "command_template": "scripts/build_demo_module_profile.py --repo {repo} --scope {scope_ref} --out {module_profile}",
      "writes_target_project_state": false,
      "requires_runtime_approval": false
    },
    {
      "purpose": "prepare_offdesk_task_after_review",
      "command_template": "scripts/prepare_demo_offdesk_task.py --repo {repo} --project-key {project_key} --module-path {module_path}",
      "writes_target_project_state": false,
      "requires_runtime_approval": true
    }
  ]
}
"#,
    )
}

#[test]
#[serial]
fn project_init_writes_read_only_initialization_packet() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let before_entries = fs::read_dir(&repo)?.count();
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--operation-target",
            "modules/01_research",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["kind"], "forager_project_initialization");
    assert_eq!(json["project_key"], "demo-project");
    assert_eq!(json["read_only_project_state"], true);
    assert_eq!(json["requires_operator_review"], true);
    assert_eq!(json["summary"]["module_candidate_count"], 1);
    assert_eq!(json["summary"]["operation_target_count"], 1);
    assert_eq!(json["summary"]["ready_for_offdesk_runtime"], false);

    let artifacts = &json["artifacts"];
    for key in [
        "operation_profile_json",
        "onboarding_markdown",
        "module_candidates_json",
        "module_operation_preflight_json",
        "evidence_collector_plan_markdown",
        "governance_surface_hints_markdown",
        "wiki_seed_candidates_json",
        "ondesk_start_package_markdown",
        "offdesk_ready_check_json",
    ] {
        let path = PathBuf::from(artifacts[key].as_str().expect("artifact path"));
        assert!(path.exists(), "missing artifact {key}: {}", path.display());
    }

    let profile_path = PathBuf::from(
        artifacts["operation_profile_json"]
            .as_str()
            .expect("profile path"),
    );
    let profile: Value = serde_json::from_str(&fs::read_to_string(profile_path)?)?;
    assert_eq!(profile["kind"], "forager_project_operation_profile");
    assert_eq!(
        profile["scope_model"]["project_target"]["scope_ref"],
        "demo-project"
    );
    assert_eq!(
        profile["scope_model"]["operation_targets"][0]["scope_ref"],
        "module01_research"
    );
    assert_eq!(
        profile["scope_model"]["operation_targets"][0]["role"],
        "module_operation_target"
    );
    assert_eq!(
        profile["initialization_policy"]["grants_execution_authority"],
        false
    );
    assert!(profile["ondesk_bridge"]["first_reads"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "MODULE_OPERATION_PREFLIGHT.json"));
    assert!(profile["ondesk_bridge"]["first_reads"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "GOVERNANCE_SURFACE_HINTS.md"));
    assert_eq!(
        profile["module_operation_preflight_path"],
        artifacts["module_operation_preflight_json"]
    );
    assert_eq!(
        profile["governance_surface_hints_path"],
        artifacts["governance_surface_hints_markdown"]
    );
    assert!(profile["agent_modes"]
        .as_array()
        .unwrap()
        .iter()
        .any(|mode| mode["mode"] == "critique"));
    assert!(
        profile["safety_policy"]["forbidden_without_separate_approval"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item.as_str().unwrap().contains("delete"))
    );

    let modules_path = PathBuf::from(
        artifacts["module_candidates_json"]
            .as_str()
            .expect("modules path"),
    );
    let modules: Value = serde_json::from_str(&fs::read_to_string(modules_path)?)?;
    let candidate = &modules["candidates"][0];
    assert_eq!(candidate["module_id"], "module01_research");
    assert_eq!(candidate["scope_kind"], "module");
    assert_eq!(candidate["scope_ref"], "module01_research");
    assert_eq!(candidate["parent_project_key"], "demo-project");
    assert_eq!(candidate["selected_operation_target"], true);
    assert_eq!(candidate["path"], "modules/01_research");
    assert_eq!(
        candidate["operation_profile_status"],
        "operation_target_requires_module_profile_review"
    );
    assert!(candidate["entrypoints"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["path"] == "modules/01_research/scripts/run.sh"));

    let preflight_path = PathBuf::from(
        artifacts["module_operation_preflight_json"]
            .as_str()
            .expect("preflight path"),
    );
    let preflight: Value = serde_json::from_str(&fs::read_to_string(preflight_path)?)?;
    assert_eq!(preflight["kind"], "forager_module_operation_preflight");
    assert_eq!(preflight["ready_for_offdesk_runtime"], false);
    assert_eq!(
        preflight["operation_targets"][0]["scope_ref"],
        "module01_research"
    );
    assert_eq!(
        preflight["operation_targets"][0]["readiness_level"],
        "manual_profile_authoring_required"
    );
    assert!(preflight["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "no_known_module_profile_builder"));

    let ready_path = PathBuf::from(
        artifacts["offdesk_ready_check_json"]
            .as_str()
            .expect("ready path"),
    );
    let ready: Value = serde_json::from_str(&fs::read_to_string(ready_path)?)?;
    assert_eq!(ready["ready_for_ondesk_start"], true);
    assert_eq!(ready["ready_for_offdesk_runtime"], false);
    assert!(ready["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "operation_targets_require_module_profile_review"));
    assert_eq!(json["summary"]["governance_surface_missing_count"], 4);

    let governance_hints = fs::read_to_string(
        artifacts["governance_surface_hints_markdown"]
            .as_str()
            .expect("governance hints path"),
    )?;
    assert!(governance_hints.contains("Governance Surface Hints"));
    assert!(governance_hints.contains("`PROJECT_STATE.md`"));
    assert!(governance_hints.contains("`NEXT_ACTIONS.md`"));
    assert!(governance_hints.contains("`DECISIONS.md`"));
    assert!(governance_hints.contains("`DELIVERABLES.md`"));
    assert!(governance_hints.contains("forager project audit-docs"));

    let start_package = fs::read_to_string(
        artifacts["ondesk_start_package_markdown"]
            .as_str()
            .expect("start package path"),
    )?;
    assert!(start_package.contains("Ondesk Start Package"));
    assert!(start_package.contains("scope=`module:module01_research`"));
    assert!(start_package.contains("Runtime execution, wiki promotion, and file cleanup"));

    assert_eq!(fs::read_dir(&repo)?.count(), before_entries);
    assert!(!repo.join(".forager").exists());
    Ok(())
}

#[test]
#[serial]
fn project_init_rejects_unknown_operation_target() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--operation-target",
            "modules/missing",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("operation target not found"));
    Ok(())
}

#[test]
#[serial]
fn project_init_selects_one_declared_subproject_without_cross_scope_authority() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    write_file(&repo.join("TODO.md"), "# Tasks\n")?;
    write_file(&repo.join("wiki/methods/annual/README.md"), "# Annual\n")?;
    write_file(&repo.join("configs/annual.json"), "{}\n")?;
    write_file(&repo.join("pipelines/mapping/README.md"), "# Mapping\n")?;
    let annual_spec = temp.path().join("annual-scope.json");
    let mapping_spec = temp.path().join("mapping-scope.json");
    write_file(
        &annual_spec,
        &serde_json::json!({
            "schema": "forager_subproject_scope.v1",
            "project_key": "demo-project",
            "subproject_id": "annual-evolution",
            "label": "Annual evolution",
            "anchor_path": "wiki/methods/annual",
            "read_paths": ["configs/annual.json"],
            "owning_plan_path": "TODO.md"
        })
        .to_string(),
    )?;
    write_file(
        &mapping_spec,
        &serde_json::json!({
            "schema": "forager_subproject_scope.v1",
            "project_key": "demo-project",
            "subproject_id": "mapping",
            "label": "Mapping",
            "anchor_path": "pipelines/mapping",
            "owning_plan_path": "TODO.md"
        })
        .to_string(),
    )?;
    let before_entries = fs::read_dir(&repo)?.count();
    let out_dir = temp.path().join("init-packet");
    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().unwrap(),
            "--project-key",
            "demo-project",
            "--subproject-spec",
            annual_spec.to_str().unwrap(),
            "--subproject-spec",
            mapping_spec.to_str().unwrap(),
            "--operation-target",
            "annual-evolution",
            "--out",
            out_dir.to_str().unwrap(),
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["summary"]["operation_target_count"], 1);
    let profile: Value = serde_json::from_str(&fs::read_to_string(
        out_dir.join("PROJECT_OPERATION_PROFILE.json"),
    )?)?;
    assert_eq!(
        profile["scope_model"]["project_target"]["scope_ref"],
        "demo-project"
    );
    let target = &profile["scope_model"]["operation_targets"][0];
    assert_eq!(target["scope_kind"], "subproject");
    assert_eq!(target["scope_ref"], "annual-evolution");
    assert_eq!(target["owning_plan_path"], "TODO.md");
    assert_eq!(
        target["read_paths"],
        serde_json::json!(["configs/annual.json", "wiki/methods/annual"])
    );
    let preflight: Value = serde_json::from_str(&fs::read_to_string(
        out_dir.join("MODULE_OPERATION_PREFLIGHT.json"),
    )?)?;
    assert_eq!(
        preflight["operation_targets"][0]["scope_ref"],
        "annual-evolution"
    );
    assert_eq!(
        preflight["operation_targets"][0]["scope_kind"],
        "subproject"
    );
    assert_eq!(preflight["ready_for_offdesk_runtime"], false);
    let candidates: Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("MODULE_CANDIDATES.json"))?)?;
    assert!(candidates["candidates"]
        .as_array()
        .unwrap()
        .iter()
        .any(|candidate| {
            candidate["scope_ref"] == "mapping" && candidate["selected_operation_target"] == false
        }));
    assert_eq!(fs::read_dir(&repo)?.count(), before_entries);
    Ok(())
}

#[test]
#[serial]
fn project_init_rejects_subproject_path_outside_project() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    write_file(&repo.join("TODO.md"), "# Tasks\n")?;
    let outside = temp.path().join("outside");
    write_file(&outside.join("README.md"), "# Outside\n")?;
    let spec = temp.path().join("scope.json");
    write_file(
        &spec,
        &serde_json::json!({
            "schema": "forager_subproject_scope.v1",
            "project_key": "demo-project",
            "subproject_id": "outside",
            "label": "Outside",
            "anchor_path": "../outside",
            "owning_plan_path": "TODO.md"
        })
        .to_string(),
    )?;
    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().unwrap(),
            "--project-key",
            "demo-project",
            "--subproject-spec",
            spec.to_str().unwrap(),
            "--operation-target",
            "outside",
            "--out",
            temp.path().join("init-packet").to_str().unwrap(),
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("outside scope"));
    Ok(())
}

#[test]
#[serial]
fn project_implementation_packet_writes_read_only_alignment_packet() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let before_entries = fs::read_dir(&repo)?.count();
    let out_dir = temp.path().join("implementation-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "implementation-packet",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--goal",
            "Make delegated implementation preserve the original Forager product intent.",
            "--success-state",
            "A worker can execute from a packet without using chat scrollback.",
            "--why-now",
            "Local model overnight work needs stronger design before execution.",
            "--north-star-fit",
            "Returns the operator to evidence, choices, and continuity.",
            "--brand-fit",
            "Keeps Forager as a local meta-harness rather than one hosted agent.",
            "--scope",
            "Typed implementation packet state",
            "--exclude",
            "Runtime launch approval",
            "--allowed-file",
            "src/offdesk/implementation_packet.rs",
            "--capability",
            "FD-016: implementation packet and recursive alignment review",
            "--approach",
            "Create typed state plus a read-only project CLI packet generator.",
            "--work-slice",
            "Add Rust state contract",
            "--work-slice",
            "Add CLI JSON projection",
            "--data-contract",
            "implementation_packet.v1",
            "--data-contract",
            "recursive_alignment_review.v1",
            "--preferred-worker",
            "deterministic_script",
            "--stop-condition",
            "Missing validation command or expected artifact",
            "--validation-command",
            "cargo test --test project_init_cli project_implementation_packet_writes_read_only_alignment_packet",
            "--evidence-ref",
            "docs/implementation-packet.md",
            "--expected-artifact",
            "IMPLEMENTATION_PACKET.json",
            "--handoff-requirement",
            "Morning Ondesk review must show whether original intent was served.",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["kind"], "forager_project_implementation_packet");
    assert_eq!(json["project_key"], "demo-project");
    assert_eq!(json["read_only_project_state"], true);
    assert_eq!(json["grants_runtime_authority"], false);
    assert_eq!(json["summary"]["safe_to_delegate"], true);
    assert_eq!(json["summary"]["outcome"], "pass");
    assert_eq!(json["summary"]["required_revision_count"], 0);
    assert_eq!(json["packet"]["schema"], "implementation_packet.v1");
    assert_eq!(
        json["packet"]["recursive_alignment_review"]["schema"],
        "recursive_alignment_review.v1"
    );
    assert_eq!(
        json["packet"]["recursive_alignment_review"]["checks"]["scope_balance"],
        "right_sized"
    );
    assert_eq!(
        json["packet"]["closeout"]["accepted_truth_rule"],
        "Execution completion is not acceptance; closeout must compare actual results against this implementation packet."
    );

    let artifacts = &json["artifacts"];
    for key in [
        "implementation_packet_json",
        "recursive_alignment_review_json",
        "implementation_packet_markdown",
    ] {
        let path = PathBuf::from(artifacts[key].as_str().expect("artifact path"));
        assert!(path.exists(), "missing artifact {key}: {}", path.display());
    }

    let packet_path = PathBuf::from(
        artifacts["implementation_packet_json"]
            .as_str()
            .expect("packet path"),
    );
    let packet: Value = serde_json::from_str(&fs::read_to_string(packet_path)?)?;
    assert_eq!(packet["packet_id"], json["packet_id"]);
    assert_eq!(packet["capability_mapping"][0]["capability_id"], "FD-016");
    assert_eq!(
        packet["validation"]["evidence_required"][0],
        "docs/implementation-packet.md"
    );

    let review_path = PathBuf::from(
        artifacts["recursive_alignment_review_json"]
            .as_str()
            .expect("review path"),
    );
    let review: Value = serde_json::from_str(&fs::read_to_string(review_path)?)?;
    assert_eq!(review["outcome"], "pass");
    assert_eq!(review["safe_to_delegate"], true);

    let markdown = fs::read_to_string(
        artifacts["implementation_packet_markdown"]
            .as_str()
            .expect("markdown path"),
    )?;
    assert!(markdown.contains("Implementation Packet"));
    assert!(markdown.contains("safe_to_delegate: `true`"));
    assert!(markdown.contains("Runtime launch approval"));

    assert_eq!(fs::read_dir(&repo)?.count(), before_entries);
    assert!(!repo.join(".forager").exists());
    Ok(())
}

#[test]
#[serial]
fn project_implementation_packet_flags_missing_delegation_requirements() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("implementation-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "implementation-packet",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--goal",
            "Draft a weak packet for validation.",
            "--success-state",
            "The packet exposes missing execution requirements.",
            "--scope",
            "Packet shell only",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["summary"]["safe_to_delegate"], false);
    assert_eq!(json["summary"]["outcome"], "revise");
    let revisions = json["packet"]["recursive_alignment_review"]["required_revisions"]
        .as_array()
        .expect("required revisions");
    for expected in [
        "excluded_scope_missing",
        "work_slices_missing",
        "stop_conditions_missing",
        "validation_plan_missing",
        "expected_artifacts_missing",
    ] {
        assert!(
            revisions.iter().any(|item| item == expected),
            "missing revision {expected}: {revisions:?}"
        );
    }
    Ok(())
}

#[test]
#[serial]
fn project_apply_governance_hints_dry_run_does_not_write_surfaces() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "apply-governance-hints",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["kind"], "forager_project_governance_hints_application");
    assert_eq!(json["reviewed"], false);
    assert_eq!(json["writes_target_project_state"], false);
    assert_eq!(json["requires_operator_review"], true);
    assert_eq!(json["planned_count"], 4);
    assert_eq!(json["created_count"], 0);
    assert_eq!(json["skipped_existing_count"], 0);
    assert!(json["operations"]
        .as_array()
        .unwrap()
        .iter()
        .all(|operation| operation["status"] == "planned_create"));
    for path in [
        "PROJECT_STATE.md",
        "NEXT_ACTIONS.md",
        "DECISIONS.md",
        "DELIVERABLES.md",
    ] {
        assert!(!repo.join(path).exists(), "dry-run wrote {path}");
    }
    Ok(())
}

#[test]
#[serial]
fn project_apply_governance_hints_reviewed_creates_missing_surfaces() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "apply-governance-hints",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--reviewed",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["reviewed"], true);
    assert_eq!(json["writes_target_project_state"], true);
    assert_eq!(json["requires_operator_review"], false);
    assert_eq!(json["planned_count"], 4);
    assert_eq!(json["created_count"], 4);
    assert_eq!(json["skipped_existing_count"], 0);
    assert!(json["operations"]
        .as_array()
        .unwrap()
        .iter()
        .all(|operation| operation["status"] == "created"));
    for path in [
        "PROJECT_STATE.md",
        "NEXT_ACTIONS.md",
        "DECISIONS.md",
        "DELIVERABLES.md",
    ] {
        assert!(repo.join(path).exists(), "missing created {path}");
    }

    let audit = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;
    assert!(
        audit.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&audit.stdout),
        String::from_utf8_lossy(&audit.stderr)
    );
    let audit_json: Value = serde_json::from_slice(&audit.stdout)?;
    assert_eq!(audit_json["findings"].as_array().unwrap().len(), 0);
    assert_eq!(audit_json["recommendations"].as_array().unwrap().len(), 0);
    Ok(())
}

#[test]
#[serial]
fn project_apply_governance_hints_reviewed_never_overwrites_existing_surface() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nsentinel-current-state\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "apply-governance-hints",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--reviewed",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["planned_count"], 3);
    assert_eq!(json["created_count"], 3);
    assert_eq!(json["skipped_existing_count"], 1);
    assert!(json["operations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|operation| operation["role"] == "current_state"
            && operation["status"] == "skipped_existing"
            && operation["path"] == "PROJECT_STATE.md"));
    assert!(fs::read_to_string(repo.join("PROJECT_STATE.md"))?.contains("sentinel-current-state"));
    Ok(())
}

#[test]
#[serial]
fn project_init_preflight_uses_explicit_module_profile_spec() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let spec_path = temp.path().join("module_profile_spec.json");
    write_module_profile_spec(&spec_path)?;
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--operation-target",
            "modules/01_research",
            "--module-profile-spec",
            spec_path.to_str().expect("utf-8 spec path"),
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    let preflight_path = PathBuf::from(
        json["artifacts"]["module_operation_preflight_json"]
            .as_str()
            .expect("preflight path"),
    );
    let preflight: Value = serde_json::from_str(&fs::read_to_string(preflight_path)?)?;
    let target = &preflight["operation_targets"][0];
    assert_eq!(target["scope_ref"], "module01_research");
    assert_eq!(
        target["recognized_profile_kind"],
        "demo_module01_operation_profile"
    );
    assert_eq!(target["profile_builder_available"], true);
    assert_eq!(target["evidence_bundle_builder_available"], true);
    assert!(target["recommended_commands"]
        .as_array()
        .unwrap()
        .iter()
        .any(|command| command["purpose"] == "build_evidence_bundle"
            && command["command"]
                .as_str()
                .unwrap()
                .contains("build_demo_evidence_bundle.py")));
    assert!(target["recommended_commands"]
        .as_array()
        .unwrap()
        .iter()
        .any(
            |command| command["purpose"] == "prepare_offdesk_task_after_review"
                && command["requires_runtime_approval"] == true
        ));
    Ok(())
}

#[test]
#[serial]
fn project_init_preflight_does_not_auto_recognize_domain_modules() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--operation-target",
            "modules/01_research",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    let preflight_path = PathBuf::from(
        json["artifacts"]["module_operation_preflight_json"]
            .as_str()
            .expect("preflight path"),
    );
    let preflight: Value = serde_json::from_str(&fs::read_to_string(preflight_path)?)?;
    let target = &preflight["operation_targets"][0];
    assert!(target["recognized_profile_kind"].is_null());
    assert_eq!(target["profile_builder_available"], false);
    assert_eq!(target["evidence_bundle_builder_available"], false);
    assert!(target["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|blocker| blocker == "no_known_module_profile_builder"));
    assert!(target["recommended_commands"]
        .as_array()
        .unwrap()
        .iter()
        .any(|command| command["purpose"] == "author_module_operation_profile"));
    let text = serde_json::to_string(&preflight)?;
    assert!(!text.contains("twinpaper"));
    assert!(!text.contains("module03"));
    Ok(())
}

#[test]
#[serial]
fn project_init_refuses_non_empty_output_without_force() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("init-packet");
    fs::create_dir_all(&out_dir)?;
    fs::write(out_dir.join("existing.txt"), "keep")?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("output directory is not empty"));
    assert!(out_dir.join("existing.txt").exists());
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_accepts_clean_governance_surfaces() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("AGENTS.md").as_path(),
        "First read `PROJECT_STATE.md`, `NEXT_ACTIONS.md`, `DECISIONS.md`, and `DELIVERABLES.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("NEXT_ACTIONS.md").as_path(), "# Next Actions\n")?;
    write_file(
        repo.join("DECISIONS.md").as_path(),
        "# Decisions\n\n- Source: `PROJECT_STATE.md`\n",
    )?;
    write_file(
        repo.join("outputs/report.html").as_path(),
        "<h1>Report</h1>\n",
    )?;
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- `outputs/report.html`: inspection report.\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "research-longrun",
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["schema"], "documentation_governance_audit_v1");
    assert_eq!(json["profile"], "research-longrun");
    assert_eq!(json["findings"].as_array().unwrap().len(), 0);
    assert_eq!(json["recommendations"].as_array().unwrap().len(), 0);
    assert_eq!(json["summary"]["surfaces"]["current_present"], true);
    assert_eq!(json["summary"]["deliverables"]["output_candidates"], 1);
    assert_eq!(
        json["summary"]["deliverables"]["referenced_human_outputs"],
        1
    );
    assert_eq!(
        json["summary"]["deliverables"]["unreferenced_human_outputs"],
        0
    );
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_rejects_missing_deliverable_path() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("DECISIONS.md").as_path(), "# Decisions\n")?;
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- `outputs/missing.html`: missing report.\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;

    assert!(
        !output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert!(json["findings"].as_array().unwrap().iter().any(|finding| {
        finding["severity"] == "error" && finding["code"] == "missing_deliverable_path"
    }));
    assert!(json["recommendations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|recommendation| recommendation["kind"] == "repair_deliverables_surface"));
    assert_eq!(
        json["summary"]["deliverables"]["missing_paths"][0],
        "outputs/missing.html"
    );
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("documentation governance audit found error findings"));
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_recommends_focused_output_review() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("DECISIONS.md").as_path(), "# Decisions\n")?;
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- `README.md`: source overview.\n",
    )?;
    for index in 0..7 {
        write_file(
            repo.join(format!("outputs/report-{index}.html")).as_path(),
            &"x".repeat((index + 1) * 10),
        )?;
    }
    let markdown_path = temp.path().join("audit.md");

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--current-stale-days",
            "36500",
            "--md-out",
            markdown_path.to_str().expect("utf-8 markdown path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["summary"]["deliverables"]["unreferenced_human_outputs"],
        7
    );
    let recommendation = json["recommendations"]
        .as_array()
        .unwrap()
        .iter()
        .find(|recommendation| recommendation["kind"] == "review_human_output_candidates")
        .expect("focused output review recommendation");
    assert_eq!(recommendation["priority"], "normal");
    assert!(recommendation["suggested_action"]
        .as_str()
        .unwrap()
        .contains("RETENTION_REVIEW.md"));
    let paths = recommendation["paths"].as_array().unwrap();
    assert_eq!(paths.len(), 5);
    assert_eq!(paths[0], "outputs/report-6.html");
    assert_eq!(paths[4], "outputs/report-2.html");

    let markdown = fs::read_to_string(markdown_path)?;
    assert!(markdown.contains("Recommendations"));
    assert!(markdown.contains("review_human_output_candidates"));
    assert!(markdown.contains("RETENTION_REVIEW.md"));
    assert!(markdown.contains("For the full machine-readable summary"));
    assert!(!markdown.contains("Machine Summary"));
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_recommends_adaptive_wiki_projection_export() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("AGENTS.md").as_path(),
        "First read `PROJECT_STATE.md`, `NEXT_ACTIONS.md`, `DECISIONS.md`, and `DELIVERABLES.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("NEXT_ACTIONS.md").as_path(), "# Next Actions\n")?;
    write_file(repo.join("DECISIONS.md").as_path(), "# Decisions\n")?;
    write_file(repo.join("DELIVERABLES.md").as_path(), "# Deliverables\n")?;

    let adaptive_profile = temp
        .path()
        .join(".config")
        .join("forager")
        .join("profiles")
        .join("demo");
    write_file(
        adaptive_profile
            .join("adaptive_wiki_entries.json")
            .as_path(),
        r#"{"version":"2026-05-14.v0","entries":[]}"#,
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--adaptive-profile-dir",
            adaptive_profile.to_str().expect("utf-8 profile path"),
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["summary"]["adaptive_wiki"]["projection_state"],
        "missing"
    );
    assert!(json["findings"].as_array().unwrap().iter().any(|finding| {
        finding["severity"] == "warn" && finding["code"] == "missing_adaptive_wiki_projection"
    }));
    let recommendation = json["recommendations"]
        .as_array()
        .unwrap()
        .iter()
        .find(|recommendation| recommendation["kind"] == "reexport_adaptive_wiki_projection")
        .expect("adaptive wiki projection recommendation");
    assert_eq!(recommendation["priority"], "normal");
    assert_eq!(
        recommendation["command"],
        "forager -p demo offdesk wiki export-markdown"
    );
    Ok(())
}

#[test]
#[serial]
fn project_artifact_index_tracks_human_outputs_and_missing_refs() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- Main report: `outputs/report.html`\n- Missing export: `outputs/missing.pdf`\n",
    )?;
    write_file(
        repo.join("outputs/report.html").as_path(),
        "<html><body>report</body></html>\n",
    )?;
    write_file(repo.join("outputs/plot.png").as_path(), "png-bytes\n")?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "artifact-index",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["schema"], "artifact_index.v1");
    assert_eq!(json["project_key"], "demo-project");
    assert!(json["authority"]["read_only"].as_bool().unwrap_or(false));
    assert!(
        json["summary"]["human_facing_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 3
    );
    assert_eq!(json["summary"]["missing_entries"], 1);

    let entries = json["entries"].as_array().expect("artifact entries");
    assert!(entries.iter().any(|entry| {
        entry["relative_path"] == "outputs/report.html"
            && entry["source"] == "project_deliverables"
            && entry["retention_class"] == "handoff"
            && entry["present"] == true
    }));
    assert!(entries.iter().any(|entry| {
        entry["relative_path"] == "outputs/plot.png"
            && entry["source"] == "project_output_scan"
            && entry["review_status"] == "needs_triage"
    }));
    assert!(entries.iter().any(|entry| {
        entry["relative_path"] == "outputs/missing.pdf"
            && entry["source"] == "project_deliverables"
            && entry["present"] == false
            && entry["review_status"] == "missing"
    }));

    let review_output = forager_command(temp.path())
        .args([
            "project",
            "retention-review",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&review_output.stdout),
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: Value = serde_json::from_slice(&review_output.stdout)?;
    assert_eq!(review["schema"], "artifact_retention_review.v1");
    assert!(review["authority"]["read_only"].as_bool().unwrap_or(false));
    assert_eq!(review["summary"]["missing_entries"], 1);
    assert!(
        review["summary"]["by_scope"]["project_surface"]["action_required_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 2
    );
    assert!(
        review["summary"]["action_required_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 2
    );
    assert!(review["recommendations"]
        .as_array()
        .expect("retention recommendations")
        .iter()
        .any(
            |recommendation| recommendation["kind"] == "restore_or_update_missing_artifacts"
                && recommendation["priority"] == "high"
        ));
    let action_items = review["queues"]["action_required"]
        .as_array()
        .expect("action required items");
    assert!(action_items.iter().any(|item| {
        item["relative_path"] == "outputs/missing.pdf"
            && item["recommended_action"] == "restore_or_update_reference"
            && item["retention_scope"] == "project_surface"
    }));
    assert!(action_items.iter().any(|item| {
        item["relative_path"] == "outputs/plot.png"
            && item["recommended_action"] == "promote_to_deliverables_or_mark_disposable"
            && item["retention_scope"] == "project_surface"
    }));
    assert!(!profile_dir(temp.path()).exists());

    let request_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        request_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&request_output.stdout),
        String::from_utf8_lossy(&request_output.stderr)
    );
    let request: Value = serde_json::from_slice(&request_output.stdout)?;
    assert_eq!(request["schema"], "artifact_retention_approval_request.v2");
    assert_eq!(request["status"], "pending_approval");
    assert_eq!(request["action"], "maintenance.artifact_cleanup");
    assert_eq!(request["requested_action"], "promote");
    assert_eq!(request["risk_level"], "canonical_mutation");
    assert_eq!(request["target"]["relative_path"], "outputs/plot.png");
    assert_eq!(request["target"]["review_status"], "needs_triage");
    assert_eq!(request["authority"]["records_approval_only"], true);
    assert!(request["authority"]["does_not_authorize"]
        .as_array()
        .expect("authority")
        .iter()
        .any(|item| item == "delete files"));
    assert!(request["next_commands"]
        .as_array()
        .expect("next commands")
        .iter()
        .all(|command| !command
            .as_str()
            .unwrap_or_default()
            .contains("retention-apply")));

    let approval = &request["approval"];
    assert_eq!(approval["status"], "pending");
    assert_eq!(approval["source_surface"], "project.retention_request");
    assert_eq!(approval["metadata"]["kind"], "retention_v2");
    assert_eq!(
        approval["metadata"]["proposal"]["target"]["source_kind"],
        "artifact"
    );
    assert_eq!(
        approval["metadata"]["proposal"]["requested_action"],
        "promote"
    );
    assert_eq!(
        approval["metadata"]["proposal"]["target"]["observation"]["relative_path"],
        "outputs/plot.png"
    );
    assert_eq!(
        approval["metadata"]["approval_brief"]["schema"],
        "approval_brief.v1"
    );
    assert_eq!(
        approval["metadata"]["approval_brief"]["source"],
        "project.retention_request"
    );
    assert!(approval["metadata"]["approval_brief"]["scope"]
        .as_str()
        .unwrap_or_default()
        .contains("does not delete, move, archive"));
    assert!(
        approval["metadata"]["approval_brief"]["judgment_route_summary"]
            .as_str()
            .unwrap_or_default()
            .contains("사용자")
    );
    assert!(
        approval["metadata"]["approval_brief"]["evidence_sufficiency"]
            .as_str()
            .unwrap_or_default()
            .contains("Retention review")
    );
    assert_eq!(
        approval["metadata"]["approval_brief"]["default_if_no_reply"],
        "defer"
    );
    assert!(approval["metadata"]["approval_brief"]["options"]
        .as_array()
        .expect("options")
        .iter()
        .any(|option| option["id"] == "defer"
            && option["natural_input_prompt"]
                .as_str()
                .unwrap_or_default()
                .contains("evidence")));

    let approvals_path = profile_dir(temp.path()).join("pending_action_approvals.json");
    let stored: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(stored.as_array().expect("stored approvals").len(), 1);
    assert_eq!(stored[0]["metadata"], approval["metadata"]);

    let request_again_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        request_again_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&request_again_output.stdout),
        String::from_utf8_lossy(&request_again_output.stderr)
    );
    let request_again: Value = serde_json::from_slice(&request_again_output.stdout)?;
    assert_eq!(
        request_again["approval"]["approval_id"],
        request["approval"]["approval_id"]
    );
    let stored_again: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(stored_again.as_array().expect("stored approvals").len(), 1);

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "pending"])
        .output()?;
    assert!(
        pending_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&pending_output.stdout),
        String::from_utf8_lossy(&pending_output.stderr)
    );
    let pending_stdout = String::from_utf8_lossy(&pending_output.stdout);
    assert!(
        pending_stdout.contains("prompt: promote recommendation for artifact retention promote")
    );
    assert!(pending_stdout.contains("Approve the promote retention follow-up?"));
    assert!(pending_stdout.contains("retention target:"));
    assert!(!pending_stdout.contains("outputs/plot.png"));

    let approval_id = approval["approval_id"].as_str().expect("approval id");
    let approve_output = forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?;
    assert!(
        approve_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&approve_output.stdout),
        String::from_utf8_lossy(&approve_output.stderr)
    );
    let approved: Value = serde_json::from_slice(&approve_output.stdout)?;
    assert_eq!(approved["approval_id"], approval_id);
    assert_eq!(approved["status"], "approved");

    let approved_request_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        approved_request_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&approved_request_output.stdout),
        String::from_utf8_lossy(&approved_request_output.stderr)
    );
    let approved_request: Value = serde_json::from_slice(&approved_request_output.stdout)?;
    assert_eq!(approved_request["status"], "already_approved");
    assert!(approved_request["next_commands"]
        .as_array()
        .expect("approved next commands")
        .iter()
        .any(|command| command
            .as_str()
            .unwrap_or_default()
            .contains("retention-apply")));

    let approved_rows: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    let mut tampered_rows = approved_rows.clone();
    tampered_rows[0]["request_id"] = Value::String("tampered-request".to_string());
    fs::write(&approvals_path, serde_json::to_vec_pretty(&tampered_rows)?)?;
    let tampered_apply = forager_command(temp.path())
        .args([
            "project",
            "retention-apply",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(!tampered_apply.status.success());
    assert!(String::from_utf8_lossy(&tampered_apply.stderr)
        .contains("approval identity is not one exact retention v2 authority"));
    fs::write(&approvals_path, serde_json::to_vec_pretty(&approved_rows)?)?;

    let deliverables_before = fs::read_to_string(repo.join("DELIVERABLES.md"))?;
    let apply_output = forager_command(temp.path())
        .args([
            "project",
            "retention-apply",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        apply_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&apply_output.stdout),
        String::from_utf8_lossy(&apply_output.stderr)
    );
    let application: Value = serde_json::from_slice(&apply_output.stdout)?;
    assert_eq!(application["schema"], "artifact_retention_application.v2");
    assert_eq!(application["status"], "applied_plan_recorded");
    assert_eq!(application["approval_id"], approval_id);
    assert_eq!(application["requested_action"], "promote");
    assert_eq!(application["target"]["relative_path"], "outputs/plot.png");
    assert_eq!(application["operation"]["mutation_performed"], false);
    assert_eq!(application["operation"]["plan_only"], true);
    assert!(application["operation"]["blockers"]
        .as_array()
        .expect("blockers")
        .iter()
        .any(|blocker| blocker
            .as_str()
            .unwrap_or_default()
            .contains("separate mutation workflow")));
    assert_eq!(application["approval"]["before_status"], "approved");
    assert_eq!(application["approval"]["after_status"], "superseded");
    assert_eq!(application["approval"]["consumed"], true);
    assert_eq!(application["authority"]["writes_project_state"], false);
    assert_eq!(application["authority"]["writes_files"], false);
    let receipt_path = application["receipt_path"].as_str().expect("receipt path");
    assert!(Path::new(receipt_path).exists());
    let receipt: Value = serde_json::from_str(&fs::read_to_string(receipt_path)?)?;
    assert_eq!(receipt["approval_id"], approval_id);
    assert_eq!(receipt["schema"], "artifact_retention_application.v2");
    assert_eq!(receipt["file_mutation_performed"], false);
    assert_eq!(
        fs::read_to_string(repo.join("DELIVERABLES.md"))?,
        deliverables_before
    );

    let stored_after_apply: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(stored_after_apply[0]["approval_id"], approval_id);
    assert_eq!(stored_after_apply[0]["status"], "superseded");

    let mut crash_rows = stored_after_apply.clone();
    crash_rows[0]["status"] = Value::String("approved".to_string());
    let created_at = chrono::DateTime::parse_from_rfc3339(
        crash_rows[0]["created_at"]
            .as_str()
            .context("approval created_at")?,
    )?;
    crash_rows[0]["expires_at"] = Value::String(
        (created_at + chrono::Duration::microseconds(1))
            .to_rfc3339_opts(chrono::SecondsFormat::AutoSi, true),
    );
    fs::write(&approvals_path, serde_json::to_vec_pretty(&crash_rows)?)?;
    let receipt_reuse_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        receipt_reuse_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&receipt_reuse_output.stdout),
        String::from_utf8_lossy(&receipt_reuse_output.stderr)
    );
    let receipt_reuse: Value = serde_json::from_slice(&receipt_reuse_output.stdout)?;
    assert_eq!(receipt_reuse["approval"]["approval_id"], approval_id);

    let apply_again_output = forager_command(temp.path())
        .args([
            "project",
            "retention-apply",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        apply_again_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&apply_again_output.stdout),
        String::from_utf8_lossy(&apply_again_output.stderr)
    );
    let application_again: Value = serde_json::from_slice(&apply_again_output.stdout)?;
    assert_eq!(application_again["status"], "reconciled_existing");
    assert_eq!(application_again["receipt_path"], receipt_path);

    write_file(
        repo.join("outputs/plot.png").as_path(),
        "changed-png-bytes\n",
    )?;
    let drift_retry_output = forager_command(temp.path())
        .args([
            "project",
            "retention-apply",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        drift_retry_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&drift_retry_output.stdout),
        String::from_utf8_lossy(&drift_retry_output.stderr)
    );
    let drift_retry: Value = serde_json::from_slice(&drift_retry_output.stdout)?;
    assert_eq!(drift_retry["status"], "reconciled_existing");
    assert_eq!(drift_retry["receipt_path"], receipt_path);

    let changed_snapshot_request_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        changed_snapshot_request_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&changed_snapshot_request_output.stdout),
        String::from_utf8_lossy(&changed_snapshot_request_output.stderr)
    );
    let changed_snapshot_request: Value =
        serde_json::from_slice(&changed_snapshot_request_output.stdout)?;
    assert_eq!(changed_snapshot_request["status"], "pending_approval");
    assert_ne!(
        changed_snapshot_request["approval"]["approval_id"],
        approval_id
    );
    Ok(())
}
