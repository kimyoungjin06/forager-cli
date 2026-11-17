use super::*;
use std::collections::BTreeMap;

fn bytes_in(dir: &Path) -> Result<BTreeMap<PathBuf, Vec<u8>>> {
    fs::read_dir(dir)?
        .map(|entry| {
            let path = entry?.path();
            Ok((path.clone(), fs::read(path)?))
        })
        .collect()
}

fn review(home: &Path, dir: &Path) -> Result<std::process::Output> {
    Ok(forager_command(home)
        .args(["offdesk", "closeout-review", "--artifact-dir"])
        .arg(dir)
        .args(["--verdict", "approved", "--json"])
        .output()?)
}

#[test]
fn copied_plan_destinations_refuse_review_decision_and_retirement_before_writes() -> Result<()> {
    for kind in ["outside", "parent", "wrong-slot", "wrong-type"] {
        let temp = tempdir()?;
        let home = temp.path().join("home");
        let selected = temp.path().join("selected");
        write_closeout_record_presentation_fixture(&selected, "scope-fixture", "runtime", true)?;
        let initial = review(&home, &selected)?;
        assert!(
            initial.status.success(),
            "{}",
            String::from_utf8_lossy(&initial.stderr)
        );
        let sentinel = temp.path().join("outside.md");
        fs::write(&sentinel, b"external sentinel")?;
        let plan_path = selected.join("closeout_plan.json");
        let mut plan: Value = serde_json::from_slice(&fs::read(&plan_path)?)?;
        plan["artifacts"]["return_package_markdown"] = match kind {
            "outside" => json!(sentinel),
            "parent" => json!(selected.join("../outside.md")),
            "wrong-slot" => json!(selected.join("other.md")),
            _ => json!([sentinel]),
        };
        fs::write(&plan_path, serde_json::to_vec(&plan)?)?;
        let before = bytes_in(&selected)?;
        for operation in ["closeout-review", "closeout-decision", "closeout-retire"] {
            let mut command = forager_command(&home);
            command
                .args(["offdesk", operation, "--artifact-dir"])
                .arg(&selected);
            match operation {
                "closeout-review" => {
                    command.args(["--verdict", "approved"]);
                }
                "closeout-decision" => {
                    command.args([
                        "--kind",
                        "archive_review",
                        "--decision",
                        "preserve-in-place",
                        "--reason",
                        "test",
                    ]);
                }
                _ => {
                    command.args(["--reason", "test"]);
                }
            }
            let out = command.arg("--json").output()?;
            assert!(!out.status.success(), "{kind}/{operation}");
            assert_eq!(before, bytes_in(&selected)?, "{kind}/{operation}");
            assert_eq!(fs::read(&sentinel)?, b"external sentinel");
        }
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn unsafe_return_files_or_symlinked_roots_refuse_without_touching_sentinel() -> Result<()> {
    use std::os::unix::fs::symlink;
    for kind in [
        "symlink",
        "hardlink",
        "oversized",
        "root-symlink",
        "plan-symlink",
    ] {
        let temp = tempdir()?;
        let home = temp.path().join("home");
        let selected = temp.path().join("selected");
        write_closeout_record_presentation_fixture(&selected, "unsafe-fixture", "runtime", false)?;
        let sentinel = temp.path().join("sentinel.md");
        fs::write(&sentinel, b"sentinel")?;
        let path = selected.join("RETURN_PACKAGE.md");
        let mut target = selected.clone();
        match kind {
            "symlink" => {
                fs::remove_file(&path)?;
                symlink(&sentinel, &path)?;
            }
            "hardlink" => {
                fs::remove_file(&path)?;
                fs::hard_link(&sentinel, &path)?;
            }
            "oversized" => {
                fs::write(&path, vec![b'x'; 1_048_577])?;
            }
            "root-symlink" => {
                target = temp.path().join("alias");
                symlink(&selected, &target)?;
            }
            _ => {
                let saved = temp.path().join("saved-plan.json");
                fs::rename(selected.join("closeout_plan.json"), &saved)?;
                symlink(saved, selected.join("closeout_plan.json"))?;
            }
        }
        let before = bytes_in(&selected)?;
        let out = review(&home, &target)?;
        assert!(!out.status.success(), "{kind}");
        assert_eq!(before, bytes_in(&selected)?, "{kind}");
        assert_eq!(fs::read(&sentinel)?, b"sentinel");
    }
    Ok(())
}

#[test]
fn explicit_external_artifact_directory_and_relative_selection_remain_supported() -> Result<()> {
    let temp = tempdir()?;
    let home = temp.path().join("home");
    let selected = temp.path().join("selected");
    write_closeout_record_presentation_fixture(&selected, "valid-fixture", "runtime", true)?;
    let out = forager_command(&home)
        .current_dir(temp.path())
        .args([
            "offdesk",
            "closeout-review",
            "--artifact-dir",
            "selected",
            "--verdict",
            "approved",
            "--json",
        ])
        .output()?;
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let record: Value = serde_json::from_slice(&out.stdout)?;
    assert_eq!(
        record["artifacts"]["return_package_markdown"],
        json!(selected.join("RETURN_PACKAGE.md"))
    );
    let before = fs::read_to_string(selected.join("RETURN_PACKAGE.md"))?;
    let out = forager_command(&home)
        .current_dir(temp.path())
        .args([
            "offdesk",
            "closeout-decision",
            "--artifact-dir",
            "selected",
            "--kind",
            "archive_review",
            "--decision",
            "preserve-in-place",
            "--reason",
            "fixture preserve",
            "--json",
        ])
        .output()?;
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let after = fs::read_to_string(selected.join("RETURN_PACKAGE.md"))?;
    assert_ne!(before, after);
    assert_eq!(
        after
            .matches("<!-- forager:closeout-receipt:start -->")
            .count(),
        1
    );
    assert!(after.starts_with("# Ondesk Return Package\n"));
    Ok(())
}
