#!/usr/bin/env node
// Export the multi-project portfolio surface: the project registry joined with
// live session counts, per-plane wiki queue sizes, and the global attention
// summary. Written to public/portfolio-surface.json for the /portfolio page.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(siteRoot, '..');
const options = parseArgs(process.argv.slice(2));
const profile = options.profile ?? process.env.FORAGER_PROFILE ?? 'default';
const outputPath = path.resolve(siteRoot, options.output ?? 'public/portfolio-surface.json');

const surface = buildPortfolioSurface({
  foragerBin: options.foragerBin,
  profile,
});
mkdirSync(path.dirname(outputPath), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify(surface, null, 2)}\n`);
console.log(
  `Exported ${surface.schema} (${surface.projects.length} projects) to ${path.relative(siteRoot, outputPath)}`,
);

export function buildPortfolioSurface({ foragerBin, profile: sessionProfile }) {
  const registry = loadProjectRegistry();
  const sessions = foragerJson(foragerBin, ['--profile', sessionProfile, 'list', '--json']) ?? [];
  const workstation = foragerJson(foragerBin, [
    '--profile',
    sessionProfile,
    'ondesk',
    'workstation-surface',
    '--json',
  ]);

  const projects = registry.map((entry) => {
    const matched = sessions.filter((session) =>
      entry.workspace_patterns.some((pattern) => String(session.path ?? '').includes(pattern)),
    );
    const tools = {};
    for (const session of matched) {
      const tool = String(session.tool ?? 'unknown');
      tools[tool] = (tools[tool] ?? 0) + 1;
    }
    let wikiCandidates = null;
    let wikiEntries = null;
    if (entry.wiki_profile) {
      const candidates = foragerJson(foragerBin, [
        '--profile',
        entry.wiki_profile,
        'offdesk',
        'wiki',
        'candidates',
        '--json',
      ]);
      const entries = foragerJson(foragerBin, [
        '--profile',
        entry.wiki_profile,
        'offdesk',
        'wiki',
        'entries',
        '--json',
      ]);
      wikiCandidates = Array.isArray(candidates) ? candidates.length : null;
      wikiEntries = Array.isArray(entries) ? entries.length : null;
    }
    return {
      key: entry.key,
      display_name: entry.display_name,
      session_group: entry.session_group,
      wiki_profile: entry.wiki_profile,
      workspace_patterns: entry.workspace_patterns,
      session_count: matched.length,
      session_tools: tools,
      wiki_candidates: wikiCandidates,
      wiki_entries: wikiEntries,
    };
  });

  return {
    schema: 'portfolio_surface.v1',
    generated_at: new Date().toISOString(),
    profile: sessionProfile,
    registry_loaded: registry.length > 0,
    attention_counts: workstation?.attention_counts ?? null,
    health: Array.isArray(workstation?.health)
      ? workstation.health.map((item) => ({ id: item.id, status: item.status }))
      : [],
    unassigned_sessions: sessions.filter(
      (session) =>
        !registry.some((entry) =>
          entry.workspace_patterns.some((pattern) => String(session.path ?? '').includes(pattern)),
        ),
    ).length,
    projects,
  };
}

function loadProjectRegistry() {
  const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  const registryPath =
    process.env.FORAGER_PROJECT_REGISTRY || path.join(configHome, 'forager', 'projects.toml');
  if (!existsSync(registryPath)) {
    return [];
  }
  const parsed = parseTomlSubset(readFileSync(registryPath, 'utf8'));
  if (parsed.schema !== 'forager_project_registry.v1') {
    return [];
  }
  return Object.entries(parsed.projects ?? {}).map(([key, entry]) => ({
    key,
    display_name: String(entry.display_name ?? key),
    workspace_patterns: (entry.workspace_patterns ?? []).map(String).filter(Boolean),
    session_group: entry.session_group ? String(entry.session_group) : null,
    wiki_profile: entry.wiki_profile ? String(entry.wiki_profile) : null,
  }));
}

// Minimal TOML subset parser for the registry: top-level scalars,
// [section.key] tables, string values, and single-line string arrays.
function parseTomlSubset(text) {
  const root = {};
  let current = root;
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const section = line.match(/^\[([^\]]+)\]$/);
    if (section) {
      current = root;
      for (const part of splitSectionName(section[1])) {
        current[part] = current[part] ?? {};
        current = current[part];
      }
      continue;
    }
    const kv = line.match(/^([A-Za-z0-9_-]+)\s*=\s*(.+)$/);
    if (!kv) continue;
    current[kv[1]] = parseTomlValue(kv[2]);
  }
  return root;
}

function splitSectionName(name) {
  // Only the first dot separates the table family from the project key, so
  // keys themselves may contain dashes (never dots).
  const index = name.indexOf('.');
  return index === -1 ? [name.trim()] : [name.slice(0, index).trim(), name.slice(index + 1).trim()];
}

function parseTomlValue(raw) {
  const text = raw.trim();
  if (text.startsWith('[')) {
    const inner = text.replace(/^\[/, '').replace(/\]\s*$/, '');
    return inner
      .split(',')
      .map((item) => item.trim().replace(/^"/, '').replace(/"$/, ''))
      .filter(Boolean);
  }
  return text.replace(/^"/, '').replace(/"$/, '');
}

function foragerJson(foragerBin, args) {
  const { command, args: fullArgs } = resolveRunner(foragerBin, args);
  const result = spawnSync(command, fullArgs, {
    cwd: repoRoot,
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.status !== 0) {
    return null;
  }
  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

function resolveRunner(foragerBin, foragerArgs) {
  if (foragerBin) {
    return { command: foragerBin, args: foragerArgs };
  }
  // Pick the most recently built binary; a stale release build may predate
  // the wiki/portfolio CLI surface entirely.
  const candidates = [
    path.join(repoRoot, 'target', 'release', 'forager'),
    path.join(repoRoot, 'target', 'debug', 'forager'),
  ]
    .filter((candidate) => existsSync(candidate))
    .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  if (candidates.length > 0) {
    return { command: candidates[0], args: foragerArgs };
  }
  return { command: 'forager', args: foragerArgs };
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--profile') parsed.profile = argv[++index];
    else if (arg === '--output') parsed.output = argv[++index];
    else if (arg === '--forager-bin') parsed.foragerBin = argv[++index];
  }
  return parsed;
}
