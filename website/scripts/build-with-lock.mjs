#!/usr/bin/env node
import process from 'node:process';
import { runCommand, withBuildLock } from './build-lock.mjs';

const separatorIndex = process.argv.indexOf('--');
const commandArgs =
  separatorIndex >= 0 ? process.argv.slice(separatorIndex + 1) : process.argv.slice(2);

if (commandArgs.length === 0) {
  process.stderr.write('Usage: node scripts/build-with-lock.mjs -- <command> [args...]\n');
  process.exit(2);
}

try {
  await withBuildLock(async () => {
    await runCommand(commandArgs[0], commandArgs.slice(1));
  });
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}
