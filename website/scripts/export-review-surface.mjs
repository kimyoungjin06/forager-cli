#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(siteRoot, '..');
const options = parseArgs(process.argv.slice(2));
const profile = options.profile ?? process.env.FORAGER_PROFILE ?? 'default';
const outputPath = path.resolve(siteRoot, options.output ?? 'public/review-surface.json');
const foragerArgs = ['--profile', profile, 'ondesk', 'review-surface', '--json'];

if (options.projectKey) {
  foragerArgs.push('--project-key', options.projectKey);
}

const { command, args } = resolveRunner(options.foragerBin, foragerArgs);
const result = spawnSync(command, args, {
  cwd: repoRoot,
  encoding: 'utf8',
  maxBuffer: 20 * 1024 * 1024,
});

if (result.status !== 0) {
  process.stderr.write(result.stderr || result.stdout || 'forager review-surface export failed\n');
  process.exit(result.status ?? 1);
}

let surface;
try {
  surface = JSON.parse(result.stdout);
} catch (error) {
  process.stderr.write(`forager did not emit valid JSON: ${error.message}\n`);
  process.exit(1);
}

if (surface?.schema !== 'review_surface.v1') {
  process.stderr.write(`unexpected review surface schema: ${surface?.schema ?? 'missing'}\n`);
  process.exit(1);
}

if (surface?.redaction?.operator_safe !== true) {
  process.stderr.write('refusing to export review surface without redaction.operator_safe=true\n');
  process.exit(1);
}

mkdirSync(path.dirname(outputPath), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify(surface, null, 2)}\n`);
console.log(`Exported ${surface.schema} to ${path.relative(siteRoot, outputPath)}`);

function resolveRunner(foragerBin, foragerArgs) {
  if (foragerBin) {
    return { command: foragerBin, args: foragerArgs };
  }

  if (process.env.FORAGER_BIN) {
    return { command: process.env.FORAGER_BIN, args: foragerArgs };
  }

  const debugBinary = path.join(repoRoot, 'target', 'debug', 'forager');
  if (existsSync(debugBinary)) {
    return { command: debugBinary, args: foragerArgs };
  }

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
      case '--project-key':
        parsed.projectKey = requiredValue(args, (index += 1), arg);
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
  process.stdout.write(`Usage: npm run export:review-surface -- [options]

Options:
  --profile <name>       Forager profile to read. Defaults to FORAGER_PROFILE or default.
  --project-key <key>    Focus the review surface on one project key.
  --output <path>        Output path relative to website root. Defaults to public/review-surface.json.
  --forager-bin <path>   Forager binary to run. Defaults to FORAGER_BIN, target/debug/forager, then cargo run.
`);
}
