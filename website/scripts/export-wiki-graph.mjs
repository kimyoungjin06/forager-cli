#!/usr/bin/env node
// Export the adaptive-wiki tag graph as an operator-safe view model for the
// /knowledge route. Runs `forager offdesk wiki graph --json`, enriches each
// record node with the attributes the visualization needs (status, kind,
// scope, confidence, agent modes, occurrence), and writes public/wiki-graph.json.
//
// Node attributes come from the graph's derived tag edges (status/kind/scope/...)
// so the export works from the graph command alone. When the profile's canonical
// source files are readable it also picks up occurrence_count and evidence
// counts for weighting. The graph command is read-only and never mutates state.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const VIEW_SCHEMA = 'wiki_knowledge_graph_view.v1';

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(siteRoot, '..');
const options = parseArgs(process.argv.slice(2));

if (options.profiles) {
  // Multi-profile mode: one file per profile plus an index manifest, so the
  // /knowledge route can offer a profile selector.
  const profiles = options.profiles;
  const outDir = path.resolve(siteRoot, options.output ?? 'public/wiki-graph');
  mkdirSync(outDir, { recursive: true });
  const manifest = [];
  for (const profile of profiles) {
    const view = buildProfileView(profile);
    writeFileSync(path.join(outDir, `${profile}.json`), `${JSON.stringify(view, null, 2)}\n`);
    manifest.push({
      key: profile,
      records: view.summary.records,
      tag_nodes: view.summary.tag_nodes,
      review_issues: view.summary.review_issues,
      generated_at: view.generated_at,
    });
    console.log(`  ${profile}: ${view.summary.records} records, ${view.summary.tag_nodes} tags`);
  }
  const defaultKey = manifest.find((entry) => entry.records > 0)?.key ?? profiles[0];
  writeFileSync(
    path.join(outDir, 'index.json'),
    `${JSON.stringify({ schema: 'wiki_knowledge_graph_index.v1', default: defaultKey, profiles: manifest }, null, 2)}\n`,
  );
  console.log(`Exported ${profiles.length} profile(s) + index to ${path.relative(siteRoot, outDir)}/`);
} else {
  const profile = options.profile ?? process.env.FORAGER_PROFILE ?? 'default';
  const outputPath = path.resolve(siteRoot, options.output ?? 'public/wiki-graph.json');
  const view = buildProfileView(profile);
  mkdirSync(path.dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, `${JSON.stringify(view, null, 2)}\n`);
  console.log(
    `Exported ${view.schema} to ${path.relative(siteRoot, outputPath)} ` +
      `(${view.summary.records} records, ${view.summary.tag_nodes} tags, ${view.edges.length} edges)`,
  );
}

function buildProfileView(profile) {
  const { command, args } = resolveRunner(options.foragerBin, [
    '--profile',
    profile,
    'offdesk',
    'wiki',
    'graph',
    '--json',
  ]);
  const result = spawnSync(command, args, { cwd: repoRoot, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.stdout || `forager wiki graph export failed for ${profile}\n`);
    process.exit(result.status ?? 1);
  }
  let graph;
  try {
    graph = JSON.parse(result.stdout);
  } catch (error) {
    process.stderr.write(`forager did not emit valid JSON for ${profile}: ${error.message}\n`);
    process.exit(1);
  }
  if (!Array.isArray(graph?.nodes) || !Array.isArray(graph?.edges)) {
    process.stderr.write(`unexpected wiki graph shape for ${profile}: missing nodes/edges\n`);
    process.exit(1);
  }
  return buildView(graph, loadSources(profile), profile);
}

// ---- view model ----
function buildView(graph, sources, profile) {
  const degree = new Map();
  const tagsByRecord = new Map();
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    if (edge.target.startsWith('tag:')) {
      const list = tagsByRecord.get(edge.source) ?? [];
      list.push(edge.target.slice(4));
      tagsByRecord.set(edge.source, list);
    }
  }
  // record id -> derived attributes from its tag edges
  const derived = new Map();
  for (const edge of graph.edges) {
    if (!edge.target.startsWith('tag:')) continue;
    const [prefix, ...rest] = edge.target.slice(4).split('/');
    const value = rest.join('/');
    const attrs = derived.get(edge.source) ?? { agent_modes: [] };
    if (prefix === 'kind') attrs.kind = value;
    else if (prefix === 'scope') attrs.scope = value;
    else if (prefix === 'status') attrs.status = value;
    else if (prefix === 'confidence') attrs.confidence = value;
    else if (prefix === 'signal') attrs.signal_kind = value;
    else if (prefix === 'agent' && value !== 'shared' && !attrs.agent_modes.includes(value)) attrs.agent_modes.push(value);
    derived.set(edge.source, attrs);
  }

  const nodes = graph.nodes.map((node) => {
    const deg = degree.get(node.id) ?? 0;
    if (node.node_type === 'tag') {
      return {
        id: node.id,
        type: 'tag',
        label: node.label,
        tag: node.tag,
        tag_class: node.tag_class,
        prefix: (node.tag ?? '').split('/')[0],
        degree: deg,
      };
    }
    const d = derived.get(node.id) ?? {};
    const src = sources.byId.get(node.id) ?? {};
    const status = node.node_type === 'candidate' ? 'candidate' : src.status ?? d.status ?? 'promoted';
    const occurrence = src.occurrence_count;
    const evidence = Array.isArray(src.evidence_refs) ? src.evidence_refs.length : undefined;
    const weight = occurrence ?? evidence ?? Math.max(1, Math.round(deg / 3));
    const scope = src.scope ?? d.scope;
    const kind = src.kind ?? d.kind;
    const agentModes = src.agent_modes ?? d.agent_modes ?? [];
    const recordTags = tagsByRecord.get(node.id) ?? [];
    return {
      id: node.id,
      type: node.node_type,
      wiki_id: node.wiki_id,
      label: node.label,
      kind,
      scope,
      scope_ref: src.scope_ref,
      status,
      confidence: src.confidence ?? d.confidence,
      activation_mode: src.activation_mode,
      agent_modes: agentModes,
      signal_kind: src.signal_kind ?? d.signal_kind,
      occurrence,
      evidence_count: evidence,
      degree: deg,
      weight,
      facet: deriveFacet(kind, agentModes, recordTags),
    };
  });

  const records = nodes.filter((n) => n.type !== 'tag').length;
  return {
    schema: VIEW_SCHEMA,
    generated_at: graph.generated_at,
    profile,
    summary: { ...graph.summary, records },
    registry: graph.registry ?? [],
    review_issues: graph.review_issues ?? [],
    nodes,
    edges: graph.edges,
  };
}

// Knowledge facet: research (the substantive science) vs ops (how to run the
// machinery: environment, entrypoints, reproducibility gates, resume, cleanup).
// Explicit `facet/<x>` tags win; otherwise agent modes and a few governance
// tags decide. The profile selector handles the tenant axis (a project's wiki
// vs a Forager-ops wiki), so this stays within one store.
function deriveFacet(kind, agentModes, tags) {
  const researchModes = ['analysis', 'writing', 'critique', 'review', 'planning'];
  const explicit = tags.find((tag) => tag.startsWith('facet/'));
  if (explicit) return explicit.slice('facet/'.length);
  if (tags.includes('risk/conflict-priority') || tags.includes('risk/legacy-md')) return 'ops';
  const modes = agentModes || [];
  if (modes.some((mode) => researchModes.includes(mode))) return 'research';
  if (modes.some((mode) => mode === 'development' || mode === 'maintenance')) return 'ops';
  if (kind === 'fact') return 'research';
  return 'research';
}

// ---- best-effort canonical source read (for occurrence/evidence weighting) ----
function loadSources(profile) {
  const byId = new Map();
  const dir = profileDir(profile);
  if (!dir) return { byId };
  for (const [file, key] of [
    ['adaptive_wiki_entries.json', 'entries'],
    ['adaptive_wiki_candidates.json', 'candidates'],
  ]) {
    const full = path.join(dir, file);
    if (!existsSync(full)) continue;
    try {
      const parsed = JSON.parse(readFileSync(full, 'utf8'));
      for (const item of parsed[key] ?? []) {
        if (item?.id) byId.set(`${key === 'entries' ? 'entry' : 'candidate'}:${item.id}`, item);
      }
    } catch {
      // ignore unreadable source; derived tag attributes still apply
    }
  }
  return { byId };
}

function profileDir(profile) {
  const home = os.homedir();
  if (!home) return null;
  if (process.platform === 'linux') {
    const cfg = process.env.XDG_CONFIG_HOME || path.join(home, '.config');
    return path.join(cfg, 'forager', 'profiles', profile);
  }
  return path.join(home, '.forager', 'profiles', profile);
}

// ---- runner + args (mirrors export-workstation-surface.mjs) ----
function resolveRunner(foragerBin, foragerArgs) {
  if (foragerBin) return { command: foragerBin, args: foragerArgs };
  if (process.env.FORAGER_BIN) return { command: process.env.FORAGER_BIN, args: foragerArgs };
  const debugBinary = path.join(repoRoot, 'target', 'debug', 'forager');
  if (existsSync(debugBinary)) return { command: debugBinary, args: foragerArgs };
  return {
    command: 'cargo',
    args: ['run', '--quiet', '--manifest-path', path.join(repoRoot, 'Cargo.toml'), '--', ...foragerArgs],
  };
}

function parseArgs(args) {
  const parsed = {};
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    switch (arg) {
      case '--profile':
        parsed.profile = requiredValue(args, (index += 1), arg);
        break;
      case '--profiles':
        parsed.profiles = requiredValue(args, (index += 1), arg)
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean);
        break;
      case '--output':
        parsed.output = requiredValue(args, (index += 1), arg);
        break;
      case '--forager-bin':
        parsed.foragerBin = requiredValue(args, (index += 1), arg);
        break;
      case '--help':
      case '-h':
        printHelp();
        process.exit(0);
      default:
        process.stderr.write(`unknown option: ${arg}\n`);
        printHelp();
        process.exit(2);
    }
  }
  return parsed;
}

function requiredValue(args, index, flag) {
  const value = args[index];
  if (!value || value.startsWith('--')) {
    process.stderr.write(`${flag} requires a value\n`);
    process.exit(2);
  }
  return value;
}

function printHelp() {
  process.stdout.write(`Usage: npm run export:wiki-graph -- [options]

Options:
  --profile <name>       Single profile to read. Defaults to FORAGER_PROFILE or default.
  --profiles <a,b,c>     Export several profiles plus an index manifest for the profile selector.
                         Writes public/wiki-graph/<profile>.json and public/wiki-graph/index.json.
  --output <path>        Output path (file for --profile, directory for --profiles). Relative to website root.
  --forager-bin <path>   Forager binary to run. Defaults to FORAGER_BIN, target/debug/forager, then cargo run.
`);
}
