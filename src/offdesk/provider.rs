//! Provider error classification and provider transport descriptors.

use serde::{Deserialize, Serialize};

use super::redaction::operator_safe_text;

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
    ModelNotFound,
    FormatError,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderErrorClassification {
    pub reason: ProviderErrorReason,
    pub retryable: bool,
    pub should_fallback: bool,
    pub should_record_cooldown: bool,
    pub should_compress: bool,
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

/// Classify provider errors before fallback and provider capacity cooldown logic.
pub fn classify_provider_error(
    status_code: Option<u16>,
    message: &str,
    retry_after_sec: Option<u64>,
) -> ProviderErrorClassification {
    let lower = message.to_ascii_lowercase();
    let reason = if matches!(status_code, Some(401 | 403))
        || contains_any(
            &lower,
            &[
                "unauthorized",
                "forbidden",
                "invalid api key",
                "bad api key",
            ],
        ) {
        ProviderErrorReason::Auth
    } else if matches!(status_code, Some(402))
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
    } else if status_code == Some(429)
        || contains_any(&lower, &["rate limit", "too many requests", "rate_limit"])
    {
        ProviderErrorReason::RateLimit
    } else if contains_any(&lower, &["overloaded", "capacity", "try again later"])
        || status_code == Some(529)
    {
        ProviderErrorReason::Overloaded
    } else if contains_any(&lower, &["timed out", "timeout", "deadline exceeded"])
        || status_code == Some(408)
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
    } else if status_code == Some(413)
        || contains_any(
            &lower,
            &["payload too large", "request too large", "body too large"],
        )
    {
        ProviderErrorReason::PayloadTooLarge
    } else if status_code == Some(404)
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
    } else if matches!(status_code, Some(500..=599)) {
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
        ProviderErrorReason::ContextOverflow | ProviderErrorReason::PayloadTooLarge
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
            | ProviderErrorReason::ModelNotFound
    );

    ProviderErrorClassification {
        reason,
        retryable,
        should_fallback,
        should_record_cooldown,
        should_compress,
        retry_after_sec,
        summary: operator_safe_text(message),
    }
}

fn contains_any(haystack: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| haystack.contains(needle))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_429_to_rate_limit_with_cooldown_and_fallback() {
        let result = classify_provider_error(Some(429), "Rate limit exceeded", Some(60));
        assert_eq!(result.reason, ProviderErrorReason::RateLimit);
        assert!(result.retryable);
        assert!(result.should_fallback);
        assert!(result.should_record_cooldown);
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
    }

    #[test]
    fn provider_error_summary_is_redacted() {
        let result = classify_provider_error(None, "failed with token=sk-secretsecretsecret", None);
        assert!(!result.summary.contains("sk-secret"));
    }
}
