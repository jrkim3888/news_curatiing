import { defineConfig } from 'astro/config';

const site = process.env.SITE_URL || 'https://ai-brief.pages.dev';
if (!/^https?:\/\//.test(site))
  throw new Error('SITE_URL must be an absolute HTTP(S) URL');
export default defineConfig({
  site,
  output: 'static',
  trailingSlash: 'always',
});
