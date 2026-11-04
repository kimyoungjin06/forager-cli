use crate::cli::{self, Cli, Commands, LEGACY_BINARY_NAME, PRIMARY_BINARY_NAME};
use crate::migrations;
use crate::session::{load_config_read_only, normalize_profile_name};
use crate::tui;
use anyhow::Result;
use clap::{CommandFactory, Parser};
use clap_complete::generate;
use std::process::ExitCode;

/// Build the runtime shared by CLI and TUI entrypoints.
///
/// Short CLI calls must not allocate a worker for every CPU on a large host.
/// Keep the default pool small while preserving Tokio's explicit override.
pub fn build_cli_runtime() -> Result<tokio::runtime::Runtime> {
    let workers = match std::env::var_os("TOKIO_WORKER_THREADS") {
        Some(value) => value
            .to_str()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|workers| *workers > 0)
            .ok_or_else(|| anyhow::anyhow!("TOKIO_WORKER_THREADS must be a positive integer"))?,
        None => std::thread::available_parallelism()
            .map_or(1, std::num::NonZeroUsize::get)
            .min(2),
    };
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers)
        .enable_all()
        .build()
        .map_err(Into::into)
}

/// Run either executable with the same bounded runtime policy.
pub fn run_cli_with_runtime() -> ExitCode {
    match build_cli_runtime() {
        Ok(runtime) => runtime.block_on(run_cli_process()),
        Err(error) => {
            eprintln!("{}", render_operator_safe_terminal_error(&error));
            ExitCode::FAILURE
        }
    }
}

pub async fn run_cli() -> Result<()> {
    run_parsed_cli(Cli::parse()).await
}

pub async fn run_cli_process() -> ExitCode {
    // This launcher runs inside the newly allocated pane. It must never load
    // profile configuration, run migrations or execute repository hooks.
    let arguments = std::env::args_os().collect::<Vec<_>>();
    if arguments
        .get(1)
        .is_some_and(|arg| arg == crate::offdesk::read_policy_tmux::INTERNAL_ARGUMENT)
    {
        let result = (|| -> Result<u8> {
            anyhow::ensure!(
                arguments.len() == 4,
                "invalid restricted launcher arguments"
            );
            crate::offdesk::read_policy_tmux::run_helper(
                std::path::Path::new(&arguments[2]),
                arguments[3]
                    .to_str()
                    .ok_or_else(|| anyhow::anyhow!("invalid launch hash"))?,
            )
        })();
        return match result {
            Ok(code) => ExitCode::from(code),
            Err(error) => {
                eprintln!("{}", render_operator_safe_terminal_error(&error));
                ExitCode::FAILURE
            }
        };
    }
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error) => {
            let rendered = crate::offdesk::operator_safe_text(&error.to_string());
            if error.use_stderr() {
                eprint!("{rendered}");
            } else {
                print!("{rendered}");
            }
            return process_exit_code(error.exit_code());
        }
    };

    match run_parsed_cli(cli).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{}", render_operator_safe_terminal_error(&error));
            ExitCode::FAILURE
        }
    }
}

async fn run_parsed_cli(cli: Cli) -> Result<()> {
    if debug_logging_enabled() {
        tracing_subscriber::fmt()
            .with_env_filter("forager=debug")
            .init();
    }

    let explicit_profile = cli.profile.clone();
    let profile_independent = matches!(
        cli.command.as_ref(),
        Some(Commands::Ondesk {
            command: cli::ondesk::OndeskCommands::PlanInspect(_)
                | cli::ondesk::OndeskCommands::PrepareOffdesk(_)
                | cli::ondesk::OndeskCommands::PlanAdoptProposal(_)
                | cli::ondesk::OndeskCommands::PlanApply(_)
        })
    ) || matches!(
        cli.command.as_ref(),
        Some(Commands::Offdesk { command }) if command.is_profile_independent_read_only()
    ) || matches!(
        cli.command.as_ref(),
        Some(Commands::Project { command }) if matches!(command.as_ref(), cli::project::ProjectCommands::Inventory(_) | cli::project::ProjectCommands::PlanRouting(_))
    );
    let profile = if profile_independent {
        String::new()
    } else {
        let selected = explicit_profile
            .clone()
            .or_else(|| std::env::var("AGENT_OF_EMPIRES_PROFILE").ok())
            .or_else(configured_default_profile)
            .unwrap_or_default();
        normalize_profile_name(&selected)?
    };
    maybe_warn_legacy_alias(&cli);
    let _frontend = if matches!(
        cli.command.as_ref(),
        Some(Commands::Go(_))
            | Some(Commands::Session {
                command: cli::session::SessionCommands::Attach(_)
            })
    ) {
        Some(crate::maintenance::admit_frontend()?)
    } else {
        None
    };

    // Handle commands that don't need app data or migrations.
    // These work in read-only/sandboxed environments (e.g. Nix builds).
    match cli.command {
        Some(Commands::Completion { shell }) => {
            let command_name = completion_binary_name();
            let mut command = Cli::command().name(command_name);
            generate(shell, &mut command, command_name, &mut std::io::stdout());
            return Ok(());
        }
        Some(Commands::Init(args)) => return cli::init::run(args).await,
        Some(Commands::Doctor(args)) => {
            return cli::doctor::run(explicit_profile.as_deref(), args).await;
        }
        Some(Commands::Migrate { command }) => return cli::migrate::run(command).await,
        Some(Commands::Tmux { command }) => {
            use cli::tmux::TmuxCommands;
            return match command {
                TmuxCommands::Status(args) => cli::tmux::run_status(args),
            };
        }
        Some(Commands::Offdesk { command })
            if matches!(
                command.as_ref(),
                cli::offdesk::OffdeskCommands::DebugBundle(_)
            ) =>
        {
            return cli::offdesk::run(&profile, *command).await;
        }
        Some(Commands::Offdesk { command }) if command.is_migration_free_read_only() => {
            return cli::offdesk::run(&profile, *command).await;
        }
        Some(Commands::Project { command }) if command.is_ephemeral_document_observation() => {
            return cli::project::run(&profile, *command).await;
        }
        Some(Commands::Session { command }) if command.is_read_only_context_status() => {
            return cli::session::run(&profile, command).await;
        }
        Some(Commands::Ondesk { command }) if command.is_morning_return() => {
            return cli::ondesk::run(&profile, command).await;
        }
        Some(Commands::Ondesk {
            command:
                command @ (cli::ondesk::OndeskCommands::PlanInspect(_)
                | cli::ondesk::OndeskCommands::PrepareOffdesk(_)
                | cli::ondesk::OndeskCommands::PlanAdoptProposal(_)
                | cli::ondesk::OndeskCommands::PlanReviewProposal(_)
                | cli::ondesk::OndeskCommands::PlanApply(_)),
        }) => {
            return cli::ondesk::run(&profile, command).await;
        }
        Some(Commands::Sounds { command }) => return cli::sounds::run(command).await,
        Some(Commands::Uninstall(args)) => return cli::uninstall::run(args).await,
        _ => {}
    }

    // TUI mode handles migrations with a spinner; CLI runs them silently.
    if cli.command.is_some() {
        migrations::run_migrations()?;
    }

    match cli.command {
        Some(Commands::Add(args)) => cli::add::run(&profile, args).await,
        Some(Commands::Go(args)) => cli::go::run(&profile, args).await,
        Some(Commands::List(args)) => cli::list::run(&profile, args).await,
        Some(Commands::Remove(args)) => cli::remove::run(&profile, args).await,
        Some(Commands::Status(args)) => cli::status::run(&profile, args).await,
        Some(Commands::Session { command }) => cli::session::run(&profile, command).await,
        Some(Commands::Group { command }) => cli::group::run(&profile, command).await,
        Some(Commands::Profile { command }) => cli::profile::run(command).await,
        Some(Commands::Project { command }) => cli::project::run(&profile, *command).await,
        Some(Commands::Worktree { command }) => cli::worktree::run(&profile, command).await,
        Some(Commands::Offdesk { command }) => cli::offdesk::run(&profile, *command).await,
        Some(Commands::Ondesk { command }) => cli::ondesk::run(&profile, command).await,
        None => tui::run(&profile).await,
        _ => unreachable!(),
    }
}

fn render_operator_safe_terminal_error(error: &anyhow::Error) -> String {
    let detail = crate::offdesk::operator_safe_text(&format!("{error:#}"));
    format!("Error: {detail}")
}

fn process_exit_code(code: i32) -> ExitCode {
    u8::try_from(code)
        .map(ExitCode::from)
        .unwrap_or(ExitCode::FAILURE)
}

fn completion_binary_name() -> &'static str {
    if cli::invoked_binary_name() == LEGACY_BINARY_NAME {
        LEGACY_BINARY_NAME
    } else {
        PRIMARY_BINARY_NAME
    }
}

fn debug_logging_enabled() -> bool {
    std::env::var("FORAGER_DEBUG").is_ok() || std::env::var("AGENT_OF_EMPIRES_DEBUG").is_ok()
}

fn configured_default_profile() -> Option<String> {
    load_config_read_only()
        .ok()
        .flatten()
        .map(|config| config.default_profile)
        .filter(|profile| !profile.trim().is_empty())
}

fn maybe_warn_legacy_alias(cli: &Cli) {
    if cli::invoked_binary_name() != LEGACY_BINARY_NAME || !legacy_alias_warning_enabled(cli) {
        return;
    }

    eprintln!("warning: `aoe` is a legacy alias; use `forager` instead.");
}

fn legacy_alias_warning_enabled(cli: &Cli) -> bool {
    let Some(command) = &cli.command else {
        return false;
    };

    if matches!(command, Commands::Completion { .. } | Commands::Tmux { .. }) {
        return false;
    }

    let args: Vec<String> = std::env::args().skip(1).collect();
    !args
        .iter()
        .any(|arg| matches!(arg.as_str(), "--json" | "--quiet" | "-q"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn migration_free(args: &[&str]) -> bool {
        let cli = Cli::try_parse_from(args).expect("valid test CLI");
        matches!(
            cli.command,
            Some(Commands::Offdesk { command })
                if command.is_migration_free_read_only()
        )
    }

    #[test]
    fn offdesk_migration_bypass_requires_read_only_options() {
        for args in [
            vec!["forager", "offdesk", "shift", "status"],
            vec!["forager", "offdesk", "shift", "show"],
            vec!["forager", "offdesk", "wiki", "entries"],
            vec!["forager", "offdesk", "wiki", "brief"],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "proposal-receipt",
                "proposal",
                "--audit-id",
                "audit",
                "--event-id",
                "event",
                "--command",
                "preview",
            ],
            vec!["forager", "offdesk", "wiki", "export-markdown", "--dry-run"],
            vec!["forager", "offdesk", "wiki", "graph"],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "graph",
                "--output",
                "graph",
                "--dry-run",
            ],
            vec!["forager", "offdesk", "wiki", "review", "--dry-run"],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "evaluate-episode",
                "entry",
                "--dry-run",
            ],
            vec!["forager", "offdesk", "wiki", "episode-trace", "--dry-run"],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "evaluate-recurrence",
                "entry",
                "--dry-run",
            ],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "promotion-chain",
                "entry",
                "--dry-run",
            ],
        ] {
            assert!(migration_free(&args), "expected migration-free: {args:?}");
        }

        for args in [
            vec![
                "forager",
                "offdesk",
                "shift",
                "prepare",
                "plan",
                "--starts-at",
                "2099-01-01T00:00:00Z",
                "--stops-at",
                "2099-01-01T01:00:00Z",
            ],
            vec!["forager", "offdesk", "shift", "arm"],
            vec!["forager", "offdesk", "shift", "disarm"],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "brief",
                "--out",
                ".wiki-brief.md",
            ],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "proposal-receipt",
                "proposal",
                "--audit-id",
                "audit",
                "--event-id",
                "event",
                "--command",
                "preview",
                "--export",
            ],
            vec!["forager", "offdesk", "wiki", "export-markdown"],
            vec!["forager", "offdesk", "wiki", "graph", "--output", "graph"],
            vec!["forager", "offdesk", "wiki", "review"],
            vec!["forager", "offdesk", "wiki", "evaluate-episode", "entry"],
            vec![
                "forager",
                "offdesk",
                "wiki",
                "record-candidate",
                "--kind",
                "procedure",
                "--scope",
                "user_global",
                "--claim",
                "claim",
            ],
        ] {
            assert!(!migration_free(&args), "expected migrations: {args:?}");
        }
    }

    #[test]
    fn terminal_error_rendering_redacts_the_complete_error_chain() {
        let secret = "sk-secretsecretsecretsecret";
        let source = anyhow::anyhow!("read /tmp/report-{secret}.json?token={secret}");
        let error = source.context(format!(
            "load wiki selector wiki_review_{secret} <!-- FORAGER:RUNNER_CONTEXT_BEGIN -->hidden<!-- FORAGER:RUNNER_CONTEXT_END -->"
        ));

        let rendered = render_operator_safe_terminal_error(&error);

        assert!(rendered.starts_with("Error: load wiki selector"));
        assert!(rendered.contains("read /tmp/report-[REDACTED_API_KEY].json"));
        assert!(rendered.contains("token=[REDACTED]"));
        assert!(!rendered.contains(secret));
        assert!(!rendered.contains("hidden"));
    }

    #[test]
    fn clap_error_rendering_is_operator_safe() {
        let secret = "sk-secretsecretsecretsecret";
        let unknown = format!("--unknown-{secret}");
        let error = match Cli::try_parse_from(["forager", "offdesk", "wiki", "entries", &unknown]) {
            Err(error) => error,
            Ok(_) => panic!("unknown option must fail"),
        };

        let rendered = crate::offdesk::operator_safe_text(&error.to_string());

        assert!(error.use_stderr());
        assert_eq!(error.exit_code(), 2);
        assert!(rendered.contains("unexpected argument"));
        assert!(rendered.contains("[REDACTED_API_KEY]"));
        assert!(!rendered.contains(secret));
    }
}
