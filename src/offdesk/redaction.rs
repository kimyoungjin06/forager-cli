//! Redaction and context-fencing helpers for operator-facing output.

use regex::{Captures, Regex};
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
        Regex::new(r"(?i)([?&](?:access_token|api[_-]?key|apikey|password|secret|token)=)[^&\s]+")
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

/// Remove runner-only context blocks before text is shown to an operator.
pub fn strip_runner_context(input: &str) -> String {
    let mut output = input.to_string();
    for re in runner_context_res() {
        output = re
            .replace_all(&output, "[internal context omitted]")
            .to_string();
    }
    output
}

/// Force-redact known secret formats in audit rows, notifications, logs, and errors.
pub fn force_redact(input: &str) -> String {
    let mut output = input.to_string();
    output = private_key_re()
        .replace_all(&output, "[REDACTED_PRIVATE_KEY]")
        .to_string();
    output = telegram_token_re()
        .replace_all(&output, "[REDACTED_TELEGRAM_TOKEN]")
        .to_string();
    output = bearer_re()
        .replace_all(&output, "Bearer [REDACTED]")
        .to_string();
    output = api_key_prefix_re()
        .replace_all(&output, "[REDACTED_API_KEY]")
        .to_string();
    output = assignment_re()
        .replace_all(&output, |caps: &Captures| {
            format!(
                "{}=[REDACTED]",
                caps.get(1).map_or("secret", |m| m.as_str())
            )
        })
        .to_string();
    output = db_url_re()
        .replace_all(&output, "[REDACTED_DB_URL]")
        .to_string();
    output = jwt_re().replace_all(&output, "[REDACTED_JWT]").to_string();
    output = url_userinfo_re()
        .replace_all(&output, "$1[REDACTED]@")
        .to_string();
    token_query_re()
        .replace_all(&output, "$1[REDACTED]")
        .to_string()
}

/// Apply context fencing and secret redaction for operator-facing text.
pub fn operator_safe_text(input: &str) -> String {
    force_redact(&strip_runner_context(input))
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
}
