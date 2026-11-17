use super::*;

fn operation(path: &Path, source: &str, kind: &'static str) -> CloseoutFileOperation {
    closeout_file_operation(CloseoutFileOperationInput {
        operation: kind,
        path: path.to_str().unwrap(),
        destination: None,
        source: source.into(),
        risk: "medium",
        reason: "retain evidence",
        evidence_refs: vec![source.into()],
        present: true,
        requires_commercial_review: true,
        requires_human_approval: true,
    })
}

#[test]
fn duplicate_task_and_background_log_keeps_both_provenances() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("log.txt");
    fs::write(&path, "original")?;
    let merged = merge_closeout_file_references(vec![
        operation(&path, "task:t", "archive_candidate"),
        operation(&path, "background:b", "archive_candidate"),
    ]);
    assert_eq!(merged.len(), 1);
    assert_eq!(merged[0].source_refs, ["task:t", "background:b"]);
    assert_eq!(merged[0].evidence_refs, ["task:t", "background:b"]);
    assert_eq!(fs::read_to_string(path)?, "original");
    Ok(())
}

#[test]
fn equal_files_and_conflicting_operations_are_not_collapsed() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let a = dir.path().join("a");
    let b = dir.path().join("b");
    fs::write(&a, "equal")?;
    fs::hard_link(&a, &b)?;
    let merged = merge_closeout_file_references(vec![
        operation(&a, "task:a", "archive_candidate"),
        operation(&b, "task:b", "archive_candidate"),
        operation(&a, "task:c", "keep"),
    ]);
    assert_eq!(merged.len(), 3);
    assert!(a.exists() && b.exists());
    Ok(())
}

#[test]
fn unreadable_identity_does_not_guess_file_equivalence() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let missing = dir.path().join("missing");
    assert_eq!(
        merge_closeout_file_references(vec![
            operation(&missing, "task:a", "keep"),
            operation(&missing, "task:b", "keep")
        ])
        .len(),
        2
    );
    Ok(())
}

#[cfg(unix)]
#[test]
fn symlink_alias_stays_explicit_for_review() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let original = dir.path().join("log");
    let alias = dir.path().join("alias");
    fs::write(&original, "evidence")?;
    std::os::unix::fs::symlink(&original, &alias)?;
    assert_eq!(
        merge_closeout_file_references(vec![
            operation(&original, "task:a", "keep"),
            operation(&alias, "task:b", "keep")
        ])
        .len(),
        2
    );
    Ok(())
}
