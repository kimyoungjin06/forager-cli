//! Auto-orchestrator provisioning for newly created sessions.

use std::io::Read;
use std::path::{Path, PathBuf};

use serde::Serialize;
use sha2::{Digest, Sha256};

use super::{Config, Instance, Status};

const STARTUP_PREFLIGHT_SCHEMA: &str = "auto_orchestrator_startup_preflight.v1";
const CODEX_CONFIG_MAX_BYTES: u64 = 1024 * 1024;
const PROJECT_IDENTITY_HASH_DOMAIN: &[u8] = b"forager.auto_orchestrator.project.v1\0";
const CODEX_CONFIG_HASH_DOMAIN: &[u8] = b"forager.auto_orchestrator.codex_config.v1\0";
const DEFAULT_CODEX_ORCHESTRATOR_COMMAND: &str = "codex -c check_for_update_on_startup=false";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AutoOrchestratorStartupStateV1 {
    Ready,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AutoOrchestratorStartupReasonV1 {
    ProjectRootUnavailable,
    CodexHomeUnavailable,
    CodexConfigMissing,
    CodexConfigUnsafe,
    CodexConfigInvalid,
    CodexProjectTrustMissing,
    CodexProjectNotTrusted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AutoOrchestratorUpdatePolicyV1 {
    NotApplicable,
    SuppressedForDefaultCommand,
    UserManagedCustomCommand,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AutoOrchestratorStartupPreflightV1 {
    pub schema: String,
    pub state: AutoOrchestratorStartupStateV1,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<AutoOrchestratorStartupReasonV1>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_root_identity_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub codex_config_identity_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exact_project_trusted: Option<bool>,
    pub update_policy: AutoOrchestratorUpdatePolicyV1,
    pub manual_trust_required: bool,
    pub recovery_commands: Vec<String>,
}

impl AutoOrchestratorStartupPreflightV1 {
    fn ready(update_policy: AutoOrchestratorUpdatePolicyV1) -> Self {
        Self {
            schema: STARTUP_PREFLIGHT_SCHEMA.to_string(),
            state: AutoOrchestratorStartupStateV1::Ready,
            reason: None,
            project_root_identity_sha256: None,
            codex_config_identity_sha256: None,
            exact_project_trusted: None,
            update_policy,
            manual_trust_required: false,
            recovery_commands: Vec::new(),
        }
    }

    fn blocked(
        instance: &Instance,
        reason: AutoOrchestratorStartupReasonV1,
        update_policy: AutoOrchestratorUpdatePolicyV1,
    ) -> Self {
        let manual_trust_required = matches!(
            reason,
            AutoOrchestratorStartupReasonV1::CodexProjectTrustMissing
                | AutoOrchestratorStartupReasonV1::CodexProjectNotTrusted
        );
        let recovery_commands = if manual_trust_required {
            vec![
                format!("forager session start {}", instance.id),
                format!("forager session attach {}", instance.id),
            ]
        } else {
            Vec::new()
        };
        Self {
            schema: STARTUP_PREFLIGHT_SCHEMA.to_string(),
            state: AutoOrchestratorStartupStateV1::Blocked,
            reason: Some(reason),
            project_root_identity_sha256: None,
            codex_config_identity_sha256: None,
            exact_project_trusted: manual_trust_required.then_some(false),
            update_policy,
            manual_trust_required,
            recovery_commands,
        }
    }

    pub fn allows_automatic_launch(&self) -> bool {
        self.state == AutoOrchestratorStartupStateV1::Ready
    }
}

struct CodexStartupObservation {
    project_root_identity_sha256: String,
    codex_config_identity_sha256: String,
    exact_project_trusted: bool,
}

#[derive(Debug, Clone)]
pub struct AutoOrchestratorOutcome {
    pub session_id: String,
    pub title: String,
    pub launched: bool,
    pub startup_preflight: AutoOrchestratorStartupPreflightV1,
}

fn parse_bool_env(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

fn normalize_project_path(path: &str) -> String {
    let canonical = PathBuf::from(path)
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(path));
    canonical
        .to_string_lossy()
        .trim_end_matches('/')
        .to_string()
}

pub fn is_auto_orchestrator_role(instance: &Instance) -> bool {
    instance.auto_orchestrator || instance.title.eq_ignore_ascii_case("orchestrator")
}

fn codex_update_policy(instance: &Instance) -> AutoOrchestratorUpdatePolicyV1 {
    if !instance.tool.eq_ignore_ascii_case("codex") {
        AutoOrchestratorUpdatePolicyV1::NotApplicable
    } else if instance.command.trim().is_empty()
        || instance.command == DEFAULT_CODEX_ORCHESTRATOR_COMMAND
    {
        AutoOrchestratorUpdatePolicyV1::SuppressedForDefaultCommand
    } else {
        AutoOrchestratorUpdatePolicyV1::UserManagedCustomCommand
    }
}

/// Apply Forager's bounded policy only to its own default Codex command.
/// User-configured commands remain entirely user managed.
pub fn prepare_launch(instance: &mut Instance) {
    if is_auto_orchestrator_role(instance)
        && instance.tool.eq_ignore_ascii_case("codex")
        && instance.command.trim().is_empty()
    {
        instance.command = DEFAULT_CODEX_ORCHESTRATOR_COMMAND.to_string();
    }
}

fn hash_parts(domain: &[u8], parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(domain);
    for part in parts {
        hasher.update((part.len() as u64).to_be_bytes());
        hasher.update(part.as_bytes());
    }
    format!("{:x}", hasher.finalize())
}

fn codex_home_path() -> Option<PathBuf> {
    std::env::var_os("CODEX_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|home| home.join(".codex")))
}

#[cfg(unix)]
fn codex_config_metadata_is_safe(metadata: &std::fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;

    metadata.is_file()
        && metadata.nlink() == 1
        && metadata.uid() == nix::unistd::Uid::current().as_raw()
        && metadata.mode() & 0o022 == 0
}

#[cfg(not(unix))]
fn codex_config_metadata_is_safe(metadata: &std::fs::Metadata) -> bool {
    metadata.is_file()
}

fn observe_codex_startup(
    instance: &Instance,
    codex_home: &Path,
) -> Result<CodexStartupObservation, AutoOrchestratorStartupReasonV1> {
    let canonical_project = PathBuf::from(&instance.project_path)
        .canonicalize()
        .map_err(|_| AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?;
    let project_root = crate::process::open_bounded_root(&canonical_project)
        .map_err(|_| AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?;
    let project_identity = crate::process::filesystem_file_identity(
        &project_root
            .metadata()
            .map_err(|_| AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?,
    )
    .ok_or(AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?;

    if !codex_home.is_absolute() {
        return Err(AutoOrchestratorStartupReasonV1::CodexHomeUnavailable);
    }
    let codex_root = crate::process::open_bounded_root(codex_home)
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexHomeUnavailable)?;
    let codex_root_identity = crate::process::filesystem_file_identity(
        &codex_root
            .metadata()
            .map_err(|_| AutoOrchestratorStartupReasonV1::CodexHomeUnavailable)?,
    )
    .ok_or(AutoOrchestratorStartupReasonV1::CodexHomeUnavailable)?;

    let mut config_file =
        match crate::process::open_bounded_regular_file(&codex_root, Path::new("config.toml")) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(AutoOrchestratorStartupReasonV1::CodexConfigMissing);
            }
            Err(_) => return Err(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe),
        };
    let before = config_file
        .metadata()
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    if !codex_config_metadata_is_safe(&before) || before.len() > CODEX_CONFIG_MAX_BYTES {
        return Err(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe);
    }
    let config_file_identity = crate::process::filesystem_file_identity(&before)
        .ok_or(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    let mut bytes = Vec::new();
    config_file
        .by_ref()
        .take(CODEX_CONFIG_MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    if bytes.len() as u64 > CODEX_CONFIG_MAX_BYTES {
        return Err(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe);
    }
    let after = config_file
        .metadata()
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    if !codex_config_metadata_is_safe(&after)
        || crate::process::FilesystemObjectSnapshot::from_metadata(&before)
            != crate::process::FilesystemObjectSnapshot::from_metadata(&after)
    {
        return Err(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe);
    }

    let source = std::str::from_utf8(&bytes)
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigInvalid)?;
    let config = toml::from_str::<toml::Value>(source)
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigInvalid)?;
    let canonical_project_text = canonical_project.to_string_lossy();
    let Some(project_config) = config
        .get("projects")
        .and_then(toml::Value::as_table)
        .and_then(|projects| projects.get(canonical_project_text.as_ref()))
    else {
        return Err(AutoOrchestratorStartupReasonV1::CodexProjectTrustMissing);
    };
    let exact_project_trusted = project_config
        .get("trust_level")
        .and_then(toml::Value::as_str)
        == Some("trusted");
    if !exact_project_trusted {
        return Err(AutoOrchestratorStartupReasonV1::CodexProjectNotTrusted);
    }

    let final_config_metadata = config_file
        .metadata()
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    let reopened_config =
        crate::process::open_bounded_regular_file(&codex_root, Path::new("config.toml"))
            .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    let reopened_config_metadata = reopened_config
        .metadata()
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    let reopened_codex_root = crate::process::open_bounded_root(codex_home)
        .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?;
    let reopened_project_root = crate::process::open_bounded_root(&canonical_project)
        .map_err(|_| AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?;
    if !codex_config_metadata_is_safe(&final_config_metadata)
        || crate::process::FilesystemObjectSnapshot::from_metadata(&before)
            != crate::process::FilesystemObjectSnapshot::from_metadata(&final_config_metadata)
        || !codex_config_metadata_is_safe(&reopened_config_metadata)
        || crate::process::filesystem_file_identity(&reopened_config_metadata)
            != Some(config_file_identity)
        || crate::process::filesystem_file_identity(
            &reopened_codex_root
                .metadata()
                .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?,
        ) != Some(codex_root_identity)
        || crate::process::filesystem_file_identity(
            &codex_root
                .metadata()
                .map_err(|_| AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)?,
        ) != Some(codex_root_identity)
        || crate::process::filesystem_file_identity(
            &reopened_project_root
                .metadata()
                .map_err(|_| AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?,
        ) != Some(project_identity)
        || crate::process::filesystem_file_identity(
            &project_root
                .metadata()
                .map_err(|_| AutoOrchestratorStartupReasonV1::ProjectRootUnavailable)?,
        ) != Some(project_identity)
    {
        return Err(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe);
    }

    let project_identity_text = format!("{}:{}", project_identity.0, project_identity.1);
    let codex_root_identity_text = format!("{}:{}", codex_root_identity.0, codex_root_identity.1);
    let config_file_identity_text =
        format!("{}:{}", config_file_identity.0, config_file_identity.1);
    let config_content_sha256 = format!("{:x}", Sha256::digest(&bytes));
    Ok(CodexStartupObservation {
        project_root_identity_sha256: hash_parts(
            PROJECT_IDENTITY_HASH_DOMAIN,
            &[canonical_project_text.as_ref(), &project_identity_text],
        ),
        codex_config_identity_sha256: hash_parts(
            CODEX_CONFIG_HASH_DOMAIN,
            &[
                &codex_root_identity_text,
                &config_file_identity_text,
                &config_content_sha256,
            ],
        ),
        exact_project_trusted,
    })
}

fn startup_preflight_with_codex_home(
    instance: &Instance,
    codex_home: Option<&Path>,
) -> AutoOrchestratorStartupPreflightV1 {
    let update_policy = codex_update_policy(instance);
    if !is_auto_orchestrator_role(instance) || !instance.tool.eq_ignore_ascii_case("codex") {
        return AutoOrchestratorStartupPreflightV1::ready(update_policy);
    }
    let Some(codex_home) = codex_home else {
        return AutoOrchestratorStartupPreflightV1::blocked(
            instance,
            AutoOrchestratorStartupReasonV1::CodexHomeUnavailable,
            update_policy,
        );
    };
    match observe_codex_startup(instance, codex_home) {
        Ok(observation) => AutoOrchestratorStartupPreflightV1 {
            schema: STARTUP_PREFLIGHT_SCHEMA.to_string(),
            state: AutoOrchestratorStartupStateV1::Ready,
            reason: None,
            project_root_identity_sha256: Some(observation.project_root_identity_sha256),
            codex_config_identity_sha256: Some(observation.codex_config_identity_sha256),
            exact_project_trusted: Some(observation.exact_project_trusted),
            update_policy,
            manual_trust_required: false,
            recovery_commands: Vec::new(),
        },
        Err(reason) => AutoOrchestratorStartupPreflightV1::blocked(instance, reason, update_policy),
    }
}

pub fn startup_preflight(instance: &Instance) -> AutoOrchestratorStartupPreflightV1 {
    startup_preflight_with_codex_home(instance, codex_home_path().as_deref())
}

fn auto_orchestrator_enabled(config: &Config) -> bool {
    if let Ok(value) = std::env::var("AOE_AUTO_ORCHESTRATOR") {
        if let Some(parsed) = parse_bool_env(&value) {
            return parsed;
        }
    }
    config.session.auto_orchestrator
}

fn orchestrator_title(config: &Config) -> String {
    if let Ok(from_env) = std::env::var("AOE_ORCHESTRATOR_TITLE") {
        let trimmed = from_env.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }

    config
        .session
        .orchestrator_title
        .as_ref()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "Orchestrator".to_string())
}

fn build_orchestrator_instance(
    trigger_instance: &Instance,
    config: &Config,
    title: &str,
) -> Instance {
    let mut orchestrator = Instance::new(title, &trigger_instance.project_path);
    orchestrator.group_path = trigger_instance.group_path.clone();
    orchestrator.tool = config
        .session
        .default_tool
        .as_ref()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            if trigger_instance.tool.trim().is_empty() {
                "codex".to_string()
            } else {
                trigger_instance.tool.clone()
            }
        });
    orchestrator.yolo_mode = trigger_instance.yolo_mode;
    orchestrator.auto_orchestrator = true;

    if let Some(command) = config
        .session
        .orchestrator_command
        .as_ref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        orchestrator.command = command.to_string();
    }
    orchestrator
}

/// Ensure an orchestrator session exists for the same project path as `trigger_instance`.
///
/// This is best-effort and never fails hard: any launch error is logged and we still
/// keep the orchestrator session in storage so users can start it later manually.
pub fn maybe_create_for_instance(
    instances: &mut Vec<Instance>,
    trigger_instance: &Instance,
    config: &Config,
) -> Option<AutoOrchestratorOutcome> {
    let codex_home = codex_home_path();
    maybe_create_for_instance_with_codex_home(
        instances,
        trigger_instance,
        config,
        codex_home.as_deref(),
    )
}

fn maybe_create_for_instance_with_codex_home(
    instances: &mut Vec<Instance>,
    trigger_instance: &Instance,
    config: &Config,
    codex_home: Option<&Path>,
) -> Option<AutoOrchestratorOutcome> {
    if !auto_orchestrator_enabled(config) {
        return None;
    }

    let title = orchestrator_title(config);
    if trigger_instance.title.eq_ignore_ascii_case(&title) {
        return None;
    }

    let trigger_path = normalize_project_path(&trigger_instance.project_path);
    let already_exists = instances.iter().any(|inst| {
        inst.title.eq_ignore_ascii_case(&title)
            && normalize_project_path(&inst.project_path) == trigger_path
    });

    if already_exists {
        return None;
    }

    let mut orchestrator = build_orchestrator_instance(trigger_instance, config, &title);
    prepare_launch(&mut orchestrator);
    let startup_preflight = startup_preflight_with_codex_home(&orchestrator, codex_home);

    let session_id = orchestrator.id.clone();
    let mut launched = false;
    if !startup_preflight.allows_automatic_launch() {
        orchestrator.status = Status::Stopped;
        tracing::warn!(
            "Auto-orchestrator session '{}' was retained without launch: startup preflight {:?}",
            title,
            startup_preflight.reason
        );
    } else if let Err(e) = orchestrator.start_with_size(None, config) {
        tracing::warn!(
            "Failed to auto-start orchestrator session '{}' for '{}': {}",
            title,
            trigger_instance.project_path,
            e
        );
    } else if let Ok(session) = orchestrator.tmux_session() {
        launched = session.exists();
    }

    instances.push(orchestrator);

    Some(AutoOrchestratorOutcome {
        session_id,
        title,
        launched,
        startup_preflight,
    })
}

/// Ensure orchestrator sessions exist for all existing project sessions.
/// Returns the number of newly created orchestrator sessions.
pub fn ensure_for_existing_sessions(instances: &mut Vec<Instance>, config: &Config) -> usize {
    let snapshot = instances.clone();
    let mut created = 0usize;

    for inst in &snapshot {
        if maybe_create_for_instance(instances, inst, config).is_some() {
            created += 1;
        }
    }

    created
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_bool_env_handles_common_values() {
        assert_eq!(parse_bool_env("1"), Some(true));
        assert_eq!(parse_bool_env("true"), Some(true));
        assert_eq!(parse_bool_env("0"), Some(false));
        assert_eq!(parse_bool_env("off"), Some(false));
        assert_eq!(parse_bool_env("maybe"), None);
    }

    #[test]
    fn orchestrator_title_comes_from_the_supplied_effective_config() {
        let mut config = Config::default();
        config.session.orchestrator_title = Some("Active Profile Orchestrator".to_string());

        assert_eq!(orchestrator_title(&config), "Active Profile Orchestrator");
    }

    #[test]
    fn built_orchestrator_preserves_yolo_and_records_provenance() {
        let config = Config::default();
        let mut trigger = Instance::new("worker", "/tmp/project");
        trigger.tool = "codex".to_string();
        trigger.yolo_mode = true;

        let orchestrator = build_orchestrator_instance(&trigger, &config, "Orchestrator");

        assert!(orchestrator.yolo_mode);
        assert!(orchestrator.auto_orchestrator);
        assert_eq!(orchestrator.project_path, trigger.project_path);
    }

    fn write_codex_config(codex_home: &Path, project: &Path, trust_level: &str) {
        std::fs::create_dir_all(codex_home).expect("create codex home");
        let project = project.to_string_lossy().replace('\\', "\\\\");
        let rendered = format!("[projects.\"{project}\"]\ntrust_level = \"{trust_level}\"\n");
        toml::from_str::<toml::Value>(&rendered).expect("test codex config must be valid TOML");
        std::fs::write(codex_home.join("config.toml"), rendered).expect("write codex config");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(
                codex_home.join("config.toml"),
                std::fs::Permissions::from_mode(0o600),
            )
            .expect("secure codex config");
        }
    }

    fn test_codex_orchestrator(project: &Path) -> Instance {
        let mut instance = Instance::new("Orchestrator", &project.to_string_lossy());
        instance.tool = "codex".to_string();
        instance.auto_orchestrator = true;
        prepare_launch(&mut instance);
        instance
    }

    #[test]
    fn default_codex_launch_suppresses_only_startup_update_check() {
        let project = tempfile::tempdir().expect("project");
        let mut instance = test_codex_orchestrator(project.path());

        assert_eq!(instance.command, DEFAULT_CODEX_ORCHESTRATOR_COMMAND);
        assert!(!instance.command.contains("bypass-hook-trust"));

        instance.command = "codex --model gpt-5".to_string();
        prepare_launch(&mut instance);
        assert_eq!(instance.command, "codex --model gpt-5");
    }

    #[test]
    fn exact_trusted_codex_project_is_ready() {
        let project = tempfile::tempdir().expect("project");
        let codex_home = tempfile::tempdir().expect("codex home");
        write_codex_config(codex_home.path(), project.path(), "trusted");
        let instance = test_codex_orchestrator(project.path());

        let preflight = startup_preflight_with_codex_home(&instance, Some(codex_home.path()));

        assert_eq!(preflight.state, AutoOrchestratorStartupStateV1::Ready);
        assert_eq!(preflight.exact_project_trusted, Some(true));
        assert_eq!(
            preflight.update_policy,
            AutoOrchestratorUpdatePolicyV1::SuppressedForDefaultCommand
        );
        assert!(preflight.project_root_identity_sha256.is_some());
        assert!(preflight.codex_config_identity_sha256.is_some());
    }

    #[test]
    fn parent_trust_does_not_authorize_an_exact_child_project() {
        let workspace = tempfile::tempdir().expect("workspace");
        let project = workspace.path().join("child");
        std::fs::create_dir(&project).expect("project");
        let codex_home = tempfile::tempdir().expect("codex home");
        write_codex_config(codex_home.path(), workspace.path(), "trusted");
        let instance = test_codex_orchestrator(&project);

        let preflight = startup_preflight_with_codex_home(&instance, Some(codex_home.path()));

        assert_eq!(
            preflight.reason,
            Some(AutoOrchestratorStartupReasonV1::CodexProjectTrustMissing)
        );
        assert!(preflight.manual_trust_required);
        assert_eq!(preflight.recovery_commands.len(), 2);
    }

    #[test]
    fn blocked_preflight_retains_a_stopped_orchestrator_without_tmux() {
        let workspace = tempfile::tempdir().expect("workspace");
        let project = workspace.path().join("child");
        std::fs::create_dir(&project).expect("project");
        let codex_home = tempfile::tempdir().expect("codex home");
        write_codex_config(codex_home.path(), workspace.path(), "trusted");
        let trigger = Instance::new("worker", &project.to_string_lossy());
        let mut config = Config::default();
        config.session.auto_orchestrator = true;
        config.session.default_tool = Some("codex".to_string());
        let mut instances = vec![trigger.clone()];

        let outcome = maybe_create_for_instance_with_codex_home(
            &mut instances,
            &trigger,
            &config,
            Some(codex_home.path()),
        )
        .expect("orchestrator outcome");

        assert!(!outcome.launched);
        assert_eq!(
            outcome.startup_preflight.reason,
            Some(AutoOrchestratorStartupReasonV1::CodexProjectTrustMissing)
        );
        let orchestrator = instances.last().expect("stored orchestrator");
        assert_eq!(orchestrator.status, Status::Stopped);
        assert!(!orchestrator
            .tmux_session()
            .expect("tmux session handle")
            .exists());
    }

    #[cfg(unix)]
    #[test]
    fn writable_or_hardlinked_codex_config_is_rejected() {
        use std::os::unix::fs::PermissionsExt;

        let project = tempfile::tempdir().expect("project");
        let codex_home = tempfile::tempdir().expect("codex home");
        write_codex_config(codex_home.path(), project.path(), "trusted");
        let config_path = codex_home.path().join("config.toml");
        std::fs::set_permissions(&config_path, std::fs::Permissions::from_mode(0o620))
            .expect("make config group writable");
        let instance = test_codex_orchestrator(project.path());

        let writable = startup_preflight_with_codex_home(&instance, Some(codex_home.path()));
        assert_eq!(
            writable.reason,
            Some(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)
        );

        std::fs::set_permissions(&config_path, std::fs::Permissions::from_mode(0o600))
            .expect("restore safe permissions");
        std::fs::hard_link(&config_path, codex_home.path().join("config.alias"))
            .expect("hardlink config");
        let hardlinked = startup_preflight_with_codex_home(&instance, Some(codex_home.path()));
        assert_eq!(
            hardlinked.reason,
            Some(AutoOrchestratorStartupReasonV1::CodexConfigUnsafe)
        );
    }
}
