//! Redaction and context-fencing helpers for operator-facing output.

use regex::{Captures, Regex};
use serde::Serialize;
use std::sync::OnceLock;

fn private_key_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?s)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        )
        .expect("valid private key regex")
    })
}

fn telegram_token_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b").expect("valid regex"))
}

fn bearer_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}").expect("valid bearer regex")
    })
}

fn api_key_prefix_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{20,})\b",
        )
        .expect("valid API key prefix regex")
    })
}

fn assignment_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r#"(?i)\b(password|token|api[_-]?key|secret)\s*[:=]\s*("[^"]*"|'[^']*'|[^\s,;]+)"#,
        )
        .expect("valid assignment regex")
    })
}

fn db_url_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)://[^\s]+")
            .expect("valid db URL regex")
    })
}

fn jwt_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
            .expect("valid JWT regex")
    })
}

fn url_userinfo_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
            .expect("valid URL userinfo regex")
    })
}

fn token_query_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)([?&](?:access_token|api[_-]?key|apikey|password|secret|token)=)([^&\s]+)")
            .expect("valid token query regex")
    })
}

fn runner_context_res() -> &'static [Regex] {
    static RES: OnceLock<Vec<Regex>> = OnceLock::new();
    RES.get_or_init(|| {
        vec![
            Regex::new(
                r"(?s)<!--\s*(?:FORAGER|AOE):RUNNER_CONTEXT_BEGIN\s*-->.*?<!--\s*(?:FORAGER|AOE):RUNNER_CONTEXT_END\s*-->",
            )
            .expect("valid runner context regex"),
            Regex::new(
                r"(?s)\[\[(?:FORAGER|AOE)_RUNNER_CONTEXT_BEGIN\]\].*?\[\[(?:FORAGER|AOE)_RUNNER_CONTEXT_END\]\]",
            )
                .expect("valid runner context regex"),
            Regex::new(r"(?s)<runner_context\b[^>]*>.*?</runner_context>")
                .expect("valid runner context regex"),
            Regex::new(r"(?s)```(?:forager|a[o]e)-runner-context\s*.*?```")
                .expect("valid runner context regex"),
        ]
    })
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct RedactionOutcome {
    pub text: String,
    pub changed: bool,
    pub runner_context_removed: usize,
    pub secrets_redacted: usize,
}

impl RedactionOutcome {
    fn from_text(text: String) -> Self {
        Self {
            text,
            changed: false,
            runner_context_removed: 0,
            secrets_redacted: 0,
        }
    }

    fn add_runner_context(&mut self, count: usize) {
        self.runner_context_removed += count;
        self.changed |= count > 0;
    }

    fn add_secret_redactions(&mut self, count: usize) {
        self.secrets_redacted += count;
        self.changed |= count > 0;
    }
}

/// Remove runner-only context blocks before text is shown to an operator.
pub fn strip_runner_context(input: &str) -> String {
    strip_runner_context_with_report(input).text
}

/// Remove runner-only context blocks and report how much context was removed.
pub fn strip_runner_context_with_report(input: &str) -> RedactionOutcome {
    let mut output = input.to_string();
    let mut removed = 0;
    for re in runner_context_res() {
        let count = re.find_iter(&output).count();
        removed += count;
        output = re
            .replace_all(&output, "[internal context omitted]")
            .to_string();
    }
    let mut outcome = RedactionOutcome::from_text(output);
    outcome.add_runner_context(removed);
    outcome
}

/// Force-redact known secret formats in audit rows, notifications, logs, and errors.
pub fn force_redact(input: &str) -> String {
    force_redact_with_report(input).text
}

/// Force-redact known secret formats and report how many replacements were made.
pub fn force_redact_with_report(input: &str) -> RedactionOutcome {
    let mut output = input.to_string();
    let mut redacted = 0;

    (output, redacted) =
        replace_counted(output, private_key_re(), "[REDACTED_PRIVATE_KEY]", redacted);
    (output, redacted) = replace_counted(
        output,
        telegram_token_re(),
        "[REDACTED_TELEGRAM_TOKEN]",
        redacted,
    );
    (output, redacted) = replace_counted(output, bearer_re(), "Bearer [REDACTED]", redacted);
    (output, redacted) =
        replace_counted(output, api_key_prefix_re(), "[REDACTED_API_KEY]", redacted);
    (output, redacted) = replace_assignments(output, redacted);
    (output, redacted) = replace_counted(output, db_url_re(), "[REDACTED_DB_URL]", redacted);
    (output, redacted) = replace_counted(output, jwt_re(), "[REDACTED_JWT]", redacted);
    (output, redacted) = replace_counted(output, url_userinfo_re(), "$1[REDACTED]@", redacted);
    (output, redacted) = replace_token_queries(output, redacted);

    let mut outcome = RedactionOutcome::from_text(output);
    outcome.add_secret_redactions(redacted);
    outcome
}

/// Apply context fencing and secret redaction for operator-facing text.
pub fn operator_safe_text(input: &str) -> String {
    operator_safe_report(input).text
}

/// Apply context fencing and secret redaction, returning a report for audit surfaces.
pub fn operator_safe_report(input: &str) -> RedactionOutcome {
    let stripped = strip_runner_context_with_report(input);
    let redacted = force_redact_with_report(&stripped.text);
    RedactionOutcome {
        text: redacted.text,
        changed: stripped.changed || redacted.changed,
        runner_context_removed: stripped.runner_context_removed,
        secrets_redacted: redacted.secrets_redacted,
    }
}

fn replace_counted(
    input: String,
    re: &Regex,
    replacement: &str,
    prior_count: usize,
) -> (String, usize) {
    let count = re.find_iter(&input).count();
    if count == 0 {
        return (input, prior_count);
    }
    (
        re.replace_all(&input, replacement).to_string(),
        prior_count + count,
    )
}

fn replace_assignments(input: String, prior_count: usize) -> (String, usize) {
    let mut count = 0;
    let output = assignment_re()
        .replace_all(&input, |caps: &Captures| {
            let value = caps.get(2).map_or("", |m| m.as_str());
            if value == "[REDACTED]" || value == "\"[REDACTED]\"" || value == "'[REDACTED]'" {
                return caps
                    .get(0)
                    .map_or_else(String::new, |m| m.as_str().to_string());
            }
            count += 1;
            format!(
                "{}=[REDACTED]",
                caps.get(1).map_or("secret", |m| m.as_str())
            )
        })
        .to_string();
    (output, prior_count + count)
}

fn replace_token_queries(input: String, prior_count: usize) -> (String, usize) {
    let mut count = 0;
    let output = token_query_re()
        .replace_all(&input, |caps: &Captures| {
            let value = caps.get(2).map_or("", |m| m.as_str());
            if value == "[REDACTED]" {
                return caps
                    .get(0)
                    .map_or_else(String::new, |m| m.as_str().to_string());
            }
            count += 1;
            format!(
                "{}[REDACTED]",
                caps.get(1).map_or("?token=", |m| m.as_str())
            )
        })
        .to_string();
    (output, prior_count + count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_representative_secrets() {
        let input = "Authorization: Bearer abcdefghijklmnop\n\
            token = ghp_abcdefghijklmnopqrstuvwxyz123456\n\
            db=postgres://user:pass@localhost/db\n\
            bot=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc\n\
            jwt=aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc\n\
            https://user:pass@example.com/path?access_token=secret123";

        let output = force_redact(input);

        assert!(!output.contains("abcdefghijklmnop"));
        assert!(!output.contains("ghp_abcdefghijklmnopqrstuvwxyz123456"));
        assert!(!output.contains("postgres://user:pass"));
        assert!(!output.contains("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc"));
        assert!(!output.contains("aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc"));
        assert!(!output.contains("user:pass@example.com"));
        assert!(!output.contains("secret123"));
    }

    #[test]
    fn strips_runner_only_context_before_operator_output() {
        let input = "visible\n<!-- FORAGER:RUNNER_CONTEXT_BEGIN -->hidden<!-- FORAGER:RUNNER_CONTEXT_END -->\nshown";
        let output = operator_safe_text(input);
        assert!(output.contains("visible"));
        assert!(output.contains("shown"));
        assert!(!output.contains("hidden"));
    }

    #[test]
    fn strips_legacy_runner_only_context_before_operator_output() {
        let input =
            "visible\n<!-- AOE:RUNNER_CONTEXT_BEGIN -->hidden<!-- AOE:RUNNER_CONTEXT_END -->\nshown";
        let output = operator_safe_text(input);
        assert!(output.contains("visible"));
        assert!(output.contains("shown"));
        assert!(!output.contains("hidden"));
    }

    #[test]
    fn reports_context_and_secret_redactions() {
        let input = "visible\n<!-- FORAGER:RUNNER_CONTEXT_BEGIN -->hidden<!-- FORAGER:RUNNER_CONTEXT_END -->\n\
            token=sk-secretsecretsecretsecret\n\
            https://example.com/path?access_token=secret123";

        let output = operator_safe_report(input);

        assert!(output.changed);
        assert_eq!(output.runner_context_removed, 1);
        assert!(output.secrets_redacted >= 2);
        assert!(!output.text.contains("hidden"));
        assert!(!output.text.contains("sk-secret"));
        assert!(!output.text.contains("secret123"));
    }

    #[test]
    fn redaction_report_is_idempotent_for_sanitized_text() {
        let input = "token=sk-secretsecretsecretsecret https://example.com?token=secret";
        let first = operator_safe_report(input);
        let second = operator_safe_report(&first.text);

        assert!(first.changed);
        assert!(!second.changed);
        assert_eq!(second.runner_context_removed, 0);
        assert_eq!(second.secrets_redacted, 0);
        assert_eq!(first.text, second.text);
    }
}
