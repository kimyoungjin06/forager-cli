#!/usr/bin/env node
import net from 'node:net';
import process from 'node:process';
import { localBin, runCommand, withBuildLock } from './build-lock.mjs';

const playwrightArgs = process.argv.slice(2);

try {
  await withBuildLock(async () => {
    const previewPort = process.env.FORAGER_PLAYWRIGHT_PORT ?? String(await findOpenPort());
    await runCommand(localBin('astro'), ['build']);
    await runCommand(localBin('playwright'), ['test', ...playwrightArgs], {
      env: {
        ...process.env,
        FORAGER_PLAYWRIGHT_PORT: previewPort,
      },
    });
  });
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}

function findOpenPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') {
        server.close(() => reject(new Error('Could not allocate a local preview port.')));
        return;
      }
      const { port } = address;
      server.close(() => resolve(port));
    });
  });
}
