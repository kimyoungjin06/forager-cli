//! Compatibility tests for inherited sandbox metadata.
//!
//! Forager no longer creates new Docker sandbox sessions. These tests only
//! verify that existing saved session records and legacy names remain readable
//! during the transition.

use forager::containers::DockerContainer;
use forager::session::{Instance, SandboxInfo, Storage};

#[test]
fn legacy_sandbox_info_serializes() {
    let sandbox_info = SandboxInfo {
        enabled: true,
        container_id: Some("abc123".to_string()),
        image: "ubuntu:latest".to_string(),
        container_name: "forager-sandbox-test1234".to_string(),
        created_at: Some(chrono::Utc::now()),
        extra_env_keys: Some(vec!["MY_VAR".to_string()]),
        extra_env_values: None,
    };

    let json = serde_json::to_string(&sandbox_info).unwrap();
    let deserialized: SandboxInfo = serde_json::from_str(&json).unwrap();

    assert!(deserialized.enabled);
    assert_eq!(deserialized.container_id, Some("abc123".to_string()));
    assert_eq!(deserialized.container_name, "forager-sandbox-test1234");
    assert_eq!(deserialized.image, "ubuntu:latest");
    assert_eq!(
        deserialized.extra_env_keys,
        Some(vec!["MY_VAR".to_string()])
    );
}

#[test]
fn legacy_sandbox_info_still_marks_instance_sandboxed() {
    let mut inst = Instance::new("test", "/tmp/test");
    assert!(!inst.is_sandboxed());

    inst.sandbox_info = Some(SandboxInfo {
        enabled: true,
        container_id: None,
        image: "test-image".to_string(),
        container_name: "forager-sandbox-test".to_string(),
        created_at: None,
        extra_env_keys: None,
        extra_env_values: None,
    });
    assert!(inst.is_sandboxed());

    inst.sandbox_info = Some(SandboxInfo {
        enabled: false,
        container_id: None,
        image: "test-image".to_string(),
        container_name: "forager-sandbox-test".to_string(),
        created_at: None,
        extra_env_keys: None,
        extra_env_values: None,
    });
    assert!(!inst.is_sandboxed());
}

#[test]
fn legacy_sandbox_info_persists_across_save_load() {
    let temp = tempfile::TempDir::new().unwrap();
    std::env::set_var("HOME", temp.path());

    let storage = Storage::new("sandbox_test").unwrap();

    let mut inst = Instance::new("sandbox-session", "/tmp/project");
    inst.sandbox_info = Some(SandboxInfo {
        enabled: true,
        container_id: Some("container123".to_string()),
        image: "custom:image".to_string(),
        container_name: "forager-sandbox-abcd1234".to_string(),
        created_at: Some(chrono::Utc::now()),
        extra_env_keys: Some(vec!["API_KEY".to_string(), "SECRET".to_string()]),
        extra_env_values: None,
    });

    storage.save(&[inst.clone()]).unwrap();

    let loaded = storage.load().unwrap();
    assert_eq!(loaded.len(), 1);

    let loaded_inst = &loaded[0];
    assert!(loaded_inst.sandbox_info.is_some());

    let sandbox = loaded_inst.sandbox_info.as_ref().unwrap();
    assert!(sandbox.enabled);
    assert_eq!(sandbox.container_id, Some("container123".to_string()));
    assert_eq!(sandbox.image, "custom:image");
    assert_eq!(sandbox.container_name, "forager-sandbox-abcd1234");
}

#[test]
fn legacy_sandbox_container_names_remain_stable() {
    let name1 = DockerContainer::generate_name("abcd1234");
    assert_eq!(name1, "forager-sandbox-abcd1234");

    let name2 = DockerContainer::generate_name("abcdefghijklmnop");
    assert_eq!(name2, "forager-sandbox-abcdefgh");

    let name3 = DockerContainer::generate_name("abc");
    assert_eq!(name3, "forager-sandbox-abc");

    let legacy = DockerContainer::generate_legacy_name("abcdefghijklmnop");
    assert_eq!(legacy, "aoe-sandbox-abcdefgh");
}
