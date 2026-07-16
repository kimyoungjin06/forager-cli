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
const ACTION_REQUEST_SCHEMA = 'local_action_bridge_request.v1';
const BRIDGE_STATUS_SCHEMA = 'local_action_bridge_status.v1';

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
  process.stdout.write('Allowed APIs: GET /api/ondesk/bridge-status, POST /api/ondesk/action-envelope\n');
});

async function handleRequest(request, response) {
  const hostCheck = validateHostHeader(request);
  if (!hostCheck.ok) {
    json(response, 403, hostCheck);
    return;
  }

  const url = new URL(request.url ?? '/', `http://${host}:${port}`);
  const pathname = normalizePathname(url.pathname);
  const actionEnvelopeApi = `${basePath}/api/ondesk/action-envelope`;
  const bridgeStatusApi = `${basePath}/api/ondesk/bridge-status`;

  if (request.method === 'GET' && (pathname === bridgeStatusApi || pathname === '/api/ondesk/bridge-status')) {
    json(response, 200, bridgeStatusPayload(request));
    return;
  }

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
  const boundary = validateRequestBoundary(request);
  if (!boundary.ok) {
    json(response, 403, boundary);
    return;
  }

  let body;
  try {
    body = await readJsonBody(request);
  } catch (error) {
    json(response, 400, {
      ok: false,
      error: 'invalid_json_body',
      message: error.message,
    });
    return;
  }

  const actionRequest = actionRequestFromBody(body);
  if (!actionRequest.ok) {
    json(response, 400, actionRequest);
    return;
  }

  const currentSurface = exportWorkstationSurface();
  if (!currentSurface.ok) {
    json(response, 503, {
      ok: false,
      error: 'surface_unavailable',
      message: currentSurface.message,
    });
    return;
  }

  const envelopeLookup = findCurrentActionEnvelope(currentSurface.surface, actionRequest.request);
  if (!envelopeLookup.ok) {
    json(response, envelopeLookup.status, envelopeLookup);
    return;
  }

  const envelope = envelopeLookup.envelope;
  const envelopePath = writeEnvelope(envelope);
  const result = runForager(['--profile', profile, 'ondesk', 'action-envelope', '--envelope', envelopePath, '--json']);
  if (result.status !== 0) {
    json(response, 400, {
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
    schema: 'local_action_bridge_response.v1',
    action_id: envelope.action_id,
    decision_id: envelope.target_ref?.decision_id ?? actionRequest.request.decision_id,
    receipt: output.receipt,
    receipt_appended: output.receipt_appended,
    dry_run: output.dry_run,
    surface_refreshed: surfaceResult.ok,
    surface_error: surfaceResult.ok ? null : surfaceResult.message,
  });
}

function bridgeStatusPayload(request) {
  return {
    ok: true,
    schema: BRIDGE_STATUS_SCHEMA,
    profile,
    base_path: basePath,
    local_only: isLocalHost(host) && !options.allowRemote,
    allow_remote: Boolean(options.allowRemote),
    origin: request.headers.origin ?? null,
    endpoints: {
      action_envelope: `${basePath}/api/ondesk/action-envelope`,
      bridge_status: `${basePath}/api/ondesk/bridge-status`,
    },
    contract: {
      action_envelope_request_schema: ACTION_REQUEST_SCHEMA,
      accepted_fields: ['action_id', 'decision_id', 'observed_hash'],
    },
  };
}

function hostHeaderName(rawHost) {
  const value = String(rawHost ?? '').trim().toLowerCase();
  if (!value) {
    return '';
  }
  if (value.startsWith('[')) {
    const closing = value.indexOf(']');
    return closing === -1 ? value : value.slice(1, closing);
  }
  const colon = value.lastIndexOf(':');
  return colon === -1 ? value : value.slice(0, colon);
}

function validateHostHeader(request) {
  // The Host header must name this bridge, not an attacker-controlled DNS
  // name rebound to 127.0.0.1. Without this, the Origin check below can be
  // satisfied by a rebinding page whose Origin and Host headers match.
  const hostname = hostHeaderName(request.headers.host);
  const allowed = new Set(['127.0.0.1', 'localhost', '::1', host.toLowerCase()]);
  if (allowed.has(hostname)) {
    return { ok: true };
  }
  return {
    ok: false,
    error: 'host_not_allowed',
    message: `Refusing request for host ${hostname || '(missing)'}; open the bridge via http://${host}:${port}${basePath}/.`,
  };
}

function validateRequestBoundary(request) {
  const secFetchSite = String(request.headers['sec-fetch-site'] ?? '');
  if (secFetchSite && !['same-origin', 'none'].includes(secFetchSite)) {
    return {
      ok: false,
      error: 'cross_origin_request',
      message: `Refusing ${secFetchSite} browser request; open the Web UI from the local bridge origin.`,
    };
  }

  const origin = request.headers.origin;
  if (!origin) {
    return { ok: true };
  }

  const hostHeader = request.headers.host ?? `${host}:${port}`;
  const expectedOrigin = `http://${hostHeader}`;
  if (origin !== expectedOrigin) {
    return {
      ok: false,
      error: 'origin_mismatch',
      message: `Refusing request from ${origin}; expected ${expectedOrigin}.`,
    };
  }

  return { ok: true };
}

function actionRequestFromBody(body) {
  if (!isPlainObject(body)) {
    return {
      ok: false,
      error: 'invalid_request',
      message: 'Expected a JSON object.',
    };
  }

  if (isPlainObject(body.envelope)) {
    return {
      ok: false,
      error: 'full_envelope_payload_unsupported',
      message: 'Send action_id, decision_id, and observed_hash; the bridge rebuilds the envelope from the current workstation surface.',
    };
  }

  const request = isPlainObject(body.action_request) ? body.action_request : body;
  const schema = String(request.schema ?? '');
  if (schema !== ACTION_REQUEST_SCHEMA) {
    return {
      ok: false,
      error: 'unsupported_schema',
      message: `Expected ${ACTION_REQUEST_SCHEMA}, got ${schema || 'missing'}.`,
    };
  }

  const actionId = String(request.action_id ?? '').trim();
  const decisionId = String(request.decision_id ?? '').trim();
  const observedHash = String(request.observed_hash ?? '').trim();
  const missing = [
    ['action_id', actionId],
    ['decision_id', decisionId],
    ['observed_hash', observedHash],
  ]
    .filter(([, value]) => !value)
    .map(([field]) => field);

  if (missing.length) {
    return {
      ok: false,
      error: 'missing_action_request_fields',
      message: `Missing required field(s): ${missing.join(', ')}.`,
    };
  }

  return {
    ok: true,
    request: {
      schema,
      action_id: actionId,
      decision_id: decisionId,
      observed_hash: observedHash,
    },
  };
}

function findCurrentActionEnvelope(surface, request) {
  const decisions = surface?.decision_inbox?.items;
  if (!Array.isArray(decisions)) {
    return {
      ok: false,
      status: 503,
      error: 'decision_inbox_unavailable',
      message: 'Current workstation surface does not include decision inbox items.',
    };
  }

  const decision = decisions.find((item) => item?.decision_id === request.decision_id);
  if (!decision) {
    return {
      ok: false,
      status: 409,
      error: 'decision_not_current',
      message: `Decision ${request.decision_id} is not visible in the current workstation surface.`,
    };
  }

  const envelopes = Array.isArray(decision.action_envelopes) ? decision.action_envelopes : [];
  const envelope = envelopes.find((item) => item?.action_id === request.action_id);
  if (!envelope) {
    return {
      ok: false,
      status: 409,
      error: 'action_not_current',
      message: `Action ${request.action_id} is not visible for decision ${request.decision_id}.`,
    };
  }

  if (envelope.schema !== 'action_envelope.v1') {
    return {
      ok: false,
      status: 400,
      error: 'unsupported_envelope_schema',
      message: `Expected action_envelope.v1, got ${envelope.schema ?? 'missing'}.`,
    };
  }

  if (envelope.observed_hash !== request.observed_hash) {
    return {
      ok: false,
      status: 409,
      error: 'observed_hash_changed',
      message: 'The decision changed since this action was rendered. Refresh the workstation surface before recording a receipt.',
    };
  }

  return {
    ok: true,
    envelope,
  };
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
