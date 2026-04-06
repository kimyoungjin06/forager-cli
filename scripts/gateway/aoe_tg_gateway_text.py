from typing import Any, Callable, Dict, List


def build_planned_dispatch_prompt(
    user_prompt: str,
    plan: Dict[str, Any],
    critic: Dict[str, Any],
    *,
    critic_has_blockers: Callable[[Dict[str, Any]], bool],
) -> str:
    subtasks = plan.get("subtasks") or []
    summary = str(plan.get("summary", "")).strip()
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    team_spec = meta.get("phase2_team_spec") if isinstance(meta.get("phase2_team_spec"), dict) else {}
    phase1_role_preset = str(meta.get("phase1_role_preset", "")).strip()
    phase2_team_preset = str(meta.get("phase2_team_preset", "")).strip()
    approval_mode = str(meta.get("approval_mode", "")).strip() or "policy"
    critic_role = str(team_spec.get("critic_role", "")).strip()
    integration_role = str(team_spec.get("integration_role", "")).strip()
    evidence_required = [str(item).strip() for item in (plan.get("evidence_required") or []) if str(item).strip()]

    lines: List[str] = []
    lines.append("원사용자 요청:")
    lines.append(user_prompt.strip())
    lines.append("")
    if summary:
        lines.append("계획 요약:")
        lines.append(summary)
        lines.append("")

    lines.append("실행할 sub-task:")
    for row in subtasks:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id", "")).strip() or "S"
        title = str(row.get("title", "")).strip() or "subtask"
        goal = str(row.get("goal", "")).strip() or title
        role = str(row.get("owner_role", "")).strip() or "Worker"
        lines.append(f"- {sid} [{role}] {title}: {goal}")

    execution_groups = team_spec.get("execution_groups") if isinstance(team_spec.get("execution_groups"), list) else []
    review_groups = team_spec.get("review_groups") if isinstance(team_spec.get("review_groups"), list) else []
    if execution_groups:
        lines.append("")
        lines.append(
            "Phase2 execution lanes: {mode}".format(
                mode=str(team_spec.get("execution_mode", "single")).strip() or "single"
            )
        )
        for row in execution_groups[:8]:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("group_id", "")).strip() or "E"
            role = str(row.get("role", "")).strip() or "Worker"
            subtask_ids = [str(item).strip() for item in (row.get("subtask_ids") or []) if str(item).strip()]
            lines.append(f"- lane {gid} [{role}] -> {', '.join(subtask_ids) if subtask_ids else '-'}")
    if review_groups:
        lines.append("")
        lines.append(
            "Phase2 critic lanes: {mode}".format(
                mode=str(team_spec.get("review_mode", "skip")).strip() or "skip"
            )
        )
        for row in review_groups[:6]:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("group_id", "")).strip() or "R"
            role = str(row.get("role", "")).strip() or "Codex-Reviewer"
            kind = str(row.get("kind", "")).strip() or "verifier"
            depends_on = [str(item).strip() for item in (row.get("depends_on") or []) if str(item).strip()]
            dep_txt = f" after {', '.join(depends_on)}" if depends_on else ""
            lines.append(f"- review {gid} [{role}/{kind}]{dep_txt}")

    if phase1_role_preset or phase2_team_preset or approval_mode or critic_role or integration_role or evidence_required:
        lines.append("")
        lines.append("Phase2 quality contract:")
        if phase1_role_preset or phase2_team_preset:
            lines.append(
                "- preset: phase1={phase1} phase2={phase2}".format(
                    phase1=phase1_role_preset or "-",
                    phase2=phase2_team_preset or phase1_role_preset or "-",
                )
            )
        lines.append(f"- approval mode: {approval_mode}")
        lines.append("- operator approval/recovery remains outside Task Team")
        if critic_role:
            lines.append(f"- critic role: {critic_role}")
        if integration_role:
            lines.append(f"- integration role: {integration_role}")
        for item in evidence_required[:4]:
            lines.append(f"- evidence: {item}")

    issues = critic.get("issues") or []
    recs = critic.get("recommendations") or []
    approved = not critic_has_blockers(critic)

    if not approved or issues or recs:
        lines.append("")
        lines.append("critic 체크:")
        if issues:
            for item in issues[:5]:
                lines.append(f"- issue: {str(item)}")
        if recs:
            for item in recs[:5]:
                lines.append(f"- fix: {str(item)}")

    lines.append("")
    lines.append("Phase2 실행 규칙:")
    lines.append("- 가능한 역할은 병렬로 동시에 진행한다.")
    lines.append("- critic/verifier 역할은 핵심 산출물에 대해 병렬로 비판 검토한다.")
    lines.append("- quality contract의 preset/critic/integration/evidence를 기본 완료 기준으로 따른다.")
    lines.append("- 실행 결과는 역할별 산출물 + 검증 근거 + 남은 리스크를 명확히 남긴다.")
    lines.append("")
    lines.append("위 계획과 체크사항을 반영해 역할별 실행/검증 결과를 산출해라.")
    return "\n".join(lines)


def help_text(
    ui_lang: str,
    *,
    default_ui_lang: str,
    preferred_command_prefix: Callable[[], str],
    normalize_chat_lang_token: Callable[[str, str], str],
) -> str:
    p = preferred_command_prefix()
    text = (
        "AOE Telegram Gateway commands\n"
        f"command prefix: {p}  (env: AOE_TG_COMMAND_PREFIXES; supports '/' and/or '!')\n"
        f"tip: unique abbreviations are accepted (ex: {p}st -> {p}status, {p}cle -> {p}clear)\n"
        "\n"
        "routine (copy/paste examples)\n"
        f"- {p}tutorial                  # quickstart guide\n"
        f"- {p}map                       # project map (O1..)\n"
        f"- {p}use O2                    # switch active project (soft focus)\n"
        f"- {p}focus O2                  # hard lock to one project\n"
        f"- {p}sync all 1h               # seed queue from scenario files; falls back to project todo docs if scenario is empty\n"
        f"- {p}sync                      # repeat last {p}sync args (chat-local)\n"
        f"- {p}queue                     # global todo queue\n"
        f"- {p}queue followup            # projects with manual follow-up backlog only\n"
        f"- {p}fanout                    # one todo per project wave\n"
        f"- {p}offdesk on                # after-work preset (auto fanout recent)\n"
        f"- {p}auto status               # scheduler status\n"
        f"- {p}panic                     # emergency stop (auto/offdesk off)\n"
        f"- {p}clear pending             # clear pending/confirm\n"
        f"- {p}room tail 20               # latest room events\n"
        "\n"
        "Quick mode (prefix-only default)\n"
        "- /status /check /task /monitor /kpi /map /help /tutorial\n"
        "- /queue  (global todo queue view)\n"
        "- /queue followup  (projects with manual_followup backlog only)\n"
        "- /sync [O#|name|all] [since 3h|1h]  (import <project_root>/.aoe-team/AOE_TODO.md into queue; if empty, fallback to todo-ish files/recent docs; empty args repeats last /sync)\n"
        "- /sync preview [replace] [O#|name|all] [since 3h|1h]  (show source files, source classes/confidence, and would-add/update/done/prune counts without changing queue; plain /sync fallback now bootstraps from recent md docs + salvage + todo files)\n"
        "- /sync bootstrap [O#|name|all] [since 24h]  (explicit bootstrap path: prefer recent docs + salvage when canonical backlog is missing, stale, or untrusted)\n"
        "- /sync recent [O#|name|all] [N] [since 3h]  (scan N recent todo-ish docs; default N=3)\n"
        "- /sync salvage [O#|name|all] [N] [since 3h]  (broader recent-doc salvage: recovers 'next steps/남은 일/follow-up' sections; loose follow-ups go to /todo proposals)\n"
        "- /sync files [O#|name|all] [N] [since 3h]  (scan todo-ish files by filename; default N=80)\n"
        "- /sync replace [O#|name]  (full-scope sync + cancel stale sync-managed open todos that no longer appear in source)\n"
        "- optional override: <project>/.aoe-team/sync_policy.json  (path globs / confidence / group tuning)\n"
        "- /next   (global todo scheduler)\n"
        "- /fanout (one todo per project wave)\n"
        "- /drain  (repeat /next N times)\n"
        "- /auto   (background /next loop via tmux scheduler; stops on confirm/stuck/too-many-failures)\n"
        "- /auto on fanout recent since 12h maxfail=3  (idle prefetch: /sync files all since 12h + /sync salvage all since 12h)\n"
        "- /auto on fanout recent replace-sync  (idle prefetch: /sync replace all quiet; full-scope, since ignored)\n"
        "- /offdesk [on|off|status|prepare|review]  (preset: report short + routing off + auto fanout recent; prepare = preflight, review = flagged-project drill-down)\n"
        "- /offdesk on replace-sync  (same preset, but idle prefetch uses /sync replace all quiet)\n"
        "- /panic  (emergency stop: auto/offdesk off + clear pending/confirm + routing off)\n"
        "- /clear  (clear pending/routing/room/queue; safe defaults)\n"
        "- /todo   (project backlog)\n"
        "- /todo proposals   (Task Team follow-up proposal inbox)\n"
        "- /todo followup   (manual follow-up backlog only)\n"
        "- /todo add [P1|P2|P3] <summary>\n"
        "- /todo accept <PROP-xxx|number>   (promote proposal into main todo queue)\n"
        "- /todo reject <PROP-xxx|number> [reason]   (discard proposal)\n"
        "- /todo ack <TODO-xxx|number>   (reopen blocked todo after manual review)\n"
        "- /todo ackrun <TODO-xxx|number>   (reopen blocked todo and dispatch it now)\n"
        "- /todo syncback [preview]   (write runtime done/blocked notes/new accepted items back to canonical TODO.md)\n"
        "- /todo done <TODO-xxx|number>\n"
        "- /todo next   (run next open todo)\n"
        "- /room   (ephemeral board: /room post|tail|list|use)\n"
        "- /gc     (cleanup room logs + tf exec cache)\n"
        "- /tf     (proof checks, local; writes report under docs/investigations_mo; ex: /tf mod2-proof tags | /tf mod2-proof latest)\n"
        "- /use <O1|name> (active runtime switch; soft focus)\n"
        "- /focus [O1|name|off] (hard project lock / unlock)\n"
        "- /orch pause <O#|name> [reason]\n"
        "- /orch resume <O#|name>\n"
        "- /orch hide <O#|name> [reason]\n"
        "- /orch unhide <O#|name>\n"
        "- /mode [on|off|direct]\n"
        "- /on /off\n"
        "- /lang [ko|en]\n"
        "- /report [short|normal|long|off]\n"
        "- /replay [list|latest|<idx>|<id>|show <idx|id|latest>|purge]\n"
        "- /history search <query> [--project O#] [--since 12h] [--limit N] [--scope ...]\n"
        "- /ok (고위험 자동실행 확인)\n"
        "- /whoami /lockme /onlyme\n"
        "- /acl /grant /revoke\n"
        "- /pick [번호|task_label]   (빈칸이면 최근 목록)\n"
        "- /dispatch <요청>   (서브에이전트 배정)\n"
        "- /direct <질문>     (오케스트레이터 직접 답변)\n"
        "- /dispatch 또는 /direct만 입력하면 다음 메시지 1회 모드\n"
        "- /cancel (대기 모드 해제)\n"
        "\n"
        "Slash mode\n"
        "- /help\n"
        "- /status\n"
        "- /mode [on|off|direct|dispatch]\n"
        "- /lang [ko|en]\n"
        "- /report [short|normal|long|off]\n"
        "- /on /off\n"
        "- /replay [list|latest|<idx>|<id>|show <idx|id|latest>|purge]\n"
        "- /ok\n"
        "- /onlyme   # 1:1 owner-only claim (lock + owner_only)\n"
        "- /acl\n"
        "- /grant <allow|admin|readonly> <chat_id|alias>\n"
        "- /revoke <allow|admin|readonly|all> <chat_id|alias>\n"
        "- /kpi [hours]\n"
        "- /map\n"
        "- /use <O1|name>          # active project switch (soft focus)\n"
        "- /focus [O1|name|off]    # hard lock one project / unlock\n"
        "- 단일 프로젝트 권장 흐름: /map -> /use O# -> /focus O# -> 평문 또는 /sync O# -> /next\n"
        "- /use 후에는 평문/TF가 해당 프로젝트를 기본 타겟으로 사용\n"
        "- /focus 후에는 /queue, /next, /sync all, /offdesk가 해당 프로젝트에 맞게 축소되고 /fanout은 차단됨\n"
        "- /queue\n"
        "- /sync [all|O#|name]\n"
        "- /sync preview [replace] [all|O#|name] [since 3h|1h]\n"
        "- /sync recent [O#|name|all] [N]\n"
        "- /sync salvage [O#|name|all] [N]\n"
        "- /sync files [O#|name|all] [N]\n"
        "- /sync replace [O#|name]\n"
        "- optional: <project>/.aoe-team/sync_policy.json\n"
        "- /next                   # active project 우선 단일 실행\n"
        "- /fanout [N] [force]     # global wave, 프로젝트별 1개씩\n"
        "- /drain [N] [force]\n"
        "- /auto [on|off|status [short|long]]\n"
        "- /auto on fanout recent since 12h maxfail=3\n"
        "- /auto on fanout recent replace-sync\n"
        "- /offdesk [on|off|status [short|long]|prepare|review]\n"
        "- /offdesk on replace-sync\n"
        "- /panic [status]\n"
        "- /clear [pending|routing|room|queue]\n"
        "- /todo\n"
        "- /todo proposals\n"
        "- /todo add [P1|P2|P3] <summary>\n"
        "- /todo accept <PROP-xxx|number>\n"
        "- /todo reject <PROP-xxx|number> [reason]\n"
        "- /todo ack <TODO-xxx|number>\n"
        "- /todo ackrun <TODO-xxx|number>\n"
        "- /todo syncback [preview]\n"
        "- /todo done <TODO-xxx|number>\n"
        "- /todo next\n"
        "- /tf [list|<recipe> [tag]]\n"
        "- /room [list|use|post|tail]\n"
        "- /gc [force]\n"
        "- /orch pause <O#|name> [reason]\n"
        "- /orch resume <O#|name>\n"
        "- /orch hide <O#|name> [reason]\n"
        "- /orch unhide <O#|name>\n"
        "- /orch repair [all|O#|name]\n"
        "- /pick [number|request_or_alias]  # empty shows recent menu\n"
        "- /cancel [request_or_alias]\n"
        "- /retry <request_or_alias> [lane <L#|R#,...>]\n"
        "- /replan <request_or_alias> [lane <L#|R#,...>]\n"
        "- /followup <request_or_alias> [lane <L#|R#,...>]\n"
        "- /followup-exec <request_or_alias> [lane <L#|R#,...>]  # explicit execute surface; blocks while FollowupBrief is preview_only\n"
        "- /request <request_or_alias>\n"
        "- /run <prompt>\n"
        "- /add-role <Role|--name Name> [--provider <name>] [--launch <cmd>] [--spawn|--no-spawn]\n"
        "- /add-claude <Role|--name Name> [--launch <cmd>] [--spawn|--no-spawn]\n"
        "- /add-codex <Role|--name Name> [--launch <cmd>] [--spawn|--no-spawn]\n"
        "\n"
        "CLI mode\n"
        "- aoe status\n"
        "- aoe mode [on|off|direct|dispatch]\n"
        "- aoe lang [ko|en]\n"
        "- aoe report [short|normal|long|off]\n"
        "- aoe on | aoe off\n"
        "- aoe replay [list|latest|<idx>|<id>|show <idx|id|latest>|purge]\n"
        "- aoe history search <query> [--project O#] [--since 12h] [--limit N] [--scope ...]\n"
        "- aoe ok\n"
        "- aoe acl\n"
        "- aoe grant <allow|admin|readonly> <chat_id|alias>\n"
        "- aoe revoke <allow|admin|readonly|all> <chat_id|alias>\n"
        "- aoe kpi [hours]\n"
        "- aoe map\n"
        "- aoe orch use <name>     # set active project (soft focus)\n"
        "- aoe focus [O#|name|off]\n"
        "- aoe unlock\n"
        "- aoe queue\n"
        "- aoe drain [N] [force]\n"
        "- aoe fanout [N] [force]  # global wave\n"
        "- aoe auto [on|off|status]\n"
        "- aoe offdesk [on|off|status]\n"
        "- aoe panic [status]\n"
        "- aoe monitor [limit]\n"
        "- aoe next                # active project 우선 단일 실행\n"
        "- aoe todo [add|done|next] ...\n"
        "- aoe room [list|use|post|tail] ...\n"
        "- aoe gc [force]\n"
        "- aoe pick <number|request_or_alias>\n"
        "- aoe cancel [request_or_alias]\n"
        "- aoe retry <request_or_alias> [lane <L#|R#,...>]\n"
        "- aoe replan <request_or_alias> [lane <L#|R#,...>]\n"
        "- aoe followup <request_or_alias> [lane <L#|R#,...>]\n"
        "- aoe followup-exec <request_or_alias> [lane <L#|R#,...>]\n"
        "- aoe request <request_or_alias>\n"
        "- aoe run [--direct|--dispatch] [--roles <csv>] [--priority P1|P2|P3] [--timeout-sec N] [--no-wait] <prompt>\n"
        "- aoe add-role <Role|--name Name> [--provider <name>] [--launch <cmd>] [--spawn|--no-spawn]\n"
        "- aoe add-claude <Role|--name Name> [--launch <cmd>] [--spawn|--no-spawn]\n"
        "- aoe add-codex <Role|--name Name> [--launch <cmd>] [--spawn|--no-spawn]\n"
        "\n"
        "Orch Manager\n"
        "- aoe orch list (or: aoe orch map)\n"
        "- aoe orch use <name>\n"
        "- aoe orch add <name> --path <project_root> [--overview <text>] [--init|--no-init] [--spawn|--no-spawn]\n"
        "- aoe orch repair [all|--orch <name>]\n"
        "- aoe orch bgq-clean [--orch <name>]\n"
        "- aoe orch bgw-status [--orch <name>]\n"
        "- aoe orch bgw-start [--orch <name>]\n"
        "- aoe orch bgw-stop [--orch <name>]\n"
        "- aoe orch pause <name> [reason]\n"
        "- aoe orch resume <name>\n"
        "- aoe orch hide <name> [reason]\n"
        "- aoe orch unhide <name>\n"
        "- aoe orch status [--orch <name>]\n"
        "- aoe orch kpi [--orch <name>] [--hours <n>]\n"
        "- aoe orch monitor [--orch <name>] [--limit <n>]\n"
        "- aoe orch run [--orch <name>] [--direct|--dispatch] [--roles <csv>] [--priority P1|P2|P3] [--timeout-sec N] [--no-wait] <prompt>\n"
        "- aoe orch check [--orch <name>] [<request_or_alias>]   # 3단계 진행확인\n"
        "- aoe orch task [--orch <name>] [<request_or_alias>]    # lifecycle 상태\n"
        "- aoe orch pick [--orch <name>] <number|request_or_alias>\n"
        "- aoe orch cancel [--orch <name>] [<request_or_alias>]\n"
        "- aoe orch retry [--orch <name>] <request_or_alias>\n"
        "- aoe orch replan [--orch <name>] <request_or_alias>\n"
        "\n"
        "Routing\n"
        "- default: prefix-only (plain text ignored unless pending/default mode)\n"
        "- soft focus: /use <O#|name> sets the default project used by plain text and Task Team commands\n"
        "- hard lock: /focus <O#|name> narrows /queue, /next, /sync all, /offdesk to one project and blocks /fanout\n"
        "- unlock: /focus off (or /unlock)\n"
        "- default access: deny-by-default (allowlist required)\n"
        "- bootstrap: when allowlist is empty, only /lockme|/whoami|/help is accepted\n"
        "- owner-only: /onlyme locks to current chat and enables private-DM owner gate\n"
        "- owner gate: /lockme /grant /revoke are owner-only when TELEGRAM_OWNER_CHAT_ID is set\n"
        "- dispatch only when explicit (--dispatch or --roles)\n"
        "- auto dispatch: disabled by default (enable with --auto-dispatch)\n"
        "- force dispatch: --dispatch\n"
        "- force direct: --direct\n"
        "- slash-only default: enabled (disable with --no-slash-only)\n"
        "- verifier gate: on by default (disable with --no-require-verifier)\n"
        "- task planning: on by default (disable with --no-task-planning)\n"
        "- planning gate: auto-replan + block on critic issues by default\n"
    )
    if p != "/":
        import re as _re

        text = _re.sub(r"(?<!:)/(\w)", f"{p}\1", text)

    lang = normalize_chat_lang_token(ui_lang, default_ui_lang) or default_ui_lang
    if lang != "en":
        return text
    return (
        text
        .replace("고위험 자동실행 확인", "confirm high-risk auto execution")
        .replace("서브에이전트 배정", "sub-agent assignment")
        .replace("오케스트레이터 직접 답변", "orchestrator direct reply")
        .replace("다음 메시지 1회 모드", "one-shot next-message mode")
        .replace("대기 모드 해제", "clear pending mode")
        .replace("3단계 진행확인", "3-stage progress")
        .replace("lifecycle 상태", "lifecycle status")
    )
