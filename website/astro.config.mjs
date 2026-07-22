import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  site: 'https://kimyoungjin06.github.io',
  base: '/forager-cli',
  // Bind dev and preview to all interfaces so the control-plane surfaces are
  // reachable from another machine on the network, not just localhost. Override
  // the port with `--port` or the HOST/PORT the process is launched with.
  server: { host: true, port: 4321 },
  preview: { host: true, port: 4321 },
  vite: {
    plugins: [tailwindcss()],
  },
  integrations: [
    sitemap({
      changefreq: 'weekly',
      priority: 0.7,
    }),
  ],
});
