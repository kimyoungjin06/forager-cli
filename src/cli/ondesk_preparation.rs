//! Owning-plan preparation never loads a profile or calls a model.

use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::{ensure, Context, Result};
use clap::Args;

use crate::offdesk::workflow::{offdesk_preparation, ondesk_plan_adoption};

#[derive(Args)]
pub struct PrepareOffdeskArgs {
    /// Explicit owning plans in PROJECT_KEY=PATH form; one source per project
    #[arg(long = "source", required = true)]
    sources: Vec<String>,
    /// Explicit selections in PROJECT_KEY/TASK_ID form
    #[arg(long = "task", required = true)]
    tasks: Vec<String>,
    /// Optional exact source versions in PROJECT_KEY=SHA256 form
    #[arg(long = "expected-source-sha256")]
    expected: Vec<String>,
}

fn split_pair(value: &str, separator: char) -> Result<(&str, &str)> {
    let (key, value) = value
        .split_once(separator)
        .context("expected a project-scoped pair")?;
    ensure!(
        !key.trim().is_empty() && key.trim() == key && !value.trim().is_empty(),
        "nonempty project and value required"
    );
    Ok((key, value))
}

pub fn run(args: PrepareOffdeskArgs) -> Result<()> {
    ensure!(
        args.sources.len() <= 16 && args.tasks.len() <= 128,
        "preparation selection exceeds limit"
    );
    let mut expected = BTreeMap::new();
    for value in &args.expected {
        let (key, hash) = split_pair(value, '=')?;
        ensure!(
            hash.len() == 64
                && hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "invalid expected source hash"
        );
        ensure!(
            expected.insert(key, hash).is_none(),
            "duplicate expected source hash"
        );
    }
    let mut loaded = Vec::new();
    for value in &args.sources {
        let (key, path) = split_pair(value, '=')?;
        ensure!(
            !loaded.iter().any(|(project, _, _)| *project == key),
            "duplicate project source"
        );
        let path = PathBuf::from(path);
        let path = if path.is_absolute() {
            path
        } else {
            std::env::current_dir()?.join(path)
        };
        let source = ondesk_plan_adoption::read_source(&path)?;
        ensure!(
            loaded
                .iter()
                .map(|(_, _, body): &(&str, String, String)| body.len())
                .sum::<usize>()
                + source.len()
                <= 4 * 1024 * 1024,
            "preparation sources exceed byte budget"
        );
        loaded.push((
            key,
            path.to_str()
                .context("owning source path must be UTF-8")?
                .to_string(),
            source,
        ));
    }
    ensure!(
        expected
            .keys()
            .all(|key| loaded.iter().any(|(project, _, _)| project == key)),
        "expected source project was not selected"
    );
    let sources = loaded
        .iter()
        .map(
            |(project, path, source)| offdesk_preparation::PreparationSource {
                project_key: project,
                path,
                source,
            },
        )
        .collect::<Vec<_>>();
    let selected = args
        .tasks
        .iter()
        .map(|value| {
            let (project, task) = split_pair(value, '/')?;
            Ok((project.to_string(), task.to_string()))
        })
        .collect::<Result<Vec<_>>>()?;
    let report = offdesk_preparation::prepare(&sources, &selected)?;
    for version in &report.sources {
        if let Some(hash) = expected.get(version.project_key.as_str()) {
            ensure!(
                *hash == version.source_sha256,
                "owning source version changed"
            );
        }
    }
    for (_, path, source) in &loaded {
        ensure!(
            ondesk_plan_adoption::read_source(&PathBuf::from(path))? == *source,
            "owning source changed during preparation"
        );
    }
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}
