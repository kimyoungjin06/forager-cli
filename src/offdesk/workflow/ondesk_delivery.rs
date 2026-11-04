//! Explicit local return input with durable no-retry attempts and bound read claims.
use super::{
    ondesk_plan_store as files,
    ondesk_return::{self, MorningReturnInput},
    ondesk_review::ShiftReviewReference,
};
use crate::{
    process,
    session::{normalize_profile_name, profile_dir_path, Instance, RemoteSessionIdentityV1},
    tmux::status_detection::wiki_context_prompt_ready,
};
use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Duration, Utc};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

const LEDGER: &str = "ondesk_return_deliveries.v1.json";
const LOCK: &str = ".ondesk_return_deliveries.v1.lock";
const MAX_LEDGER: usize = 2_097_152;
const MAX_TICKETS: usize = 256;
const MAX_PACKAGE: usize = 160_000;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReturnTicket {
    pub schema: String,
    pub request_id: String,
    pub profile: String,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub nonce: String,
    pub package: PathBuf,
    pub package_sha256: String,
    pub project_key: String,
    pub workdir: PathBuf,
    pub owning_plan: PathBuf,
    pub current_plan_sha256: String,
    pub shift_review: ShiftReviewReference,
    pub context_sha256: String,
    pub git_status_sha256: Option<String>,
    pub session_id: String,
    pub session_identity: RemoteSessionIdentityV1,
    pub pane_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub read_policy_binding: Option<crate::offdesk::read_policy_tmux::TmuxReadPolicyBinding>,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum DeliveryState {
    SendIntent,
    InputQueued,
    DeliveryUnconfirmed,
    AgentReadClaimObserved,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    ticket: ReturnTicket,
    state: DeliveryState,
    attempted_at: DateTime<Utc>,
    observed_at: Option<DateTime<Utc>>,
    acknowledgement_pane_sha256: Option<String>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Ledger {
    schema: String,
    entries: Vec<Entry>,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub fn ticket_sha256(ticket: &ReturnTicket) -> Result<String> {
    Ok(digest(&serde_json::to_vec(ticket)?))
}

pub fn read_ticket(path: &Path) -> Result<ReturnTicket> {
    let ticket: ReturnTicket = serde_json::from_slice(&read_absolute(path, 32_768)?)?;
    validate_ticket(&ticket)?;
    Ok(ticket)
}

fn read_absolute(path: &Path, limit: usize) -> Result<Vec<u8>> {
    ensure!(path.is_absolute(), "absolute return artifact path required");
    let parent = path.parent().context("return artifact parent required")?;
    let root = process::open_bounded_root(parent)?;
    let bytes = files::read(
        &root,
        Path::new(path.file_name().context("artifact filename required")?),
        limit,
    )?;
    files::active_root(parent, &root)?;
    Ok(bytes)
}

fn session(profile: &str, id: &str) -> Result<Instance> {
    let rows: Vec<Instance> = serde_json::from_slice(&read_absolute(
        &profile_dir_path(profile)?.join("sessions.json"),
        1_048_576,
    )?)?;
    let mut matches = rows.into_iter().filter(|s| s.id == id);
    let instance = matches.next().context("exact return session not found")?;
    ensure!(
        matches.next().is_none(),
        "duplicate return session identity"
    );
    ensure!(
        matches!(instance.tool.as_str(), "claude" | "codex"),
        "no safe return input adapter for this tool"
    );
    Ok(instance)
}

fn package_context(
    profile: &str,
    package: &Path,
) -> Result<(Vec<u8>, Value, ondesk_return::MorningReturnContext)> {
    let bytes = read_absolute(package, MAX_PACKAGE)?;
    let text = std::str::from_utf8(&bytes)?;
    let body = text
        .split_once("\n```json\n")
        .context("morning package JSON fence required")?
        .1;
    let body = body
        .strip_suffix("\n```\n\n")
        .context("invalid morning package ending")?;
    let saved: Value = serde_json::from_str(body)?;
    ensure!(
        saved["schema"] == "ondesk_morning_return.v1"
            || saved["schema"] == "ondesk_morning_return.v2",
        "not a morning return package"
    );
    let field = |name: &str| {
        saved[name]
            .as_str()
            .with_context(|| format!("morning package {name} required"))
    };
    let reference = serde_json::from_value(saved["shift_review"].clone())?;
    let mapping = if saved["schema"] == "ondesk_morning_return.v2" {
        Some(serde_json::from_value::<
            super::ondesk_return_binding::ArtifactPin,
        >(
            saved["canonical_return_binding"]["reference"].clone()
        )?)
    } else {
        None
    };
    let current = ondesk_return::build_with_binding(
        MorningReturnInput {
            profile,
            project_key: field("project_key")?,
            workdir: Path::new(field("workdir")?),
            owning_plan: Path::new(field("owning_plan")?),
            reference,
            resume_session: saved["session_id"].as_str(),
            expected_branch: saved["current_git"]["branch"].as_str(),
        },
        mapping.as_ref(),
    )?;
    ensure!(
        current.pending_decisions.is_empty(),
        "morning agreement or reconciliation remains pending"
    );
    let mut fresh = serde_json::to_value(&current)?;
    sanitize(&mut fresh);
    ensure!(
        semantic_context(&fresh) == semantic_context(&saved),
        "morning package context is stale or altered"
    );
    Ok((bytes, saved, current))
}

fn sanitize(value: &mut Value) {
    match value {
        Value::String(s) => *s = crate::offdesk::operator_safe_text(s).trim().into(),
        Value::Array(a) => a.iter_mut().for_each(sanitize),
        Value::Object(o) => o.values_mut().for_each(sanitize),
        _ => {}
    }
}

fn semantic_context(value: &Value) -> Value {
    let mut value = value.clone();
    // Export itself can add a file to Git status. Bind the fresh preview's
    // status separately, while retaining branch and HEAD in the package check.
    value["current_git"] =
        json!({"head":value["current_git"]["head"], "branch":value["current_git"]["branch"]});
    value
}

pub fn preview(profile: &str, package: &Path, session_id: &str) -> Result<Value> {
    preview_with_read_policy(profile, package, session_id, None)
}

pub fn preview_with_read_policy(
    profile: &str,
    package: &Path,
    session_id: &str,
    receipt: Option<&Path>,
) -> Result<Value> {
    let profile = normalize_profile_name(profile)?;
    let (bytes, saved, context) = package_context(&profile, package)?;
    if let Some(id) = context.session_id.as_deref() {
        ensure!(
            id == session_id,
            "existing-session package targets another session"
        );
    }
    let instance = session(&profile, session_id)?;
    ensure!(
        Path::new(&instance.project_path) == context.workdir,
        "return session belongs to another workdir"
    );
    let read_policy_binding = receipt
        .map(crate::offdesk::read_policy_tmux::load_binding)
        .transpose()?;
    if let Some(binding) = &read_policy_binding {
        ensure!(
            package.starts_with(&context.workdir)
                && context.owning_plan.starts_with(&context.workdir),
            "restricted return artifacts must be inside the approved workdir"
        );
        crate::offdesk::read_policy_tmux::validate_live(
            binding,
            &instance.tmux_session()?,
            &context.workdir,
        )?;
    }
    let identity = RemoteSessionIdentityV1::observe(&instance, Some(&context.project_key))?;
    let pane = identity.capture_notification_tail(&instance, Some(&context.project_key))?;
    ensure!(
        wiki_context_prompt_ready(&pane, &instance.tool),
        "return session has no safe empty task prompt"
    );
    let now = Utc::now();
    let ticket = ReturnTicket {
        schema: "ondesk_return_ticket.v1".into(),
        request_id: Uuid::new_v4().simple().to_string(),
        profile,
        created_at: now,
        expires_at: now + Duration::minutes(5),
        nonce: Uuid::new_v4().simple().to_string(),
        package: package.into(),
        package_sha256: digest(&bytes),
        project_key: context.project_key,
        workdir: context.workdir,
        owning_plan: context.owning_plan,
        current_plan_sha256: context.current_plan_sha256,
        shift_review: context.shift_review,
        context_sha256: digest(&serde_json::to_vec(&semantic_context(&saved))?),
        git_status_sha256: context.current_git.status_sha256,
        session_id: session_id.into(),
        session_identity: identity,
        pane_sha256: digest(pane.as_bytes()),
        read_policy_binding,
    };
    validate_ticket(&ticket)?;
    revalidate(&ticket, true)?;
    Ok(
        json!({"schema":"ondesk_return_preview.v1", "preview_sha256":ticket_sha256(&ticket)?,
        "message_preview":message(&ticket)?, "ticket":ticket, "input_sent":false,
        "does_not_authorize":["task_execution","plan_write","user_acceptance"]}),
    )
}

fn validate_ticket(ticket: &ReturnTicket) -> Result<()> {
    ensure!(
        ticket.schema == "ondesk_return_ticket.v1",
        "invalid return ticket schema"
    );
    for id in [&ticket.request_id, &ticket.nonce] {
        ensure!(
            Uuid::parse_str(id)?.simple().to_string() == *id,
            "invalid return nonce or request ID"
        );
    }
    ensure!(
        ticket.profile == normalize_profile_name(&ticket.profile)?,
        "noncanonical return profile"
    );
    ensure!(
        ticket.session_identity.session_id == ticket.session_id
            && ticket.session_identity.project_key.as_deref() == Some(ticket.project_key.as_str()),
        "return session binding mismatch"
    );
    ticket.session_identity.validate()?;
    ensure!(
        ticket.expires_at > ticket.created_at
            && ticket.expires_at - ticket.created_at <= Duration::minutes(5),
        "invalid return expiry"
    );
    for hash in [
        &ticket.package_sha256,
        &ticket.current_plan_sha256,
        &ticket.context_sha256,
        &ticket.pane_sha256,
    ] {
        ensure!(
            hash.len() == 64
                && hash
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
            "invalid return hash"
        );
    }
    ensure!(
        ticket.package.is_absolute()
            && ticket.workdir.is_absolute()
            && ticket.owning_plan.is_absolute(),
        "absolute return paths required"
    );
    message(ticket)?;
    Ok(())
}

fn message(ticket: &ReturnTicket) -> Result<String> {
    let message = serde_json::to_string(&json!({
        "schema":"ondesk_return_delivery_message.v1", "request_id":ticket.request_id,
        "nonce":ticket.nonce, "package_path":ticket.package,"package_sha256":ticket.package_sha256,
        "plan_path":ticket.owning_plan,"plan_sha256":ticket.current_plan_sha256,
        "instruction":"Read and verify both local files. This authorizes context reading only; do not execute tasks or write plans. Report remaining work. Emit FORAGER_RETURN_ACK REQUEST_ID TOKEN, replacing REQUEST_ID with request_id and TOKEN with SHA256 of these bytes concatenated: UTF8(forager.ondesk.return.ack.v1) then one NUL byte, UTF8(nonce), package length as 8-byte unsigned big-endian, exact package bytes, plan length as 8-byte unsigned big-endian, exact plan bytes. This is a read claim, not task completion."
    }))?;
    ensure!(
        message.len() <= 2000 && !message.contains(['\n', '\r', '\0']),
        "return reference exceeds literal input budget"
    );
    Ok(message)
}

fn revalidate(ticket: &ReturnTicket, before_input: bool) -> Result<(Instance, Vec<u8>, Vec<u8>)> {
    validate_ticket(ticket)?;
    let (package, saved, context) = package_context(&ticket.profile, &ticket.package)?;
    ensure!(
        digest(&package) == ticket.package_sha256
            && context.current_plan_sha256 == ticket.current_plan_sha256
            && context.project_key == ticket.project_key
            && context.workdir == ticket.workdir
            && context.owning_plan == ticket.owning_plan
            && context.shift_review == ticket.shift_review
            && digest(&serde_json::to_vec(&semantic_context(&saved))?) == ticket.context_sha256,
        "return artifacts changed"
    );
    if before_input {
        ensure!(
            context.current_git.status_sha256 == ticket.git_status_sha256,
            "return worktree changed since preview"
        );
    }
    let plan = read_absolute(&ticket.owning_plan, super::ondesk_plan::MAX_PLAN_BYTES)?;
    ensure!(
        digest(&plan) == ticket.current_plan_sha256,
        "return plan changed during read"
    );
    let instance = session(&ticket.profile, &ticket.session_id)?;
    if let Some(binding) = &ticket.read_policy_binding {
        ensure!(
            ticket.package.starts_with(&ticket.workdir)
                && ticket.owning_plan.starts_with(&ticket.workdir),
            "restricted return artifacts escaped the approved workdir"
        );
        crate::offdesk::read_policy_tmux::validate_live(
            binding,
            &instance.tmux_session()?,
            &ticket.workdir,
        )?;
    }
    ensure!(
        RemoteSessionIdentityV1::observe(&instance, Some(&ticket.project_key))?
            == ticket.session_identity,
        "return session identity changed"
    );
    if before_input {
        let pane = ticket
            .session_identity
            .capture_notification_tail(&instance, Some(&ticket.project_key))?;
        ensure!(
            digest(pane.as_bytes()) == ticket.pane_sha256
                && wiki_context_prompt_ready(&pane, &instance.tool),
            "return prompt changed or busy"
        );
    }
    Ok((instance, package, plan))
}

pub fn send(
    profile: &str,
    ticket: &ReturnTicket,
    expected: &str,
    confirmed: bool,
) -> Result<Value> {
    ensure!(confirmed, "explicit return-input confirmation required");
    ensure!(
        profile == ticket.profile && ticket_sha256(ticket)? == expected,
        "return preview hash or profile mismatch"
    );
    validate_ticket(ticket)?;
    with_ledger(profile, |root, path, ledger| {
        if let Some(entry) = ledger
            .entries
            .iter()
            .find(|e| e.ticket.request_id == ticket.request_id)
        {
            ensure!(
                entry.ticket == *ticket,
                "return request ID reused with different content"
            );
            return Ok(result(entry, false));
        }
        ensure!(
            ledger.entries.len() < MAX_TICKETS,
            "return ledger capacity requires explicit review"
        );
        ensure!(
            ticket.created_at <= Utc::now() && Utc::now() < ticket.expires_at,
            "return preview expired or future-dated"
        );
        let (instance, _, _) = revalidate(ticket, true)?;
        let tmux = instance.tmux_session()?;
        let runtime = tmux.runtime_identity()?;
        ledger.entries.push(Entry {
            ticket: ticket.clone(),
            state: DeliveryState::SendIntent,
            attempted_at: Utc::now(),
            observed_at: None,
            acknowledgement_pane_sha256: None,
        });
        persist(root, path, ledger)?;
        // Any interruption from here retains a no-retry intent. Validation
        // failures before transport are also conservatively held.
        let attempt = (|| -> Result<()> {
            revalidate(ticket, true)?;
            ensure!(
                Utc::now() < ticket.expires_at,
                "return preview expired before input"
            );
            let pane = ticket
                .session_identity
                .capture_notification_tail(&instance, Some(&ticket.project_key))?;
            ensure!(
                digest(pane.as_bytes()) == ticket.pane_sha256
                    && wiki_context_prompt_ready(&pane, &instance.tool),
                "return prompt changed before input"
            );
            tmux.send_literal_line(&message(ticket)?, &runtime)
        })();
        let entry = ledger
            .entries
            .last_mut()
            .context("return attempt missing")?;
        entry.state = if attempt.is_ok() {
            DeliveryState::InputQueued
        } else {
            DeliveryState::DeliveryUnconfirmed
        };
        let output = result(entry, true);
        persist(root, path, ledger)?;
        Ok(output)
    })
}

fn ack_token(ticket: &ReturnTicket, package: &[u8], plan: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(b"forager.ondesk.return.ack.v1\0");
    h.update(ticket.nonce.as_bytes());
    h.update((package.len() as u64).to_be_bytes());
    h.update(package);
    h.update((plan.len() as u64).to_be_bytes());
    h.update(plan);
    format!("{:x}", h.finalize())
}

pub fn observe(profile: &str, request_id: &str) -> Result<Value> {
    with_ledger(profile, |root, path, ledger| {
        let entry = ledger
            .entries
            .iter_mut()
            .find(|e| e.ticket.request_id == request_id)
            .context("return attempt not found")?;
        let (instance, package, plan) = revalidate(&entry.ticket, false)?;
        if entry.state == DeliveryState::AgentReadClaimObserved {
            return Ok(result(entry, false));
        }
        let pane = entry
            .ticket
            .session_identity
            .capture_notification_tail(&instance, Some(&entry.ticket.project_key))?;
        let marker = format!(
            "FORAGER_RETURN_ACK{}{}",
            entry.ticket.request_id,
            ack_token(&entry.ticket, &package, &plan)
        );
        let compact: String = pane.chars().filter(|c| !c.is_whitespace()).collect();
        if compact.contains(&marker) {
            entry.state = DeliveryState::AgentReadClaimObserved;
            entry.observed_at = Some(Utc::now());
            entry.acknowledgement_pane_sha256 = Some(digest(pane.as_bytes()));
        }
        let output = result(entry, false);
        persist(root, path, ledger)?;
        Ok(output)
    })
}

pub fn status(profile: &str, request_id: &str) -> Result<Value> {
    let path = profile_dir_path(profile)?;
    let root = process::open_bounded_root(&path)?;
    let ledger = load(&root)?;
    files::active_root(&path, &root)?;
    let entry = ledger
        .entries
        .iter()
        .find(|e| e.ticket.request_id == request_id)
        .context("return attempt not found")?;
    Ok(result(entry, false))
}

fn result(entry: &Entry, new_attempt: bool) -> Value {
    json!({"schema":"ondesk_return_delivery_receipt.v1", "request_id":entry.ticket.request_id,
        "session_id":entry.ticket.session_id,"state":entry.state,"attempted_at":entry.attempted_at,
        "profile":entry.ticket.profile,"package_sha256":entry.ticket.package_sha256,
        "current_plan_sha256":entry.ticket.current_plan_sha256,
        "observed_at":entry.observed_at,"new_send_attempt":new_attempt,"send_attempts":1,
        "acknowledgement_pane_sha256":entry.acknowledgement_pane_sha256,
        "read_policy_binding":entry.ticket.read_policy_binding,
        "read_claim_observed":entry.state == DeliveryState::AgentReadClaimObserved,
        "task_execution_status":"not_authorized_not_assessed","automatic_retry_allowed":false,
        "record_scope":"historical_attempt_and_claim_not_live_task_status",
        "limitations":["same_user_tampering_not_covered","screen_marker_is_agent_claim_not_semantic_understanding","external_editors_and_local_keyboard_are_not_fenced"]})
}

/// Launch only an explicitly selected, registered session with no live pane.
/// Existing unconfined processes cannot acquire this restriction retroactively.
pub fn launch_restricted(profile: &str, session_id: &str) -> Result<Value> {
    let instance = session(profile, session_id)?;
    ensure!(
        !instance.is_sandboxed(),
        "restricted return does not compose with legacy sandbox settings"
    );
    let tmux = instance.tmux_session()?;
    ensure!(!tmux.exists(), "restricted return launch requires a stopped session; existing processes cannot be retroactively confined");
    let workdir = Path::new(&instance.project_path);
    let root = crate::offdesk::shift_runtime::ProjectRootBoundary::open(workdir)?;
    let mut command = std::process::Command::new("/bin/sh");
    command.args(["-c", instance.get_tool_command()]);
    let binding = crate::offdesk::read_policy_tmux::launch(
        &std::env::current_exe()?,
        &tmux,
        workdir,
        &command,
        &crate::offdesk::read_policy::ReadPolicy::canonical(),
        &root.identity_sha256,
        None,
    )?;
    Ok(
        json!({"schema":"ondesk_restricted_return_launch.v1", "session_id":session_id,
        "read_policy_receipt":binding.receipt_path,"binding":binding,"input_sent":false,
        "repository_hooks_executed":false,"does_not_authorize":["return_input","plan_write","user_acceptance"]}),
    )
}

fn load(root: &File) -> Result<Ledger> {
    let bytes = match files::read(root, Path::new(LEDGER), MAX_LEDGER) {
        Ok(b) => b,
        Err(error)
            if error
                .downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound) =>
        {
            return Ok(Ledger {
                schema: "ondesk_return_delivery_ledger.v1".into(),
                entries: Vec::new(),
            })
        }
        Err(e) => return Err(e),
    };
    owner_only(&process::open_bounded_regular_file(
        root,
        Path::new(LEDGER),
    )?)?;
    let ledger: Ledger = serde_json::from_slice(&bytes)?;
    ensure!(
        ledger.schema == "ondesk_return_delivery_ledger.v1" && ledger.entries.len() <= MAX_TICKETS,
        "invalid return ledger"
    );
    let mut ids = std::collections::HashSet::new();
    for entry in &ledger.entries {
        validate_ticket(&entry.ticket)?;
        ensure!(
            ids.insert(&entry.ticket.request_id),
            "duplicate return request in ledger"
        );
        ensure!(
            (entry.state == DeliveryState::AgentReadClaimObserved) == entry.observed_at.is_some()
                && entry.observed_at.is_some() == entry.acknowledgement_pane_sha256.is_some(),
            "inconsistent return acknowledgement record"
        );
    }
    Ok(ledger)
}

fn with_ledger<T>(
    profile: &str,
    f: impl FnOnce(&File, &Path, &mut Ledger) -> Result<T>,
) -> Result<T> {
    let path = profile_dir_path(profile)?;
    let root = process::open_bounded_root(&path)?;
    let lock = match process::create_new_bounded_regular_file(&root, Path::new(LOCK)) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            process::open_bounded_regular_file(&root, Path::new(LOCK))?
        }
        Err(e) => return Err(e.into()),
    };
    ensure!(
        process::filesystem_link_count(&lock.metadata()?) == Some(1),
        "return lock must have one link"
    );
    owner_only(&lock)?;
    FileExt::try_lock_exclusive(&lock).context("another return transaction is active")?;
    files::active_root(&path, &root)?;
    let mut ledger = load(&root)?;
    ensure!(
        ledger.entries.iter().all(|e| e.ticket.profile == profile),
        "return ledger profile mismatch"
    );
    let result = f(&root, &path, &mut ledger);
    let _ = FileExt::unlock(&lock);
    result
}

fn owner_only(file: &File) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            file.metadata()?.permissions().mode() & 0o777 == 0o600,
            "return state must be owner-only"
        );
    }
    Ok(())
}

fn persist(root: &File, path: &Path, ledger: &Ledger) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(ledger)?;
    ensure!(
        bytes.len() <= MAX_LEDGER,
        "return ledger exceeds byte limit"
    );
    let name = PathBuf::from(format!(".{LEDGER}.{}.tmp", Uuid::new_v4()));
    let result = (|| -> Result<()> {
        let mut file = process::create_new_bounded_regular_file(root, &name)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        }
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        files::active_root(path, root)?;
        process::replace_bounded_root_file(root, &name, Path::new(LEDGER))?;
        root.sync_all()?;
        files::active_root(path, root)?;
        ensure!(
            files::read(root, Path::new(LEDGER), MAX_LEDGER)? == bytes,
            "return ledger changed after commit"
        );
        Ok(())
    })();
    if result.is_err() {
        let _ = process::remove_bounded_root_file(root, &name);
    }
    result
}
