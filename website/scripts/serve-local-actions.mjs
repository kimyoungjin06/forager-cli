#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { createReadStream, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { stat } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(siteRoot, '..');
const distRoot = path.join(siteRoot, 'dist');
const options = parseArgs(process.argv.slice(2));
const host = options.host ?? '127.0.0.1';
const port = Number(options.port ?? process.env.FORAGER_ACTION_BRIDGE_PORT ?? 4387);
const profile = options.profile ?? process.env.FORAGER_PROFILE ?? 'default';
const basePath = normalizeBasePath(options.basePath ?? '/forager-cli');

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  fail(`invalid port: ${options.port}`);
}

if (!isLocalHost(host) && !options.allowRemote) {
  fail('refusing to bind a non-local host without --allow-remote');
}

if (!existsSync(distRoot)) {
  fail('website/dist is missing. Run `npm run build --prefix website` first.');
}

const server = http.createServer((request, response) => {
  handleRequest(request, response).catch((error) => {
    json(response, 500, {
      ok: false,
      error: 'internal_error',
      message: error.message,
    });
  });
});

server.listen(port, host, () => {
  process.stdout.write(`Forager local action bridge listening on http://${host}:${port}${basePath}/\n`);
  process.stdout.write('Allowed API: POST /api/ondesk/action-envelope\n');
});

async function handleRequest(request, response) {
  const url = new URL(request.url ?? '/', `http://${host}:${port}`);
  const pathname = normalizePathname(url.pathname);
  const actionEnvelopeApi = `${basePath}/api/ondesk/action-envelope`;

  if (request.method === 'POST' && (pathname === actionEnvelopeApi || pathname === '/api/ondesk/action-envelope')) {
    await handleActionEnvelope(request, response);
    return;
  }

  if (request.method === 'GET' || request.method === 'HEAD') {
    await serveStatic(pathname, response, request.method === 'HEAD');
    return;
  }

  json(response, 405, {
    ok: false,
    error: 'method_not_allowed',
  });
}

async function handleActionEnvelope(request, response) {
  const body = await readJsonBody(request);
  const envelope = body?.envelope;
  if (!isPlainObject(envelope)) {
    json(response, 400, {
      ok: false,
      error: 'missing_envelope',
      message: 'Expected JSON body with an envelope object.',
    });
    return;
  }

  const schema = String(envelope.schema ?? '');
  if (schema !== 'action_envelope.v1') {
    json(response, 400, {
      ok: false,
      error: 'unsupported_schema',
      message: `Expected action_envelope.v1, got ${schema || 'missing'}.`,
    });
    return;
  }

  const envelopePath = writeEnvelope(envelope);
  const result = runForager(['--profile', profile, 'ondesk', 'action-envelope', '--envelope', envelopePath, '--json']);
  if (result.status !== 0) {
    json(response, 422, {
      ok: false,
      error: 'action_envelope_failed',
      message: result.stderr || result.stdout || 'forager ondesk action-envelope failed',
    });
    return;
  }

  const output = parseJsonOutput(result.stdout, 'forager action-envelope output');
  const surfaceResult = exportWorkstationSurface();
  json(response, 200, {
    ok: true,
    receipt: output.receipt,
    receipt_appended: output.receipt_appended,
    receipt_path: output.receipt_path,
    dry_run: output.dry_run,
    surface_refreshed: surfaceResult.ok,
    surface_error: surfaceResult.ok ? null : surfaceResult.message,
  });
}

async function serveStatic(pathname, response, headOnly) {
  let relative = pathname.startsWith(basePath) ? pathname.slice(basePath.length) : pathname;
  if (!relative || relative === '/') {
    relative = '/index.html';
  }

  let filePath = safeJoin(distRoot, relative);
  if (!filePath) {
    response.writeHead(403);
    response.end('Forbidden');
    return;
  }

  try {
    const info = await stat(filePath);
    if (info.isDirectory()) {
      filePath = path.join(filePath, 'index.html');
    }
  } catch {
    if (!path.extname(filePath)) {
      filePath = path.join(filePath, 'index.html');
    }
  }

  let info;
  try {
    info = await stat(filePath);
  } catch {
    response.writeHead(404);
    response.end('Not found');
    return;
  }

  if (!info.isFile()) {
    response.writeHead(404);
    response.end('Not found');
    return;
  }

  response.writeHead(200, {
    'content-type': contentType(filePath),
    'content-length': info.size,
  });
  if (headOnly) {
    response.end();
    return;
  }
  createReadStream(filePath).pipe(response);
}

function exportWorkstationSurface() {
  const result = runForager(['--profile', profile, 'ondesk', 'workstation-surface', '--json']);
  if (result.status !== 0) {
    return {
      ok: false,
      message: result.stderr || result.stdout || 'forager workstation-surface export failed',
    };
  }

  let surface;
  try {
    surface = parseJsonOutput(result.stdout, 'workstation surface output');
  } catch (error) {
    return { ok: false, message: error.message };
  }

  if (surface?.schema !== 'workstation_surface.v1') {
    return { ok: false, message: `unexpected workstation surface schema: ${surface?.schema ?? 'missing'}` };
  }

  if (surface?.redaction?.operator_safe !== true) {
    return { ok: false, message: 'refusing to export non-operator-safe workstation surface' };
  }

  const outputPath = path.join(siteRoot, 'public', 'workstation-surface.json');
  mkdirSync(path.dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, `${JSON.stringify(surface, null, 2)}\n`);
  return { ok: true, message: 'surface refreshed' };
}

function writeEnvelope(envelope) {
  const actionRoot = path.join(siteRoot, '.forager-action-bridge', 'envelopes');
  mkdirSync(actionRoot, { recursive: true });
  const actionId = safeName(String(envelope.action_id ?? 'action-envelope'));
  const filename = `${Date.now()}-${process.pid}-${actionId}.json`;
  const envelopePath = path.join(actionRoot, filename);
  writeFileSync(envelopePath, `${JSON.stringify(envelope, null, 2)}\n`, { mode: 0o600 });
  return envelopePath;
}

function runForager(foragerArgs) {
  const { command, args } = resolveRunner(options.foragerBin, foragerArgs);
  return spawnSync(command, args, {
    cwd: repoRoot,
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
  });
}

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

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 512 * 1024) {
      throw new Error('request body exceeds 512 KiB');
    }
    chunks.push(chunk);
  }

  const text = Buffer.concat(chunks).toString('utf8');
  if (!text.trim()) {
    return {};
  }
  return JSON.parse(text);
}

function parseJsonOutput(text, label) {
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`invalid ${label}: ${error.message}`);
  }
}

function json(response, status, payload) {
  const text = `${JSON.stringify(payload)}\n`;
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(text),
    'cache-control': 'no-store',
  });
  response.end(text);
}

function safeJoin(root, relativePath) {
  const decoded = decodeURIComponent(relativePath);
  const resolved = path.resolve(root, `.${decoded}`);
  return resolved.startsWith(root + path.sep) || resolved === root ? resolved : null;
}

function contentType(filePath) {
  const ext = path.extname(filePath);
  switch (ext) {
    case '.html':
      return 'text/html; charset=utf-8';
    case '.css':
      return 'text/css; charset=utf-8';
    case '.js':
      return 'text/javascript; charset=utf-8';
    case '.json':
      return 'application/json; charset=utf-8';
    case '.png':
      return 'image/png';
    case '.svg':
      return 'image/svg+xml';
    case '.txt':
      return 'text/plain; charset=utf-8';
    default:
      return 'application/octet-stream';
  }
}

function parseArgs(args) {
  const parsed = {};

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    switch (arg) {
      case '--host':
        parsed.host = requiredValue(args, (index += 1), arg);
        break;
      case '--port':
        parsed.port = requiredValue(args, (index += 1), arg);
        break;
      case '--profile':
        parsed.profile = requiredValue(args, (index += 1), arg);
        break;
      case '--base-path':
        parsed.basePath = requiredValue(args, (index += 1), arg);
        break;
      case '--forager-bin':
        parsed.foragerBin = requiredValue(args, (index += 1), arg);
        break;
      case '--allow-remote':
        parsed.allowRemote = true;
        break;
      case '--help':
      case '-h':
        printHelp();
        process.exit(0);
      default:
        fail(`unknown option: ${arg}`);
    }
  }

  return parsed;
}

function requiredValue(args, index, flag) {
  const value = args[index];
  if (!value || value.startsWith('--')) {
    fail(`${flag} requires a value`);
  }
  return value;
}

function normalizeBasePath(value) {
  const normalized = `/${String(value).replace(/^\/+|\/+$/g, '')}`;
  return normalized === '/' ? '' : normalized;
}

function normalizePathname(value) {
  const pathname = value.replace(/\/+$/g, '');
  return pathname || '/';
}

function safeName(value) {
  return value.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96) || 'action-envelope';
}

function isLocalHost(value) {
  return ['127.0.0.1', 'localhost', '::1'].includes(value);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function printHelp() {
  process.stdout.write(`Usage: npm run serve:actions -- [options]

Serves website/dist and exposes the first local-only Web UI action endpoint.

Options:
  --host <host>          Bind host. Defaults to 127.0.0.1.
  --port <port>          Bind port. Defaults to FORAGER_ACTION_BRIDGE_PORT or 4387.
  --profile <name>       Forager profile. Defaults to FORAGER_PROFILE or default.
  --base-path <path>     Website base path. Defaults to /forager-cli.
  --forager-bin <path>   Forager binary to run. Defaults to FORAGER_BIN, target/debug/forager, then cargo run.
  --allow-remote         Allow binding a non-local host. Use only behind trusted local access controls.
`);
}
