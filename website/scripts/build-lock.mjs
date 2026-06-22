#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

export const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const lockDir = path.join(siteRoot, 'node_modules', '.cache', 'forager-website-build.lock');
const lockTimeoutMs = Number(process.env.FORAGER_BUILD_LOCK_TIMEOUT_MS ?? 180_000);
const pollMs = 500;

export async function withBuildLock(work) {
  await acquireBuildLock();
  let released = false;

  const release = async () => {
    if (released) {
      return;
    }
    released = true;
    await rm(lockDir, { recursive: true, force: true });
  };

  const signalHandler = async () => {
    await release();
    process.exit(130);
  };

  process.once('SIGINT', signalHandler);
  process.once('SIGTERM', signalHandler);

  try {
    return await work();
  } finally {
    process.removeListener('SIGINT', signalHandler);
    process.removeListener('SIGTERM', signalHandler);
    await release();
  }
}

export function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? siteRoot,
      env: options.env ?? process.env,
      shell: false,
      stdio: 'inherit',
    });

    child.on('error', reject);
    child.on('exit', (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(
        new Error(
          signal
            ? `${command} ${args.join(' ')} exited with signal ${signal}`
            : `${command} ${args.join(' ')} exited with code ${code}`,
        ),
      );
    });
  });
}

export function localBin(name) {
  return path.join(
    siteRoot,
    'node_modules',
    '.bin',
    process.platform === 'win32' ? `${name}.cmd` : name,
  );
}

async function acquireBuildLock() {
  const startedAt = Date.now();
  await mkdir(path.dirname(lockDir), { recursive: true });

  while (true) {
    try {
      await mkdir(lockDir);
      await writeFile(
        path.join(lockDir, 'owner.json'),
        `${JSON.stringify(
          {
            pid: process.pid,
            started_at: new Date().toISOString(),
            command: process.argv.join(' '),
          },
          null,
          2,
        )}\n`,
      );
      return;
    } catch (error) {
      if (error?.code !== 'EEXIST') {
        throw error;
      }
    }

    const owner = await readLockOwner();
    if (owner && !isProcessAlive(owner.pid)) {
      await rm(lockDir, { recursive: true, force: true });
      continue;
    }
    if (Date.now() - startedAt > lockTimeoutMs) {
      const detail = owner
        ? `held by pid ${owner.pid} since ${owner.started_at}`
        : 'held by an unknown process';
      throw new Error(`Timed out waiting for website build lock (${detail}).`);
    }

    await sleep(pollMs);
  }
}

async function readLockOwner() {
  try {
    return JSON.parse(await readFile(path.join(lockDir, 'owner.json'), 'utf8'));
  } catch {
    return null;
  }
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
