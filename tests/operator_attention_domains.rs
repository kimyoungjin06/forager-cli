#[test]
fn attention_domains_preserve_unknown_delivery_and_scope_project_review() {
    let status = std::process::Command::new("python3")
        .arg(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/support/test_operator_attention_domains.py"),
        )
        .status()
        .expect("run attention-domain regressions");
    assert!(status.success(), "attention-domain regression failed");
}
