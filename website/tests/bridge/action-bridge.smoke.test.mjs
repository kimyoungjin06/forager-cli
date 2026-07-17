// End-to-end smoke test for the local action bridge.
//
// Unlike the Playwright tests (which mock the bridge responses), this drives
// the real serve-local-actions.mjs process against a seeded temporary profile
// and the built forager binary: it exercises the HTTP layer, the forager
// subprocess calls, envelope reconstruction from a fresh surface, and receipt
// recording. Skips automatically if the forager binary or dist build is absent.

import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const repoRoot = path.resolve(siteRoot, '..');
const foragerBin = path.join(repoRoot, 'target', 'debug', 'forager');
const bridgeScript = path.join(siteRoot, 'scripts', 'serve-local-actions.mjs');
const publicSurface = path.join(siteRoot, 'public', 'workstation-surface.json');
const BASE = '/forager-cli';

const prereqsReady = existsSync(foragerBin) && existsSync(path.join(siteRoot, 'dist', 'index.html'));

const DECISION = JSON.stringify({
  schema: 'decision_record.v1',
  decision_id: 'decision-user',
  project_key: 'project',
  request_id: 'request',
  task_id: 'approval-task',
  raised_by: 'agent',
  source_surface: 'offdesk.council',
  materiality: 'high',
  status: 'user_pending',
  created_at: '2026-07-16T00:00:00Z',
  updated_at: '2026-07-16T00:00:00Z',
  decision_request: {
    kind: 'council_escalation',
    summary: 'Council recommends revising the next episode.',
    decision_needed: 'Choose whether to continue, revise, block, or stop.',
    why_now: ['Council did not return continue.'],
    current_scope: 'Next episode only.',
    non_authorized_scope: ['provider retargeting'],
    options: [
      { id: 'revise', label: 'Revise', description: 'Ask the agent to revise the plan.' },
      { id: 'block', label: 'Block', description: 'Keep the run blocked.' },
    ],
  },
  route: {
    materiality: 'high',
    target: 'user',
    reason: 'The next episode direction changes.',
    default_if_no_reply: 'defer',
  },
  approval_brief: {
    schema: 'approval_brief.v1',
    recommendation: 'revise',
    subject: 'council continuation decision',
    summary_lines: ['Council recommends revising before continuing.'],
    scope: 'Only approves the next episode direction.',
    question: 'How should the run proceed?',
  },
});

let home;
let env;
let origin;
let bridgePort;
let server;
let savedSurface = null;

function findOpenPort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

// fetch() forbids overriding the Host header, so use a raw request to prove the
// bridge rejects a non-local Host (the DNS-rebinding guard).
function rawGet(port, pathname, headers) {
  return new Promise((resolve, reject) => {
    const request = http.request(
      { host: '127.0.0.1', port, path: pathname, method: 'GET', headers },
      (response) => {
        let data = '';
        response.on('data', (chunk) => {
          data += chunk;
        });
        response.on('end', () => resolve({ status: response.statusCode, body: data }));
      },
    );
    request.on('error', reject);
    request.end();
  });
}

async function waitForBridge(url, attempts = 100) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return true;
      }
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

before(async () => {
  if (!prereqsReady) {
    return;
  }
  home = mkdtempSync(path.join(tmpdir(), 'forager-bridge-smoke-'));
  const profileDir = path.join(home, '.config', 'forager', 'profiles', 'default');
  mkdirSync(profileDir, { recursive: true });
  writeFileSync(path.join(profileDir, 'offdesk_decisions.jsonl'), `${DECISION}\n`);

  // The bridge rewrites public/workstation-surface.json (a gitignored build
  // artifact); preserve any local copy so a dev workspace is untouched.
  if (existsSync(publicSurface)) {
    savedSurface = readFileSync(publicSurface);
  }

  env = { ...process.env, HOME: home, XDG_CONFIG_HOME: path.join(home, '.config') };
  delete env.FORAGER_PROFILE;
  delete env.AGENT_OF_EMPIRES_PROFILE;

  bridgePort = await findOpenPort();
  origin = `http://127.0.0.1:${bridgePort}`;
  server = spawn('node', [bridgeScript, '--port', String(bridgePort), '--forager-bin', foragerBin], {
    env,
    cwd: repoRoot,
    stdio: 'ignore',
  });
  const ready = await waitForBridge(`${origin}${BASE}/api/ondesk/bridge-status`);
  assert.ok(ready, 'bridge did not become ready');
});

after(() => {
  server?.kill();
  if (savedSurface !== null) {
    writeFileSync(publicSurface, savedSurface);
  }
  if (home) {
    rmSync(home, { recursive: true, force: true });
  }
});

test('bridge-status reports a ready local bridge', async (t) => {
  if (!prereqsReady) {
    t.skip('forager binary or website/dist build is missing');
    return;
  }
  const response = await fetch(`${origin}${BASE}/api/ondesk/bridge-status`);
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.ok, true);
  assert.equal(body.schema, 'local_action_bridge_status.v1');
  assert.equal(body.local_only, true);
});

test('a compact action request records a receipt from the seeded profile', async (t) => {
  if (!prereqsReady) {
    t.skip('forager binary or website/dist build is missing');
    return;
  }
  // Read the fresh surface directly to learn the current compact request the
  // browser would send (action_id, decision_id, observed_hash).
  const surfaceRun = spawnSync(
    foragerBin,
    ['--profile', 'default', 'ondesk', 'workstation-surface', '--json'],
    { env, cwd: repoRoot, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 },
  );
  assert.equal(surfaceRun.status, 0, surfaceRun.stderr);
  const surface = JSON.parse(surfaceRun.stdout);
  const decision = surface.decisions.find((item) => item.decision_id === 'decision-user');
  assert.ok(decision, 'seeded decision is present in the surface');
  const envelope = decision.action_envelopes[0];
  assert.ok(envelope, 'decision exposes an action envelope');

  const response = await fetch(`${origin}${BASE}/api/ondesk/action-envelope`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      action_request: {
        schema: 'local_action_bridge_request.v1',
        action_id: envelope.action_id,
        decision_id: decision.decision_id,
        observed_hash: envelope.observed_hash,
      },
    }),
  });
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  assert.equal(body.ok, true);
  assert.equal(body.receipt.result_status, 'validated_preview');
  assert.equal(body.receipt.stale, false);

  // The receipt is durably recorded in the seeded profile.
  const receiptsFile = path.join(
    home,
    '.config',
    'forager',
    'profiles',
    'default',
    'action_envelope_receipts.jsonl',
  );
  assert.ok(existsSync(receiptsFile), 'receipt ledger was written');
  const receiptLines = readFileSync(receiptsFile, 'utf8').trim().split('\n').filter(Boolean);
  assert.equal(receiptLines.length, 1);
});

test('a full-envelope payload is rejected', async (t) => {
  if (!prereqsReady) {
    t.skip('forager binary or website/dist build is missing');
    return;
  }
  const response = await fetch(`${origin}${BASE}/api/ondesk/action-envelope`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ envelope: { schema: 'action_envelope.v1' } }),
  });
  const body = await response.json();
  assert.equal(response.status, 400);
  assert.equal(body.ok, false);
  assert.equal(body.error, 'full_envelope_payload_unsupported');
});

test('a non-local Host header is refused', async (t) => {
  if (!prereqsReady) {
    t.skip('forager binary or website/dist build is missing');
    return;
  }
  const response = await rawGet(bridgePort, `${BASE}/api/ondesk/bridge-status`, {
    Host: 'attacker.example',
  });
  assert.equal(response.status, 403);
  assert.equal(JSON.parse(response.body).error, 'host_not_allowed');
});
