import type { APIRoute } from 'astro';

import { sitePath } from '../lib/paths';

export const GET: APIRoute = ({ site }) => {
  const siteUrl = site?.toString().replace(/\/$/, '') ?? '';
  const publicSiteUrl = `${siteUrl}${sitePath('/')}`.replace(/\/$/, '');

  const robotsTxt = `# robots.txt
User-agent: *
Allow: /

# Sitemap location
Sitemap: ${publicSiteUrl}/sitemap-index.xml
`;

  return new Response(robotsTxt, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
};
