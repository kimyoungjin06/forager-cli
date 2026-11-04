//! Bind the existing weekly usage guard to one immutable shift selection.

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use super::shift_runtime::ProfileSourceBoundary;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WeeklyUsageBinding {
    pub schema: String,
    pub state_file: String,
    pub sample_files: BTreeMap<String, String>,
    pub max_age_seconds: u64,
    pub contract: Value,
}

fn leaf(name: &str) -> bool {
    name.starts_with("offdesk_weekly_")
        && name.ends_with(".json")
        && name.len() <= 128
        && !name.contains("..")
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}

impl WeeklyUsageBinding {
    pub fn validate(&self, stops_at: DateTime<Utc>) -> Result<()> {
        ensure!(
            self.schema == "offdesk_shift_weekly_usage.v1",
            "unknown weekly usage binding"
        );
        ensure!(
            leaf(&self.state_file),
            "weekly state must be a profile-local offdesk_weekly_*.json file"
        );
        ensure!(
            (1..=300).contains(&self.max_age_seconds),
            "weekly sample freshness must be 1 to 300 seconds"
        );
        let baselines = self.contract["baselines"]
            .as_object()
            .context("weekly baselines required")?;
        ensure!(
            !baselines.is_empty() && baselines.len() <= 2,
            "weekly provider scope required"
        );
        ensure!(
            self.contract["schema"] == "offdesk_weekly_usage_budget.v1",
            "invalid weekly budget schema"
        );
        let deadline = self.contract["deadline"]
            .as_f64()
            .context("weekly deadline required")?;
        ensure!(
            deadline.is_finite() && deadline >= stops_at.timestamp_millis() as f64 / 1000.0,
            "weekly deadline must cover the shift window"
        );
        let limit = self.contract["limit_percentage_points"]
            .as_f64()
            .context("weekly limit required")?;
        ensure!(
            limit.is_finite() && limit > 0.0 && limit <= 100.0,
            "invalid weekly percentage-point limit"
        );
        if let Some(nightly) = self.contract.get("nightly") {
            ensure!(
                nightly.is_object()
                    && ((nightly["schema"] == "offdesk_nightly_budget.v1" && limit == 5.0)
                        || (nightly["schema"] == "offdesk_nightly_budget.v2"
                            && limit == 10.0
                            && nightly["allowance_amendment"]["schema"]
                                == "offdesk_nightly_allowance_amendment.v1"
                            && nightly["allowance_amendment"]["from_percentage_points"] == 5
                            && nightly["allowance_amendment"]["to_percentage_points"] == 10
                            && nightly["allowance_amendment"]["providers"] == json!(["gpt"]))),
                "invalid default or explicitly amended nightly budget"
            );
        }
        ensure!(
            self.sample_files.len() == baselines.len(),
            "weekly sample scope differs from baselines"
        );
        let mut names = std::collections::HashSet::from([self.state_file.as_str()]);
        for (provider, file) in &self.sample_files {
            ensure!(
                matches!(provider.as_str(), "gpt" | "claude") && baselines.contains_key(provider),
                "unknown weekly provider scope"
            );
            ensure!(
                leaf(file) && names.insert(file),
                "weekly sample paths must be distinct profile-local files"
            );
        }
        ensure!(
            serde_json::to_vec(&self.contract)?.len() <= 65536,
            "weekly contract too large"
        );
        Ok(())
    }
}

struct UsageLock(File);

impl Drop for UsageLock {
    fn drop(&mut self) {
        // A concurrent fork can briefly inherit the open file description until
        // exec. Closing only our descriptor can retain that flock in the child.
        // Release the operation's authority explicitly before closing it.
        let _ = FileExt::unlock(&self.0);
    }
}

pub(crate) struct WeeklyUsageCheck {
    pub verdict: Value,
    sources: ProfileSourceBoundary,
    _lock: Option<UsageLock>,
}

impl WeeklyUsageCheck {
    pub fn ensure_current(&self) -> Result<()> {
        self.sources.ensure_current()
    }
}

fn contract(state: &Value) -> Value {
    let mut value = json!({"schema":state["schema"],"deadline":state["deadline"],
        "baselines":state["baselines"],"limit_percentage_points":state["limit_percentage_points"]});
    if let Some(nightly) = state.get("nightly") {
        value["nightly"] = nightly.clone();
    }
    value
}

pub(crate) fn bind(
    root: &Path,
    state_path: &Path,
    samples: BTreeMap<String, PathBuf>,
    max_age_seconds: u64,
    stops_at: DateTime<Utc>,
    now: DateTime<Utc>,
) -> Result<WeeklyUsageBinding> {
    fn filename(root: &Path, path: &Path) -> Result<String> {
        ensure!(
            path.parent() == Some(root),
            "weekly inputs must be directly inside the selected profile"
        );
        Ok(path
            .file_name()
            .and_then(|p| p.to_str())
            .context("invalid weekly filename")?
            .to_string())
    }
    let mut sources = ProfileSourceBoundary::open(root.to_path_buf())?;
    let state: Value = sources.read_json(state_path)?;
    let binding = WeeklyUsageBinding {
        schema: "offdesk_shift_weekly_usage.v1".into(),
        state_file: filename(root, state_path)?,
        sample_files: samples
            .into_iter()
            .map(|(p, path)| Ok((p, filename(root, &path)?)))
            .collect::<Result<_>>()?,
        max_age_seconds,
        contract: contract(&state),
    };
    binding.validate(stops_at)?;
    let checked = check(root, &binding, now, false)?;
    ensure!(
        checked.verdict["allowed"] == true,
        "weekly usage is held: {}",
        checked.verdict["reasons"]
    );
    sources.ensure_current()?;
    checked.ensure_current()?;
    Ok(binding)
}

pub(crate) fn check(
    root: &Path,
    binding: &WeeklyUsageBinding,
    now: DateTime<Utc>,
    persist: bool,
) -> Result<WeeklyUsageCheck> {
    let mut sources = ProfileSourceBoundary::open(root.to_path_buf())?;
    let anchored = crate::process::open_bounded_root(root)?;
    ensure!(
        crate::process::filesystem_file_identity(&anchored.metadata()?)
            == Some(sources.root_identity()),
        "weekly profile root changed"
    );
    let lock = if persist {
        let name = format!("{}.lock", binding.state_file);
        let file =
            crate::process::open_or_create_bounded_regular_file(&anchored, Path::new(&name))?;
        ensure!(
            crate::process::filesystem_link_count(&file.metadata()?).unwrap_or(1) == 1,
            "weekly lock has aliases"
        );
        file.try_lock_exclusive()
            .context("weekly usage guard busy")?;
        let lock = UsageLock(file);
        sources.read_bytes(&root.join(&name))?;
        Some(lock)
    } else {
        None
    };
    let state_path = root.join(&binding.state_file);
    let state: Value = sources.read_json_ephemeral(&state_path)?;
    ensure!(
        contract(&state) == binding.contract,
        "approved weekly budget identity changed"
    );
    let mut samples = BTreeMap::new();
    for (provider, name) in &binding.sample_files {
        // Missing/malformed samples are unknown, never zero. Read failures still
        // latch through the runtime shift hold without writing an unsafe path.
        let sample: Value = sources.read_json(&root.join(name))?;
        samples.insert(provider, sample);
    }
    let input = json!({"contract":binding.contract,"state":state,"samples":samples,
        "now":now.timestamp_millis() as f64 / 1000.0,"max_age":binding.max_age_seconds});
    ensure!(
        serde_json::to_vec(&input)?.len() <= 256 * 1024,
        "weekly snapshots exceed evaluation budget"
    );
    // Reuse the packaged evaluator, not a caller-supplied executable or new
    // provider poller. This action reads stdin and never touches files/network.
    let mut child = Command::new("python3")
        .args([
            "-I",
            "-S",
            "-c",
            include_str!("../../scripts/offdesk_usage_guard.py"),
            "evaluate-stdin",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .context("weekly usage evaluator unavailable")?;
    child
        .stdin
        .take()
        .context("weekly evaluator stdin unavailable")?
        .write_all(&serde_json::to_vec(&input)?)?;
    let output = child.wait_with_output()?;
    ensure!(
        output.status.success() && output.stdout.len() <= 256 * 1024,
        "weekly usage evaluator refused input"
    );
    let mut evaluated: Value = serde_json::from_slice(&output.stdout)?;
    evaluated["verdict"]
        .as_object_mut()
        .context("weekly verdict missing")?
        .remove("observed_at");
    evaluated["verdict"]["sample_sha256"] = serde_json::to_value(
        samples
            .iter()
            .map(|(provider, sample)| {
                use sha2::{Digest, Sha256};
                Ok((
                    provider.to_string(),
                    format!("{:x}", Sha256::digest(serde_json::to_vec(sample)?)),
                ))
            })
            .collect::<Result<BTreeMap<_, _>>>()?,
    )?;
    ensure!(
        evaluated["verdict"]["allowed"].is_boolean(),
        "weekly evaluator lacks a verdict"
    );
    sources.ensure_current()?;
    if persist {
        // The Python guard uses this same state and lock. Never reset a baseline
        // or held flag; reject a noncooperating write before replacing state.
        let observed: Value = sources.read_json_ephemeral(&state_path)?;
        ensure!(observed == state, "weekly state changed before commit");
        let temporary = PathBuf::from(format!(".weekly-{}.tmp", uuid::Uuid::new_v4()));
        let result = (|| -> Result<()> {
            let mut file = crate::process::create_new_bounded_regular_file(&anchored, &temporary)?;
            file.write_all(&serde_json::to_vec_pretty(&evaluated["state"])?)?;
            file.sync_all()?;
            sources.ensure_current()?;
            crate::process::replace_bounded_root_file(
                &anchored,
                &temporary,
                Path::new(&binding.state_file),
            )?;
            anchored.sync_all()?;
            sources.ensure_current()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = crate::process::remove_bounded_root_file(&anchored, &temporary);
        }
        result?;
    }
    Ok(WeeklyUsageCheck {
        verdict: evaluated["verdict"].clone(),
        sources,
        _lock: lock,
    })
}

pub(crate) fn projection(
    root: &Path,
    binding: Option<&WeeklyUsageBinding>,
    now: DateTime<Utc>,
) -> Value {
    match binding {
        None => json!({"status":"not_bound","allowed":true,"runtime_weekly_limit":false}),
        Some(binding) => {
            let verdict = match check(root, binding, now, false) {
                Ok(check) => check.verdict,
                Err(_) => json!({"allowed":false,"reasons":["weekly_usage_unknown_or_changed"]}),
            };
            json!({"status":if verdict["allowed"] == true {"ready"} else {"held"},"allowed":verdict["allowed"],
                "binding":binding,"verdict":verdict,"runtime_weekly_limit":true,
                "enforcement":"task_and_wiki_dispatch_and_reservation_admission_only",
                "inflight_call_can_overshoot":true,"inflight_task_can_overshoot":true,"account_usage_includes_other_sessions":true,
                "automatic_provider_polling":false,"running_tasks_killed":false,
                "usd_or_token_ceiling":false})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn fixture(root: &Path, now: DateTime<Utc>) -> Result<WeeklyUsageBinding> {
        let stamp = now.timestamp_millis() as f64 / 1000.0;
        let mut baselines = json!({});
        let mut high = json!({});
        let mut samples = BTreeMap::new();
        for provider in ["gpt", "claude"] {
            let sample = json!({"schema":"offdesk_weekly_usage.v1","provider":provider,
                "account":format!("fixture-{provider}"),"observed_at":stamp,
                "weekly_meters":{"week":{"used_percent":20,"resets_at":stamp+604800.0}}});
            baselines[provider] = sample.clone();
            high[provider] = json!({"week":20});
            let path = root.join(format!("offdesk_weekly_{provider}.json"));
            fs::write(&path, serde_json::to_vec(&sample)?)?;
            samples.insert(provider.into(), path);
        }
        let state_path = root.join("offdesk_weekly_state.json");
        fs::write(
            &state_path,
            serde_json::to_vec(&json!({"schema":"offdesk_weekly_usage_budget.v1",
            "deadline":stamp+7200.0,"baselines":baselines,"limit_percentage_points":10,
            "held":false,"high_water":high}))?,
        )?;
        bind(
            root,
            &state_path,
            samples,
            300,
            now + chrono::Duration::hours(1),
            now,
        )
    }

    fn change(root: &Path, provider: &str, mutate: impl FnOnce(&mut Value)) -> Result<()> {
        let path = root.join(format!("offdesk_weekly_{provider}.json"));
        let mut sample: Value = serde_json::from_slice(&fs::read(&path)?)?;
        mutate(&mut sample);
        fs::write(path, serde_json::to_vec(&sample)?)?;
        Ok(())
    }

    #[test]
    fn difficult_timestamps_survive_rust_python_json_roundtrips() -> Result<()> {
        for literal in [
            "1793666848.1939325",
            "1793666848.1939323",
            "1793666848.3041325",
        ] {
            let mut value: Value = serde_json::from_str(&format!(
                "{{\"baselines\":{{\"gpt\":{{\"observed_at\":{literal}}}}}}}"
            ))?;
            for _ in 0..3 {
                assert_eq!(
                    serde_json::to_string(&value["baselines"]["gpt"]["observed_at"])?,
                    literal
                );
                let mut child = Command::new("python3")
                    .args(["-I", "-S", "-c", "import json,sys; json.dump(json.load(sys.stdin),sys.stdout,allow_nan=False)"])
                    .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn()?;
                child
                    .stdin
                    .take()
                    .unwrap()
                    .write_all(&serde_json::to_vec(&value)?)?;
                let output = child.wait_with_output()?;
                ensure!(output.status.success(), "Python roundtrip failed");
                value = serde_json::from_slice(&output.stdout)?;
            }
            assert_eq!(
                serde_json::to_string(&value["baselines"]["gpt"]["observed_at"])?,
                literal
            );
        }
        Ok(())
    }

    #[test]
    fn persisted_guard_checks_keep_exact_contract_while_high_water_increases() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let initial = fixture(d.path(), now)?;
        let path = d.path().join(&initial.state_file);
        let mut state: Value = serde_json::from_slice(&fs::read(&path)?)?;
        let seconds = (now.timestamp() - 2) as f64;
        state["baselines"]["gpt"]["observed_at"] = json!(seconds + 0.1939325);
        state["baselines"]["claude"]["observed_at"] = json!(seconds + 0.3041325);
        fs::write(&path, serde_json::to_vec(&state)?)?;
        let bound = bind(
            d.path(),
            &path,
            initial
                .sample_files
                .iter()
                .map(|(p, f)| (p.clone(), d.path().join(f)))
                .collect(),
            300,
            now + chrono::Duration::hours(1),
            now,
        )?;
        let expected = serde_json::to_vec(&bound.contract)?;
        for used in 21..24 {
            change(d.path(), "gpt", |v| {
                v["weekly_meters"]["week"]["used_percent"] = json!(used)
            })?;
            assert_eq!(
                check(d.path(), &bound, Utc::now(), true)?.verdict["allowed"],
                true
            );
            let returned: Value = serde_json::from_slice(&fs::read(&path)?)?;
            assert_eq!(serde_json::to_vec(&contract(&returned))?, expected);
            assert_eq!(returned["high_water"]["gpt"]["week"], used);
        }
        Ok(())
    }

    #[test]
    fn lossless_parsing_does_not_tolerate_real_contract_changes() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let bound = fixture(d.path(), now)?;
        let path = d.path().join(&bound.state_file);
        let original: Value = serde_json::from_slice(&fs::read(&path)?)?;
        for scenario in [
            "timestamp_ulp",
            "baseline",
            "account",
            "reset",
            "deadline",
            "allowance",
        ] {
            let mut state = original.clone();
            match scenario {
                "timestamp_ulp" => {
                    let observed = state["baselines"]["gpt"]["observed_at"].as_f64().unwrap();
                    state["baselines"]["gpt"]["observed_at"] =
                        json!(f64::from_bits(observed.to_bits() + 1));
                }
                "baseline" => {
                    state["baselines"]["gpt"]["weekly_meters"]["week"]["used_percent"] = json!(19)
                }
                "account" => state["baselines"]["gpt"]["account"] = json!("other"),
                "reset" => {
                    state["baselines"]["gpt"]["weekly_meters"]["week"]["resets_at"] = json!(1)
                }
                "deadline" => state["deadline"] = json!(1),
                _ => state["limit_percentage_points"] = json!(11),
            }
            let before = serde_json::to_vec(&state)?;
            fs::write(&path, &before)?;
            assert!(check(d.path(), &bound, now, true).is_err(), "{scenario}");
            assert_eq!(fs::read(&path)?, before);
        }
        Ok(())
    }

    #[test]
    fn nightly_start_binding_survives_resume_and_stops_at_five_points() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let legacy = fixture(d.path(), now)?;
        let initialize = || {
            Command::new("python3")
                .args([
                    "-I",
                    "-S",
                    "-c",
                    include_str!("../../scripts/offdesk_usage_guard.py"),
                    "init-nightly",
                    "--state-dir",
                ])
                .arg(d.path())
                .arg("--gpt-sample")
                .arg(d.path().join(&legacy.sample_files["gpt"]))
                .arg("--claude-sample")
                .arg(d.path().join(&legacy.sample_files["claude"]))
                .output()
        };
        let first = initialize()?;
        ensure!(first.status.success(), "nightly init failed: {:?}", first);
        let prepared: Value = serde_json::from_slice(&first.stdout)?;
        let path = PathBuf::from(prepared["state_path"].as_str().unwrap());
        let resumed = initialize()?;
        ensure!(resumed.status.success(), "nightly resume failed");
        let second: Value = serde_json::from_slice(&resumed.stdout)?;
        assert_eq!(second["reused"], true);
        assert_eq!(prepared["nightly"], second["nightly"]);
        let current = Utc::now();
        let stops =
            DateTime::from_timestamp(prepared["nightly"]["stops_at"].as_f64().unwrap() as i64, 0)
                .unwrap();
        let binding = bind(
            d.path(),
            &path,
            legacy
                .sample_files
                .iter()
                .map(|(p, f)| (p.clone(), d.path().join(f)))
                .collect(),
            300,
            stops.min(current + chrono::Duration::minutes(15)),
            current,
        )?;
        assert_eq!(binding.contract["limit_percentage_points"], 5);
        assert_eq!(binding.contract["nightly"], prepared["nightly"]);
        change(d.path(), "gpt", |v| {
            v["weekly_meters"]["week"]["used_percent"] = json!(24)
        })?;
        assert_eq!(
            check(d.path(), &binding, Utc::now(), true)?.verdict["allowed"],
            true
        );
        change(d.path(), "gpt", |v| {
            v["weekly_meters"]["week"]["used_percent"] = json!(25)
        })?;
        assert_eq!(
            check(d.path(), &binding, Utc::now(), true)?.verdict["allowed"],
            false
        );
        let mut changed: Value = serde_json::from_slice(&fs::read(&path)?)?;
        changed["nightly"]["started_at"] = json!(0);
        fs::write(&path, serde_json::to_vec(&changed)?)?;
        assert!(check(d.path(), &binding, Utc::now(), true).is_err());
        Ok(())
    }

    #[test]
    fn explicitly_amended_night_binds_original_baseline_and_persists_ten_point_limit() -> Result<()>
    {
        let d = tempdir()?;
        let script = format!(
            "{}\n{}",
            include_str!("../../scripts/offdesk_usage_guard.py")
                .replace("if __name__ == \"__main__\":", "if False:",),
            r#"
root = Path(sys.argv[1]); now = time.time()
sample_path = root / 'offdesk_weekly_gpt.json'
atomic_json(sample_path, normalize('gpt', 'fixture', {'week': (20, now + 604800)}, now))
first = initialize_nightly(root, {'gpt': sample_path}, now)
atomic_json(sample_path, normalize('gpt', 'fixture', {'week': (25, now + 604800)}, now))
initialize_nightly(root, {'gpt': sample_path}, now)
path = Path(first['state_path']); state, state_sha = _amendment_json(path)
request = root / 'request.json'
atomic_json(request, {'schema': 'offdesk_nightly_allowance_request.v1', 'request_id': 'rust-fixture',
    'authority_ref': 'explicit-user-fixture', 'state_path': str(path), 'state_sha256': state_sha,
    'status_sha256': _amendment_json(Path(str(path) + '.status.json'))[1],
    'contract_sha256': json_digest(budget_contract(state)), 'from_percentage_points': 5, 'to_percentage_points': 10})
amend_nightly(path, {'gpt': sample_path}, request, root / 'receipt.json', now)
print(str(path))
"#,
        );
        let output = Command::new("python3")
            .args(["-I", "-S", "-c", &script])
            .arg(d.path())
            .output()?;
        ensure!(
            output.status.success(),
            "amendment fixture failed: {:?}",
            output
        );
        let path = PathBuf::from(String::from_utf8(output.stdout)?.trim());
        let current = Utc::now();
        let binding = bind(
            d.path(),
            &path,
            [("gpt".into(), d.path().join("offdesk_weekly_gpt.json"))].into(),
            300,
            current + chrono::Duration::minutes(15),
            current,
        )?;
        assert_eq!(binding.contract["limit_percentage_points"], 10);
        assert_eq!(
            binding.contract["nightly"]["schema"],
            "offdesk_nightly_budget.v2"
        );
        assert_eq!(
            check(d.path(), &binding, Utc::now(), true)?.verdict["allowed"],
            true
        );
        let persisted: Value = serde_json::from_slice(&fs::read(&path)?)?;
        assert_eq!(contract(&persisted), binding.contract);
        change(d.path(), "gpt", |v| {
            v["weekly_meters"]["week"]["used_percent"] = json!(30)
        })?;
        assert_eq!(
            check(d.path(), &binding, Utc::now(), true)?.verdict["allowed"],
            false
        );
        Ok(())
    }

    #[test]
    fn readonly_projection_is_repeatable_and_does_not_create_lock_or_update_state() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let b = fixture(d.path(), now)?;
        let before = fs::read(d.path().join(&b.state_file))?;
        let first = projection(d.path(), Some(&b), now);
        assert_eq!(
            first,
            projection(d.path(), Some(&b), now + chrono::Duration::seconds(1))
        );
        assert_eq!(before, fs::read(d.path().join(&b.state_file))?);
        assert_eq!(fs::read_dir(d.path())?.count(), 3);
        assert_eq!(first["runtime_weekly_limit"], true);
        Ok(())
    }

    #[test]
    fn each_provider_limit_latches_and_cannot_be_cleared_by_a_fresh_sample() -> Result<()> {
        for provider in ["gpt", "claude"] {
            let d = tempdir()?;
            let now = Utc::now();
            let b = fixture(d.path(), now)?;
            change(d.path(), provider, |v| {
                v["weekly_meters"]["week"]["used_percent"] = json!(29)
            })?;
            assert_eq!(check(d.path(), &b, now, true)?.verdict["allowed"], true);
            change(d.path(), provider, |v| {
                v["weekly_meters"]["week"]["used_percent"] = json!(30)
            })?;
            assert_eq!(check(d.path(), &b, now, true)?.verdict["allowed"], false);
            change(d.path(), provider, |v| {
                v["weekly_meters"]["week"]["used_percent"] = json!(31)
            })?;
            let later = check(d.path(), &b, now, true)?;
            assert_eq!(later.verdict["allowed"], false);
            assert!(later.verdict["reasons"]
                .as_array()
                .unwrap()
                .contains(&json!("previous_hold_requires_review")));
        }
        Ok(())
    }

    #[test]
    fn stale_future_account_reset_meter_change_and_decrease_hold() -> Result<()> {
        for scenario in ["stale", "future", "account", "reset", "meter", "decrease"] {
            let d = tempdir()?;
            let now = Utc::now();
            let b = fixture(d.path(), now)?;
            change(d.path(), "gpt", |v| match scenario {
                "stale" => v["observed_at"] = json!(now.timestamp() - 301),
                "future" => v["observed_at"] = json!(now.timestamp() + 60),
                "account" => v["account"] = json!("other"),
                "reset" => v["weekly_meters"]["week"]["resets_at"] = json!(now.timestamp() + 1234),
                "meter" => v["weekly_meters"] = json!({}),
                _ => v["weekly_meters"]["week"]["used_percent"] = json!(19),
            })?;
            assert_eq!(
                check(d.path(), &b, now, true)?.verdict["allowed"],
                false,
                "{scenario}"
            );
        }
        Ok(())
    }

    #[test]
    fn native_second_jitter_preserves_bound_budget_and_large_drift_latches() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let initial = fixture(d.path(), now)?;
        let state_path = d.path().join(&initial.state_file);
        let mut state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
        let reset = now.timestamp() + 604800;
        let mut sample = state["baselines"]["gpt"].clone();
        sample["weekly_meters"] = json!({"codex:primary":{"used_percent":20,"resets_at":reset}});
        state["baselines"]["gpt"] = sample.clone();
        state["high_water"]["gpt"] = json!({"codex:primary":20});
        fs::write(&state_path, serde_json::to_vec(&state)?)?;
        fs::write(
            d.path().join(&initial.sample_files["gpt"]),
            serde_json::to_vec(&sample)?,
        )?;
        let bound = bind(
            d.path(),
            &state_path,
            initial
                .sample_files
                .iter()
                .map(|(k, v)| (k.clone(), d.path().join(v)))
                .collect(),
            300,
            now + chrono::Duration::hours(1),
            now,
        )?;
        for (offset, expected) in [(1, true), (2, false), (0, false)] {
            change(d.path(), "gpt", |value| {
                value["weekly_meters"]["codex:primary"]["resets_at"] = json!(reset + offset);
                value["weekly_meters"]["codex:primary"]["used_percent"] = json!(25);
            })?;
            let checked = check(d.path(), &bound, now, true)?;
            assert_eq!(checked.verdict["allowed"], expected);
        }
        let after: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
        assert_eq!(after["baselines"], bound.contract["baselines"]);
        assert_eq!(after["high_water"]["gpt"]["codex:primary"], 25);
        assert_eq!(after["held"], true);
        Ok(())
    }

    #[test]
    fn policy_tamper_missing_sample_and_active_guard_lock_refuse() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let b = fixture(d.path(), now)?;
        let held = check(d.path(), &b, now, true)?;
        assert!(check(d.path(), &b, now, true).is_err());
        drop(held);
        let mut state: Value = serde_json::from_slice(&fs::read(d.path().join(&b.state_file))?)?;
        state["limit_percentage_points"] = json!(11);
        fs::write(d.path().join(&b.state_file), serde_json::to_vec(&state)?)?;
        assert!(check(d.path(), &b, now, true).is_err());
        state["limit_percentage_points"] = json!(10);
        fs::write(d.path().join(&b.state_file), serde_json::to_vec(&state)?)?;
        fs::remove_file(d.path().join(&b.sample_files["claude"]))?;
        assert_eq!(projection(d.path(), Some(&b), now)["allowed"], false);
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn unsafe_sample_aliases_and_external_policy_paths_refuse_without_writes() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let mut b = fixture(d.path(), now)?;
        let sample = d.path().join(&b.sample_files["gpt"]);
        let target = d.path().join("original.json");
        fs::rename(&sample, &target)?;
        std::os::unix::fs::symlink(&target, &sample)?;
        let before = fs::read(d.path().join(&b.state_file))?;
        assert!(check(d.path(), &b, now, true).is_err());
        assert_eq!(before, fs::read(d.path().join(&b.state_file))?);
        b.state_file = "../offdesk_weekly_other.json".into();
        assert!(b.validate(now).is_err());
        Ok(())
    }
    #[test]
    fn changed_sample_between_check_and_reservation_invalidates_the_observation() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let b = fixture(d.path(), now)?;
        let checked = check(d.path(), &b, now, true)?;
        change(d.path(), "gpt", |v| {
            v["weekly_meters"]["week"]["used_percent"] = json!(30)
        })?;
        assert!(checked.ensure_current().is_err());
        Ok(())
    }
    #[test]
    fn inherited_descriptor_does_not_retain_completed_check_authority() -> Result<()> {
        let d = tempdir()?;
        let now = Utc::now();
        let b = fixture(d.path(), now)?;
        let first = check(d.path(), &b, now, true)?;
        let inherited = first._lock.as_ref().unwrap().0.try_clone()?;
        drop(first);
        let next = check(d.path(), &b, now, true)?;
        assert_eq!(next.verdict["allowed"], true);
        drop(next);
        drop(inherited);
        Ok(())
    }
}
