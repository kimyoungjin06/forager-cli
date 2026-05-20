//! Provider error classification and provider transport descriptors.

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use super::redaction::operator_safe_text;

const PROVIDER_CAPACITY_FILE: &str = "provider_capacity.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderErrorReason {
    Auth,
    Billing,
    RateLimit,
    Overloaded,
    ServerError,
    Timeout,
    ContextOverflow,
    PayloadTooLarge,
    ImageTooLarge,
    ModelNotFound,
    ProviderPolicyBlocked,
    LongContextTier,
    FormatError,
    Unknown,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderRecoveryAction {
    #[default]
    Abort,
    Retry,
    CooldownThenRetry,
    CompressThenRetry,
    Fallback,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderErrorClassification {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status_code: Option<u16>,
    pub reason: ProviderErrorReason,
    pub retryable: bool,
    pub should_fallback: bool,
    pub should_record_cooldown: bool,
    pub should_compress: bool,
    #[serde(default)]
    pub recommended_action: ProviderRecoveryAction,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub retry_after_sec: Option<u64>,
    pub summary: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderKind {
    Anthropic,
    Openai,
    OpenaiCompatible,
    ClaudeCodeCli,
    CodexCli,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderDescriptor {
    pub provider_id: String,
    pub kind: ProviderKind,
    pub endpoint_metadata: String,
    pub auth_env_name: String,
    pub response_text_extractor: String,
    #[serde(default)]
    pub supported_runner_roles: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderProfile {
    pub provider_id: String,
    pub kind: ProviderKind,
    pub display_name: String,
    #[serde(default)]
    pub auth_env_names: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub base_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub models_url: Option<String>,
    #[serde(default)]
    pub fallback_models: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_aux_model: Option<String>,
    #[serde(default)]
    pub supports_streaming: bool,
    #[serde(default)]
    pub supports_tool_calls: bool,
    #[serde(default)]
    pub supported_runner_roles: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderFallbackAuthStatus {
    Available,
    MissingAuth,
    NotRequired,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderFallbackSource {
    SameProviderModel,
    CrossProviderFallbackModel,
    CrossProviderDefaultModel,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderFallbackCandidate {
    pub provider_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    pub source: ProviderFallbackSource,
    pub auth_status: ProviderFallbackAuthStatus,
    pub capacity_status: ProviderCapacityStatus,
    pub recommended: bool,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderFallbackRecommendation {
    pub current_provider_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_model: Option<String>,
    pub trigger_reason: String,
    pub generated_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<ProviderFallbackCandidate>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderErrorInput {
    pub provider_id: Option<String>,
    pub model: Option<String>,
    pub status_code: Option<u16>,
    pub message: String,
    pub retry_after_sec: Option<u64>,
}

impl ProviderErrorInput {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            provider_id: None,
            model: None,
            status_code: None,
            message: message.into(),
            retry_after_sec: None,
        }
    }
}

/// Classify provider errors before fallback and provider capacity cooldown logic.
pub fn classify_provider_error(
    status_code: Option<u16>,
    message: &str,
    retry_after_sec: Option<u64>,
) -> ProviderErrorClassification {
    classify_provider_error_with_context(ProviderErrorInput {
        provider_id: None,
        model: None,
        status_code,
        message: message.to_string(),
        retry_after_sec,
    })
}

/// Classify a provider error while preserving provider/model context for policy inputs.
pub fn classify_provider_error_with_context(
    input: ProviderErrorInput,
) -> ProviderErrorClassification {
    let lower = input.message.to_ascii_lowercase();
    let reason = if contains_any(
        &lower,
        &[
            "long context beta",
            "long-context beta",
            "long context tier",
            "context tier",
            "oauth long context",
        ],
    ) {
        ProviderErrorReason::LongContextTier
    } else if contains_any(
        &lower,
        &[
            "provider policy",
            "policy blocked",
            "blocked by policy",
            "content policy",
            "safety policy",
            "moderation policy",
        ],
    ) {
        ProviderErrorReason::ProviderPolicyBlocked
    } else if matches!(input.status_code, Some(401 | 403))
        || contains_any(
            &lower,
            &[
                "unauthorized",
                "forbidden",
                "invalid api key",
                "bad api key",
            ],
        )
    {
        ProviderErrorReason::Auth
    } else if matches!(input.status_code, Some(402))
        || contains_any(
            &lower,
            &[
                "billing",
                "quota exceeded",
                "insufficient credits",
                "payment required",
            ],
        )
    {
        ProviderErrorReason::Billing
    } else if input.status_code == Some(429)
        || contains_any(&lower, &["rate limit", "too many requests", "rate_limit"])
    {
        ProviderErrorReason::RateLimit
    } else if contains_any(&lower, &["overloaded", "capacity", "try again later"])
        || input.status_code == Some(529)
    {
        ProviderErrorReason::Overloaded
    } else if contains_any(&lower, &["timed out", "timeout", "deadline exceeded"])
        || input.status_code == Some(408)
    {
        ProviderErrorReason::Timeout
    } else if contains_any(
        &lower,
        &[
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "input is too long",
        ],
    ) {
        ProviderErrorReason::ContextOverflow
    } else if contains_any(
        &lower,
        &[
            "image too large",
            "image size",
            "image exceeds",
            "unsupported image size",
        ],
    ) {
        ProviderErrorReason::ImageTooLarge
    } else if input.status_code == Some(413)
        || contains_any(
            &lower,
            &["payload too large", "request too large", "body too large"],
        )
    {
        ProviderErrorReason::PayloadTooLarge
    } else if input.status_code == Some(404)
        || contains_any(
            &lower,
            &[
                "model not found",
                "unknown model",
                "does not exist",
                "invalid model",
            ],
        )
    {
        ProviderErrorReason::ModelNotFound
    } else if contains_any(
        &lower,
        &[
            "invalid json",
            "malformed",
            "schema validation",
            "invalid request format",
        ],
    ) {
        ProviderErrorReason::FormatError
    } else if matches!(input.status_code, Some(500..=599)) {
        ProviderErrorReason::ServerError
    } else {
        ProviderErrorReason::Unknown
    };

    let retryable = matches!(
        reason,
        ProviderErrorReason::RateLimit
            | ProviderErrorReason::Overloaded
            | ProviderErrorReason::ServerError
            | ProviderErrorReason::Timeout
            | ProviderErrorReason::Unknown
    );
    let should_compress = matches!(
        reason,
        ProviderErrorReason::ContextOverflow
            | ProviderErrorReason::PayloadTooLarge
            | ProviderErrorReason::ImageTooLarge
    );
    let should_record_cooldown = matches!(
        reason,
        ProviderErrorReason::RateLimit
            | ProviderErrorReason::Overloaded
            | ProviderErrorReason::ServerError
    );
    let should_fallback = matches!(
        reason,
        ProviderErrorReason::RateLimit
            | ProviderErrorReason::Overloaded
            | ProviderErrorReason::ServerError
            | ProviderErrorReason::Timeout
            | ProviderErrorReason::ContextOverflow
            | ProviderErrorReason::PayloadTooLarge
            | ProviderErrorReason::ImageTooLarge
            | ProviderErrorReason::ModelNotFound
            | ProviderErrorReason::ProviderPolicyBlocked
            | ProviderErrorReason::LongContextTier
    );
    let recommended_action = recommended_action(
        retryable,
        should_record_cooldown,
        should_compress,
        should_fallback,
    );

    ProviderErrorClassification {
        provider_id: normalize_optional(input.provider_id),
        model: normalize_optional(input.model),
        status_code: input.status_code,
        reason,
        retryable,
        should_fallback,
        should_record_cooldown,
        should_compress,
        recommended_action,
        retry_after_sec: input.retry_after_sec,
        summary: operator_safe_text(&input.message),
    }
}

pub fn default_provider_profiles() -> Vec<ProviderProfile> {
    vec![
        provider_profile(ProfileSeed {
            provider_id: "anthropic",
            kind: ProviderKind::Anthropic,
            display_name: "Anthropic",
            auth_env_names: &["ANTHROPIC_API_KEY"],
            base_url: Some("https://api.anthropic.com"),
            models_url: None,
            fallback_models: &["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
            default_aux_model: Some("claude-3-5-haiku-latest"),
            supports_streaming: true,
            supports_tool_calls: true,
            supported_runner_roles: &["llm", "planner", "worker"],
        }),
        provider_profile(ProfileSeed {
            provider_id: "openai",
            kind: ProviderKind::Openai,
            display_name: "OpenAI",
            auth_env_names: &["OPENAI_API_KEY"],
            base_url: Some("https://api.openai.com/v1"),
            models_url: Some("https://api.openai.com/v1/models"),
            fallback_models: &["gpt-4.1", "gpt-4.1-mini"],
            default_aux_model: Some("gpt-4.1-mini"),
            supports_streaming: true,
            supports_tool_calls: true,
            supported_runner_roles: &["llm", "planner", "worker"],
        }),
        provider_profile(ProfileSeed {
            provider_id: "openai-compatible",
            kind: ProviderKind::OpenaiCompatible,
            display_name: "OpenAI Compatible",
            auth_env_names: &["OPENAI_API_KEY", "OPENAI_BASE_URL"],
            base_url: None,
            models_url: None,
            fallback_models: &[],
            default_aux_model: None,
            supports_streaming: true,
            supports_tool_calls: true,
            supported_runner_roles: &["llm", "planner", "worker"],
        }),
        provider_profile(ProfileSeed {
            provider_id: "claude-code-cli",
            kind: ProviderKind::ClaudeCodeCli,
            display_name: "Claude Code CLI",
            auth_env_names: &[],
            base_url: None,
            models_url: None,
            fallback_models: &[],
            default_aux_model: None,
            supports_streaming: false,
            supports_tool_calls: false,
            supported_runner_roles: &["cli", "worker"],
        }),
        provider_profile(ProfileSeed {
            provider_id: "codex-cli",
            kind: ProviderKind::CodexCli,
            display_name: "Codex CLI",
            auth_env_names: &[],
            base_url: None,
            models_url: None,
            fallback_models: &[],
            default_aux_model: None,
            supports_streaming: false,
            supports_tool_calls: false,
            supported_runner_roles: &["cli", "worker"],
        }),
    ]
}

pub fn default_provider_profile(provider_id: &str) -> Option<ProviderProfile> {
    default_provider_profiles()
        .into_iter()
        .find(|profile| profile.provider_id == provider_id)
}

pub fn recommend_provider_fallback(
    store: &ProviderCapacityStore,
    current_provider_id: &str,
    current_model: Option<&str>,
    trigger_reason: &str,
    runner_role: &str,
    now: DateTime<Utc>,
) -> Result<ProviderFallbackRecommendation> {
    let states = store.load()?;
    Ok(recommend_provider_fallback_from_states(
        &states,
        current_provider_id,
        current_model,
        trigger_reason,
        runner_role,
        now,
        env_auth_available,
    ))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderCapacityStatus {
    Available,
    CoolingDown,
    Blocked,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderCapacityState {
    pub provider_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    pub status: ProviderCapacityStatus,
    pub reason: ProviderErrorReason,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cooldown_until: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error_summary: Option<String>,
    pub updated_at: DateTime<Utc>,
}

impl ProviderCapacityState {
    pub fn from_classification(
        classification: &ProviderErrorClassification,
        now: DateTime<Utc>,
    ) -> Option<Self> {
        if !classification.should_record_cooldown {
            return None;
        }
        let seconds = classification
            .retry_after_sec
            .unwrap_or_else(|| default_cooldown_sec(classification.reason))
            .min(i64::MAX as u64) as i64;

        Some(Self {
            provider_id: classification
                .provider_id
                .clone()
                .unwrap_or_else(|| "unknown".to_string()),
            model: classification.model.clone(),
            status: ProviderCapacityStatus::CoolingDown,
            reason: classification.reason,
            cooldown_until: Some(now + Duration::seconds(seconds)),
            last_error_summary: Some(classification.summary.clone()),
            updated_at: now,
        })
    }

    pub fn is_cooling_down_at(&self, now: DateTime<Utc>) -> bool {
        self.status == ProviderCapacityStatus::CoolingDown
            && self
                .cooldown_until
                .is_some_and(|cooldown_until| cooldown_until > now)
    }
}

#[derive(Debug, Clone)]
pub struct ProviderCapacityStore {
    root: PathBuf,
}

impl ProviderCapacityStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn path(&self) -> PathBuf {
        self.root.join(PROVIDER_CAPACITY_FILE)
    }

    pub fn load(&self) -> Result<Vec<ProviderCapacityState>> {
        read_capacity_states(&self.path())
    }

    pub fn save(&self, states: &[ProviderCapacityState]) -> Result<()> {
        write_capacity_states(&self.path(), states)
    }

    pub fn upsert(&self, state: ProviderCapacityState) -> Result<()> {
        let mut states = self.load()?;
        upsert_capacity_state(&mut states, state);
        self.save(&states)
    }

    pub fn record_failure(
        &self,
        classification: &ProviderErrorClassification,
        now: DateTime<Utc>,
    ) -> Result<Option<ProviderCapacityState>> {
        let Some(state) = ProviderCapacityState::from_classification(classification, now) else {
            return Ok(None);
        };
        self.upsert(state.clone())?;
        Ok(Some(state))
    }

    pub fn get(
        &self,
        provider_id: &str,
        model: Option<&str>,
    ) -> Result<Option<ProviderCapacityState>> {
        Ok(self
            .load()?
            .into_iter()
            .find(|state| state.provider_id == provider_id && state.model.as_deref() == model))
    }

    pub fn scheduling_match(
        &self,
        provider_id: &str,
        model: Option<&str>,
    ) -> Result<Option<ProviderCapacityState>> {
        let states = self.load()?;
        if let Some(model) = model {
            if let Some(state) = states.iter().find(|state| {
                state.provider_id == provider_id && state.model.as_deref() == Some(model)
            }) {
                return Ok(Some(state.clone()));
            }
        }

        Ok(states
            .into_iter()
            .find(|state| state.provider_id == provider_id && state.model.is_none()))
    }
}

fn contains_any(haystack: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| haystack.contains(needle))
}

fn recommended_action(
    retryable: bool,
    should_record_cooldown: bool,
    should_compress: bool,
    should_fallback: bool,
) -> ProviderRecoveryAction {
    if should_compress {
        ProviderRecoveryAction::CompressThenRetry
    } else if should_record_cooldown {
        ProviderRecoveryAction::CooldownThenRetry
    } else if should_fallback {
        ProviderRecoveryAction::Fallback
    } else if retryable {
        ProviderRecoveryAction::Retry
    } else {
        ProviderRecoveryAction::Abort
    }
}

fn default_cooldown_sec(reason: ProviderErrorReason) -> u64 {
    match reason {
        ProviderErrorReason::RateLimit => 60,
        ProviderErrorReason::Overloaded => 30,
        ProviderErrorReason::ServerError => 15,
        _ => 0,
    }
}

fn normalize_optional(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn recommend_provider_fallback_from_states<F>(
    states: &[ProviderCapacityState],
    current_provider_id: &str,
    current_model: Option<&str>,
    trigger_reason: &str,
    runner_role: &str,
    now: DateTime<Utc>,
    auth_available: F,
) -> ProviderFallbackRecommendation
where
    F: Fn(&str) -> bool,
{
    let current_provider_id = current_provider_id.trim();
    let current_provider_id = if current_provider_id.is_empty() {
        "unknown"
    } else {
        current_provider_id
    };
    let current_model = current_model
        .map(str::trim)
        .filter(|model| !model.is_empty())
        .map(str::to_string);
    let runner_role = runner_role.trim();
    let runner_role = if runner_role.is_empty() {
        "worker"
    } else {
        runner_role
    };
    let profiles = default_provider_profiles();
    let mut seen = HashSet::<(String, Option<String>)>::new();
    let mut candidates = Vec::new();
    let context = ProviderFallbackCandidateContext {
        states,
        now,
        auth_available: &auth_available,
    };

    if let Some(profile) = profiles
        .iter()
        .find(|profile| profile.provider_id == current_provider_id)
    {
        for model in &profile.fallback_models {
            if current_model.as_deref() == Some(model.as_str()) {
                continue;
            }
            push_provider_fallback_candidate(
                &mut candidates,
                &mut seen,
                profile,
                Some(model.as_str()),
                ProviderFallbackSource::SameProviderModel,
                &context,
            );
        }
    }

    for profile in profiles
        .iter()
        .filter(|profile| profile.provider_id != current_provider_id)
        .filter(|profile| provider_supports_runner_role(profile, runner_role))
    {
        for model in &profile.fallback_models {
            push_provider_fallback_candidate(
                &mut candidates,
                &mut seen,
                profile,
                Some(model.as_str()),
                ProviderFallbackSource::CrossProviderFallbackModel,
                &context,
            );
        }
        if let Some(model) = profile.default_aux_model.as_deref() {
            push_provider_fallback_candidate(
                &mut candidates,
                &mut seen,
                profile,
                Some(model),
                ProviderFallbackSource::CrossProviderDefaultModel,
                &context,
            );
        }
    }

    ProviderFallbackRecommendation {
        current_provider_id: operator_safe_text(current_provider_id),
        current_model: current_model.as_deref().map(operator_safe_text),
        trigger_reason: operator_safe_text(trigger_reason),
        generated_at: now,
        candidates,
    }
}

struct ProviderFallbackCandidateContext<'a, F>
where
    F: Fn(&str) -> bool,
{
    states: &'a [ProviderCapacityState],
    now: DateTime<Utc>,
    auth_available: &'a F,
}

fn push_provider_fallback_candidate<F>(
    candidates: &mut Vec<ProviderFallbackCandidate>,
    seen: &mut HashSet<(String, Option<String>)>,
    profile: &ProviderProfile,
    model: Option<&str>,
    source: ProviderFallbackSource,
    context: &ProviderFallbackCandidateContext<'_, F>,
) where
    F: Fn(&str) -> bool,
{
    let key = (
        profile.provider_id.clone(),
        model
            .map(operator_safe_text)
            .filter(|model| !model.is_empty()),
    );
    if !seen.insert(key.clone()) {
        return;
    }

    let auth_status = provider_auth_status(profile, context.auth_available);
    let capacity_status =
        provider_capacity_status(context.states, &profile.provider_id, model, context.now);
    let recommended = auth_status != ProviderFallbackAuthStatus::MissingAuth
        && capacity_status == ProviderCapacityStatus::Available;
    let reason = provider_fallback_candidate_reason(source, auth_status, capacity_status);

    candidates.push(ProviderFallbackCandidate {
        provider_id: operator_safe_text(&profile.provider_id),
        model: key.1,
        source,
        auth_status,
        capacity_status,
        recommended,
        reason,
    });
}

fn provider_auth_status<F>(
    profile: &ProviderProfile,
    auth_available: &F,
) -> ProviderFallbackAuthStatus
where
    F: Fn(&str) -> bool,
{
    if profile.auth_env_names.is_empty() {
        return ProviderFallbackAuthStatus::NotRequired;
    }
    if profile
        .auth_env_names
        .iter()
        .all(|env_name| auth_available(env_name))
    {
        ProviderFallbackAuthStatus::Available
    } else {
        ProviderFallbackAuthStatus::MissingAuth
    }
}

fn provider_capacity_status(
    states: &[ProviderCapacityState],
    provider_id: &str,
    model: Option<&str>,
    now: DateTime<Utc>,
) -> ProviderCapacityStatus {
    let Some(state) = scheduling_match_in_states(states, provider_id, model) else {
        return ProviderCapacityStatus::Available;
    };
    if state.status == ProviderCapacityStatus::Blocked {
        return ProviderCapacityStatus::Blocked;
    }
    if state.is_cooling_down_at(now) {
        return ProviderCapacityStatus::CoolingDown;
    }
    ProviderCapacityStatus::Available
}

fn scheduling_match_in_states<'a>(
    states: &'a [ProviderCapacityState],
    provider_id: &str,
    model: Option<&str>,
) -> Option<&'a ProviderCapacityState> {
    if let Some(model) = model {
        if let Some(state) = states
            .iter()
            .find(|state| state.provider_id == provider_id && state.model.as_deref() == Some(model))
        {
            return Some(state);
        }
    }

    states
        .iter()
        .find(|state| state.provider_id == provider_id && state.model.is_none())
}

fn provider_fallback_candidate_reason(
    source: ProviderFallbackSource,
    auth_status: ProviderFallbackAuthStatus,
    capacity_status: ProviderCapacityStatus,
) -> String {
    if auth_status == ProviderFallbackAuthStatus::MissingAuth {
        return "auth not available for provider".to_string();
    }
    match capacity_status {
        ProviderCapacityStatus::CoolingDown => "provider capacity cooldown active".to_string(),
        ProviderCapacityStatus::Blocked => "provider capacity blocked".to_string(),
        ProviderCapacityStatus::Available => match source {
            ProviderFallbackSource::SameProviderModel => "same provider fallback model".to_string(),
            ProviderFallbackSource::CrossProviderFallbackModel => {
                "cross provider fallback model".to_string()
            }
            ProviderFallbackSource::CrossProviderDefaultModel => {
                "cross provider default model".to_string()
            }
        },
    }
}

fn provider_supports_runner_role(profile: &ProviderProfile, runner_role: &str) -> bool {
    profile.supported_runner_roles.is_empty()
        || profile
            .supported_runner_roles
            .iter()
            .any(|role| role == runner_role)
}

fn env_auth_available(env_name: &str) -> bool {
    std::env::var_os(env_name).is_some_and(|value| !value.as_os_str().is_empty())
}

struct ProfileSeed<'a> {
    provider_id: &'a str,
    kind: ProviderKind,
    display_name: &'a str,
    auth_env_names: &'a [&'a str],
    base_url: Option<&'a str>,
    models_url: Option<&'a str>,
    fallback_models: &'a [&'a str],
    default_aux_model: Option<&'a str>,
    supports_streaming: bool,
    supports_tool_calls: bool,
    supported_runner_roles: &'a [&'a str],
}

fn provider_profile(seed: ProfileSeed<'_>) -> ProviderProfile {
    ProviderProfile {
        provider_id: seed.provider_id.to_string(),
        kind: seed.kind,
        display_name: seed.display_name.to_string(),
        auth_env_names: seed
            .auth_env_names
            .iter()
            .map(|value| value.to_string())
            .collect(),
        base_url: seed.base_url.map(str::to_string),
        models_url: seed.models_url.map(str::to_string),
        fallback_models: seed
            .fallback_models
            .iter()
            .map(|value| value.to_string())
            .collect(),
        default_aux_model: seed.default_aux_model.map(str::to_string),
        supports_streaming: seed.supports_streaming,
        supports_tool_calls: seed.supports_tool_calls,
        supported_runner_roles: seed
            .supported_runner_roles
            .iter()
            .map(|value| value.to_string())
            .collect(),
    }
}

fn read_capacity_states(path: &Path) -> Result<Vec<ProviderCapacityState>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    if content.trim().is_empty() {
        return Ok(Vec::new());
    }
    Ok(serde_json::from_str(&content)?)
}

fn write_capacity_states(path: &Path, states: &[ProviderCapacityState]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(states)?)?;
    Ok(())
}

fn upsert_capacity_state(states: &mut Vec<ProviderCapacityState>, state: ProviderCapacityState) {
    if let Some(existing) = states
        .iter_mut()
        .find(|existing| existing.provider_id == state.provider_id && existing.model == state.model)
    {
        *existing = state;
    } else {
        states.push(state);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn maps_429_to_rate_limit_with_cooldown_and_fallback() {
        let result = classify_provider_error(Some(429), "Rate limit exceeded", Some(60));
        assert_eq!(result.reason, ProviderErrorReason::RateLimit);
        assert!(result.retryable);
        assert!(result.should_fallback);
        assert!(result.should_record_cooldown);
        assert_eq!(
            result.recommended_action,
            ProviderRecoveryAction::CooldownThenRetry
        );
        assert_eq!(result.retry_after_sec, Some(60));
    }

    #[test]
    fn maps_auth_billing_and_model_not_found_to_stable_reasons() {
        assert_eq!(
            classify_provider_error(Some(401), "invalid api key", None).reason,
            ProviderErrorReason::Auth
        );
        assert_eq!(
            classify_provider_error(Some(402), "billing issue", None).reason,
            ProviderErrorReason::Billing
        );
        assert_eq!(
            classify_provider_error(Some(404), "model not found", None).reason,
            ProviderErrorReason::ModelNotFound
        );
    }

    #[test]
    fn context_overflow_requests_compression() {
        let result = classify_provider_error(None, "maximum context length exceeded", None);
        assert_eq!(result.reason, ProviderErrorReason::ContextOverflow);
        assert!(result.should_compress);
        assert!(result.should_fallback);
        assert_eq!(
            result.recommended_action,
            ProviderRecoveryAction::CompressThenRetry
        );
    }

    #[test]
    fn provider_error_summary_is_redacted() {
        let result = classify_provider_error(None, "failed with token=sk-secretsecretsecret", None);
        assert!(!result.summary.contains("sk-secret"));
    }

    #[test]
    fn context_classifier_preserves_provider_and_model() {
        let result = classify_provider_error_with_context(ProviderErrorInput {
            provider_id: Some(" openai ".to_string()),
            model: Some("gpt-4.1".to_string()),
            status_code: Some(529),
            message: "provider overloaded".to_string(),
            retry_after_sec: Some(45),
        });

        assert_eq!(result.provider_id.as_deref(), Some("openai"));
        assert_eq!(result.model.as_deref(), Some("gpt-4.1"));
        assert_eq!(result.status_code, Some(529));
        assert_eq!(result.reason, ProviderErrorReason::Overloaded);
        assert_eq!(
            result.recommended_action,
            ProviderRecoveryAction::CooldownThenRetry
        );
    }

    #[test]
    fn maps_policy_and_size_errors_to_recovery_hints() {
        let policy = classify_provider_error(None, "blocked by provider policy", None);
        assert_eq!(policy.reason, ProviderErrorReason::ProviderPolicyBlocked);
        assert!(policy.should_fallback);
        assert_eq!(policy.recommended_action, ProviderRecoveryAction::Fallback);

        let image = classify_provider_error(None, "image too large for this model", None);
        assert_eq!(image.reason, ProviderErrorReason::ImageTooLarge);
        assert!(image.should_compress);
        assert!(!image.should_record_cooldown);
    }

    #[test]
    fn default_provider_profiles_cover_builtin_backends() {
        let profiles = default_provider_profiles();
        assert!(profiles
            .iter()
            .any(|profile| profile.provider_id == "anthropic"));
        assert!(profiles
            .iter()
            .any(|profile| profile.provider_id == "openai"));
        assert!(profiles
            .iter()
            .any(|profile| profile.provider_id == "codex-cli"));

        let openai = default_provider_profile("openai").expect("openai profile");
        assert_eq!(openai.kind, ProviderKind::Openai);
        assert!(openai
            .auth_env_names
            .contains(&"OPENAI_API_KEY".to_string()));
    }

    #[test]
    fn provider_fallback_orders_same_provider_before_cross_provider() {
        let now = Utc::now();
        let recommendation = recommend_provider_fallback_from_states(
            &[],
            "openai",
            Some("gpt-4.1"),
            "provider capacity cooldown active",
            "worker",
            now,
            |_| true,
        );

        let first = recommendation.candidates.first().expect("candidate");
        assert_eq!(first.provider_id, "openai");
        assert_eq!(first.model.as_deref(), Some("gpt-4.1-mini"));
        assert_eq!(first.source, ProviderFallbackSource::SameProviderModel);
        assert!(recommendation
            .candidates
            .iter()
            .all(|candidate| !(candidate.provider_id == "openai"
                && candidate.model.as_deref() == Some("gpt-4.1"))));
        assert!(recommendation
            .candidates
            .iter()
            .skip_while(|candidate| {
                candidate.source == ProviderFallbackSource::SameProviderModel
            })
            .all(|candidate| {
                matches!(
                    candidate.source,
                    ProviderFallbackSource::CrossProviderFallbackModel
                        | ProviderFallbackSource::CrossProviderDefaultModel
                )
            }));
    }

    #[test]
    fn provider_fallback_marks_missing_auth_without_filtering_candidate() {
        let recommendation = recommend_provider_fallback_from_states(
            &[],
            "openai",
            Some("gpt-4.1"),
            "provider capacity cooldown active",
            "worker",
            Utc::now(),
            |_| false,
        );

        let candidate = recommendation
            .candidates
            .iter()
            .find(|candidate| candidate.provider_id == "anthropic")
            .expect("anthropic candidate");
        assert_eq!(
            candidate.auth_status,
            ProviderFallbackAuthStatus::MissingAuth
        );
        assert!(!candidate.recommended);
        assert!(!candidate.reason.contains("ANTHROPIC_API_KEY"));
    }

    #[test]
    fn provider_fallback_keeps_cooling_candidates_not_recommended() {
        let now = Utc::now();
        let states = vec![ProviderCapacityState {
            provider_id: "anthropic".to_string(),
            model: Some("claude-3-5-sonnet-latest".to_string()),
            status: ProviderCapacityStatus::CoolingDown,
            reason: ProviderErrorReason::RateLimit,
            cooldown_until: Some(now + Duration::minutes(1)),
            last_error_summary: Some("rate limit".to_string()),
            updated_at: now,
        }];

        let recommendation = recommend_provider_fallback_from_states(
            &states,
            "openai",
            Some("gpt-4.1"),
            "provider capacity cooldown active",
            "worker",
            now,
            |_| true,
        );

        let candidate = recommendation
            .candidates
            .iter()
            .find(|candidate| {
                candidate.provider_id == "anthropic"
                    && candidate.model.as_deref() == Some("claude-3-5-sonnet-latest")
            })
            .expect("cooling candidate");
        assert_eq!(
            candidate.capacity_status,
            ProviderCapacityStatus::CoolingDown
        );
        assert!(!candidate.recommended);
    }

    #[test]
    fn capacity_state_records_only_cooldown_errors() {
        let now = Utc::now();
        let rate_limit = classify_provider_error_with_context(ProviderErrorInput {
            provider_id: Some("anthropic".to_string()),
            model: Some("claude-3-5-sonnet-latest".to_string()),
            status_code: Some(429),
            message: "rate limit token=sk-secretsecretsecret".to_string(),
            retry_after_sec: Some(120),
        });
        let state =
            ProviderCapacityState::from_classification(&rate_limit, now).expect("capacity state");

        assert_eq!(state.provider_id, "anthropic");
        assert_eq!(state.model.as_deref(), Some("claude-3-5-sonnet-latest"));
        assert_eq!(state.status, ProviderCapacityStatus::CoolingDown);
        assert_eq!(state.cooldown_until, Some(now + Duration::seconds(120)));
        assert!(state.is_cooling_down_at(now + Duration::seconds(30)));
        assert!(!state
            .last_error_summary
            .as_deref()
            .unwrap()
            .contains("sk-secret"));

        let context = classify_provider_error(None, "maximum context length exceeded", None);
        assert!(ProviderCapacityState::from_classification(&context, now).is_none());
    }

    #[test]
    fn capacity_store_upserts_provider_model_state() -> Result<()> {
        let temp = tempdir()?;
        let store = ProviderCapacityStore::new(temp.path());
        let now = Utc::now();
        let first = classify_provider_error_with_context(ProviderErrorInput {
            provider_id: Some("openai".to_string()),
            model: Some("gpt-4.1".to_string()),
            status_code: Some(500),
            message: "server error".to_string(),
            retry_after_sec: None,
        });
        let second = classify_provider_error_with_context(ProviderErrorInput {
            provider_id: Some("openai".to_string()),
            model: Some("gpt-4.1".to_string()),
            status_code: Some(429),
            message: "rate limit".to_string(),
            retry_after_sec: Some(90),
        });

        store.record_failure(&first, now)?;
        store.record_failure(&second, now + Duration::seconds(1))?;

        let states = store.load()?;
        assert_eq!(states.len(), 1);
        assert_eq!(states[0].reason, ProviderErrorReason::RateLimit);
        assert_eq!(
            store
                .get("openai", Some("gpt-4.1"))?
                .expect("capacity record")
                .cooldown_until,
            Some(now + Duration::seconds(91))
        );

        let compress = classify_provider_error(None, "payload too large", None);
        assert!(store
            .record_failure(&compress, now + Duration::seconds(2))?
            .is_none());
        assert_eq!(store.load()?.len(), 1);
        Ok(())
    }
}
